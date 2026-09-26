"""
alu.py - the RISC201 ALU (Member D)

Every add, sub, mul, div, mod, compare, logic and shift in the simulators
goes through ALU.compute() or ALU.compare().

You can choose the algorithm used for the three "hard" operations
(Sarangi Chapter 7):

  adder      : 'ripple'  ripple-carry adder, carry walks through all 32 bits
               'cla'     carry-lookahead, 4-bit blocks (carry jumps a block
                         at a time)
  multiplier : 'shiftadd' add the shifted multiplicand for every 1 bit
               'booth'    Booth's algorithm: add/sub only where the
                          multiplier bits CHANGE (fewer operations on runs of 1s)
  divider    : 'restoring'     subtract; if the result went negative, add back
               'nonrestoring'  never add back; the next step adds instead

All versions give the same answers. They differ in how much work they do,
which the ALU counts in self.stats (used by bench.py).

Delay model for the adders (in gate delays, simple and stated openly):
  ripple : 2 per bit                 -> 2 * 32 = 64
  cla    : 1 (make g,p) + 2 per 4-bit block + 1 (sum xor) -> 1 + 16 + 1 = 18
"""
from isa import MASK32, to_signed
from exceptions import DivideByZero


class ALU:
    ADDERS = ('ripple', 'cla')
    MULTIPLIERS = ('shiftadd', 'booth')
    DIVIDERS = ('restoring', 'nonrestoring')

    def __init__(self, adder='cla', multiplier='booth', divider='nonrestoring'):
        assert adder in self.ADDERS and multiplier in self.MULTIPLIERS \
            and divider in self.DIVIDERS
        self.adder = adder
        self.multiplier = multiplier
        self.divider = divider
        self.reset_stats()

    def reset_stats(self):
        self.stats = {
            'adds': 0, 'adder_delay': 0,      # every trip through the adder
            'muls': 0, 'mul_addsub': 0,       # add/sub steps inside multiplies
            'divs': 0, 'div_addsub': 0,       # add/sub steps inside divides
        }

    # ------------------------------------------------------------------
    # Adders
    # ------------------------------------------------------------------
    def _ripple(self, a, b, cin):
        total, carry = 0, cin
        for i in range(32):
            x, y = (a >> i) & 1, (b >> i) & 1
            total |= (x ^ y ^ carry) << i
            carry = (x & y) | (carry & (x ^ y))     # full adder carry out
        return total, 64

    def _cla(self, a, b, cin):
        total, carry = 0, cin
        for block in range(8):                       # 8 blocks of 4 bits
            base = 4 * block
            g = [((a >> (base + i)) & (b >> (base + i))) & 1 for i in range(4)]
            p = [((a >> (base + i)) ^ (b >> (base + i))) & 1 for i in range(4)]
            # c[k] = g[k-1] | p[k-1]g[k-2] | ... | p[k-1]..p[0]c[0]
            # Every carry in the block comes straight from g, p and c[0],
            # it does not wait for the carry of the bit before it.
            c = [carry]
            for k in range(1, 5):
                ck = 0
                for j in range(k):
                    term = g[j]
                    for m in range(j + 1, k):
                        term &= p[m]
                    ck |= term
                all_p = 1
                for m in range(k):
                    all_p &= p[m]
                c.append(ck | (all_p & carry))
            for i in range(4):
                total |= (p[i] ^ c[i]) << (base + i)
            carry = c[4]
        return total, 18

    def add(self, a, b, cin=0):
        """32-bit add through the chosen adder. Returns a 32-bit value."""
        a &= MASK32
        b &= MASK32
        result, delay = (self._ripple if self.adder == 'ripple' else self._cla)(a, b, cin)
        self.stats['adds'] += 1
        self.stats['adder_delay'] += delay
        return result

    def sub(self, a, b):
        return self.add(a, ~b & MASK32, 1)          # a - b = a + (~b) + 1

    # ------------------------------------------------------------------
    # Multipliers (result = low 32 bits, same for signed and unsigned)
    # ------------------------------------------------------------------
    def _shift_add(self, a, b):
        product, steps = 0, 0
        for i in range(32):
            if (b >> i) & 1:
                product = self.add(product, a << i)
                steps += 1
        return product, steps

    def _booth(self, a, b):
        product, steps, previous = 0, 0, 0
        for i in range(32):
            bit = (b >> i) & 1
            if (bit, previous) == (1, 0):           # start of a run of 1s
                product = self.sub(product, a << i)
                steps += 1
            elif (bit, previous) == (0, 1):         # end of a run of 1s
                product = self.add(product, a << i)
                steps += 1
            previous = bit
        return product, steps

    def mul(self, a, b):
        fn = self._booth if self.multiplier == 'booth' else self._shift_add
        product, steps = fn(a & MASK32, b & MASK32)
        self.stats['muls'] += 1
        self.stats['mul_addsub'] += steps
        return product & MASK32

    # ------------------------------------------------------------------
    # Dividers (work on magnitudes, sign fixed at the end)
    # ------------------------------------------------------------------
    def _restoring(self, dividend, divisor):
        remainder, quotient, steps = 0, dividend, 0
        for _ in range(32):
            remainder = (remainder << 1) | ((quotient >> 31) & 1)
            quotient = (quotient << 1) & MASK32
            remainder -= divisor
            steps += 1
            if remainder < 0:
                remainder += divisor                 # restore
                steps += 1
            else:
                quotient |= 1
        return quotient, remainder, steps

    def _nonrestoring(self, dividend, divisor):
        remainder, quotient, steps = 0, dividend, 0
        for _ in range(32):
            remainder = (remainder << 1) | ((quotient >> 31) & 1)
            quotient = (quotient << 1) & MASK32
            if remainder >= 0:
                remainder -= divisor
            else:
                remainder += divisor                 # no restore step
            steps += 1
            if remainder >= 0:
                quotient |= 1
        if remainder < 0:                            # one final correction
            remainder += divisor
            steps += 1
        return quotient, remainder, steps

    def divmod(self, a, b, pc=None):
        """Signed divide. Quotient rounds towards zero; remainder takes
        the sign of the dividend (same as C / Java)."""
        a, b = to_signed(a), to_signed(b)
        if b == 0:
            raise DivideByZero("division by zero", pc)
        fn = self._restoring if self.divider == 'restoring' else self._nonrestoring
        q, r, steps = fn(abs(a), abs(b))
        self.stats['divs'] += 1
        self.stats['div_addsub'] += steps
        if (a < 0) != (b < 0):
            q = -q
        if a < 0:
            r = -r
        return q & MASK32, r & MASK32

    # ------------------------------------------------------------------
    # The single entry point used by the processors
    # ------------------------------------------------------------------
    def compute(self, op, a, b, pc=None):
        a &= MASK32
        b &= MASK32
        if op == 'add':
            return self.add(a, b)
        if op == 'sub':
            return self.sub(a, b)
        if op == 'mul':
            return self.mul(a, b)
        if op == 'div':
            return self.divmod(a, b, pc)[0]
        if op == 'mod':
            return self.divmod(a, b, pc)[1]
        if op == 'and':
            return a & b
        if op == 'or':
            return a | b
        if op == 'not':
            return ~b & MASK32
        if op == 'mov':
            return b
        if op == 'lsl':
            return (a << b) & MASK32 if b < 32 else 0
        if op == 'lsr':
            return a >> b if b < 32 else 0
        if op == 'asr':
            return (to_signed(a) >> min(b, 31)) & MASK32
        raise ValueError(f"ALU has no operation '{op}'")

    def compare(self, a, b):
        """cmp: returns (E, GT). Signed compare, done with the subtractor."""
        diff = self.sub(a, b)
        equal = int(diff == 0)
        greater = int(to_signed(a) > to_signed(b))
        return equal, greater
