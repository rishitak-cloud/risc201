from isa import MASK32, to_signed
from exceptions import DivideByZero, ArithmeticOverflow
from fpu import FPU

MASK33 = (1 << 33) - 1


class ALU:
    ADDERS = ('ripple', 'cselect', 'cla')
    MULTIPLIERS = ('iterative', 'booth', 'booth4')
    DIVIDERS = ('restoring', 'nonrestoring')

    CSELECT_BLOCK = 6        # k = sqrt(32), rounded up (book eq. 8.1)
    CLA_LEAF = 2             # 2-bit ripple adders at the leaves (book fig. 8.9)

    def __init__(self, adder='cla', multiplier='booth', divider='nonrestoring',
                 trap_overflow=False, rounding='nearest', fdivider='restoring'):
        assert adder in self.ADDERS and multiplier in self.MULTIPLIERS \
            and divider in self.DIVIDERS
        self.adder = adder
        self.multiplier = multiplier
        self.divider = divider
        self.trap_overflow = trap_overflow
        self.flags = None            # Machine connects its flags here (for V)
        self.fpu = FPU(self, rounding, fdivider)
        self.reset_stats()

    def reset_stats(self):
        self.stats = {
            'adds': 0, 'adder_delay': 0,      # every trip through the adder
            'muls': 0, 'mul_addsub': 0,       # add/sub steps inside multiplies
            'divs': 0, 'div_addsub': 0,       # add/sub steps inside divides
            'overflows': 0,
        }
        if hasattr(self, 'fpu'):
            self.fpu.reset_stats()

    # ==================================================================
    # 8.1  ADDERS
    # ==================================================================
    @staticmethod
    def _full_adder(a, b, cin):
        """Book section 8.1.2:  s = a ^ b ^ cin,  cout = ab + a.cin + b.cin"""
        s = a ^ b ^ cin
        cout = (a & b) | (a & cin) | (b & cin)
        return s, cout

    def _ripple_block(self, a, b, cin, lo, hi):
        """Ripple carry over bits [lo, hi). Returns (sum bits, carry out)."""
        total, carry = 0, cin
        for i in range(lo, hi):
            s, carry = self._full_adder((a >> i) & 1, (b >> i) & 1, carry)
            total |= s << i
        return total, carry

    def _ripple(self, a, b, cin):
        """8.1.3 Ripple Carry Adder. The carry crosses all 32 bit positions."""
        total, _ = self._ripple_block(a, b, cin, 0, 32)
        return total, 2 * 32

    def _cselect(self, a, b, cin):
        """8.1.4 Carry Select Adder.

        Each block is added twice at the same time, once for carry-in 0 and
        once for carry-in 1. When the real carry-in finally arrives, a
        multiplexer selects one of the two precomputed answers, so the block
        does NOT have to be added again. The carry therefore crosses one
        multiplexer per block instead of k full adders.
        """
        k = self.CSELECT_BLOCK
        total, carry = 0, cin
        boundaries = 0
        for lo in range(0, 32, k):
            hi = min(lo + k, 32)
            sum0, carry0 = self._ripple_block(a, b, 0, lo, hi)   # assume cin = 0
            sum1, carry1 = self._ripple_block(a, b, 1, lo, hi)   # assume cin = 1
            if lo == 0:                                          # first block: real cin
                chosen_sum, carry = (sum1, carry1) if cin else (sum0, carry0)
            else:                                                # later blocks: the mux
                chosen_sum, carry = (sum1, carry1) if carry else (sum0, carry0)
                boundaries += 1
            total |= chosen_sum
        return total, 2 * k + boundaries

    # ---- carry lookahead: the (G,P) tree of section 8.1.5 ----
    @staticmethod
    def _combine(upper, lower):
        """Book eq. 8.9. upper = (G,P) of the higher half, lower = (G,P) of
        the lower half.  G = Gu + Pu.Gl    P = Pu.Pl"""
        gu, pu = upper
        gl, pl = lower
        return (gu | (pu & gl), pu & pl)

    def _cla(self, a, b, cin):
        """8.1.5 Carry Lookahead Adder, O(log n).

        Stage I   build a tree of (G,P) blocks from the bit-level
                  g = a.b and p = a ^ b, each node combining its two children.
        Stage II  walk the tree back down, handing each node its carry-in;
                  a node's left child gets the node's carry-in, and its right
                  child gets the carry coming OUT of the left child,
                  Cout = G + P.Cin.
        Stage 0   2-bit ripple adders at the leaves turn the correct carries
                  into sum bits (book fig. 8.9).
        """
        leaf = self.CLA_LEAF
        # ---- Stage I: bottom-up ----
        levels = []
        level = []
        for lo in range(0, 32, leaf):                 # (G,P) of each leaf block
            node = None
            for i in range(lo, lo + leaf):            # combine bits inside the leaf
                g = ((a >> i) & (b >> i)) & 1
                p = ((a >> i) ^ (b >> i)) & 1
                node = (g, p) if node is None else self._combine((g, p), node)
            level.append((lo, node))
        levels.append(level)
        while len(levels[-1]) > 1:                    # each level halves
            below, level = levels[-1], []
            for i in range(0, len(below), 2):
                if i + 1 < len(below):
                    (lo, low), (_, high) = below[i], below[i + 1]
                    level.append((lo, self._combine(high, low)))
                else:
                    level.append(below[i])
            levels.append(level)

        # ---- Stage II: top-down, hand every leaf its carry-in ----
        carry_in = {levels[-1][0][0]: cin}
        for depth in range(len(levels) - 1, 0, -1):
            below = levels[depth - 1]
            for i in range(0, len(below), 2):
                lo = below[i][0]
                c = carry_in[lo]
                carry_in[lo] = c                       # left child: same carry in
                if i + 1 < len(below):
                    g, p = below[i][1]                 # Cout = G + P.Cin
                    carry_in[below[i + 1][0]] = g | (p & c)

        # ---- Stage 0: small ripple adders at the leaves ----
        total = 0
        for lo, _ in levels[0]:
            part, _ = self._ripple_block(a, b, carry_in[lo], lo, min(lo + leaf, 32))
            total |= part
        depth = len(levels) - 1
        return total, 1 + 2 * depth * 2 + 1

    def add(self, a, b, cin=0):
        """32-bit add through the chosen adder. Returns a 32-bit value."""
        a &= MASK32
        b &= MASK32
        fn = {'ripple': self._ripple, 'cselect': self._cselect, 'cla': self._cla}[self.adder]
        result, delay = fn(a, b, cin)
        self.stats['adds'] += 1
        self.stats['adder_delay'] += delay
        return result

    def sub(self, a, b):
        return self.add(a, ~b & MASK32, 1)          # a - b = a + (~b) + 1

    # ==================================================================
    # 8.2  MULTIPLIERS   (33-bit U, 32-bit V, right shifts)
    # ==================================================================
    @staticmethod
    def _shift_uv_right(u, v):
        """Arithmetic right shift of the 65-bit pair UV by one position.
        The bit falling out of U becomes the MSB of V (book section 8.2.2)."""
        return u >> 1, ((v >> 1) | ((u & 1) << 31)) & MASK32

    def _iterative(self, n, m):
        """8.2.2 Iterative Multiplier (book Algorithm 1).
        n = multiplicand (signed), m = multiplier bits. U starts at 0,
        V holds the multiplier. Returns (U, V, add/sub count)."""
        u, v, steps = 0, m & MASK32, 0
        for i in range(1, 33):
            if v & 1:
                if i < 32:
                    u = u + n                       # normal step: add
                else:
                    u = u - n                       # last step: the MSB of a
                steps += 1                          # negative multiplier
            u, v = self._shift_uv_right(u, v)
        return u, v, steps

    def _booth(self, n, m):
        """8.2.3 Booth Multiplier (book Algorithm 2).
        Looks at the bit pair (current, previous) of the multiplier:
        1,0 -> subtract;  0,1 -> add;  0,0 and 1,1 -> nothing."""
        u, v, steps, previous = 0, m & MASK32, 0, 0
        for _ in range(32):
            current = v & 1
            if (current, previous) == (1, 0):
                u = u - n                           # a run of 1s begins
                steps += 1
            elif (current, previous) == (0, 1):
                u = u + n                           # a run of 1s ends
                steps += 1
            previous = current
            u, v = self._shift_uv_right(u, v)
        return u, v, steps

    # Radix-4 Booth: (b[i+1], b[i], b[i-1]) -> multiple of N to add
    BOOTH4 = {0b000: 0, 0b001: 1, 0b010: 1, 0b011: 2,
              0b100: -2, 0b101: -1, 0b110: -1, 0b111: 0}

    def _booth4(self, n, m):
        """Radix-4 Booth (book exercise 15). Three bits per step, UV shifted
        right by TWO, 16 steps. Each step adds 0, +-N or +-2N; 2N is N
        shifted left, so it is still a single add/sub. U needs 34 bits."""
        u, v, steps, previous = 0, m & MASK32, 0, 0
        for _ in range(16):
            triple = ((v & 0b11) << 1) | previous
            multiple = self.BOOTH4[triple]
            if multiple:
                u = u + multiple * n
                steps += 1
            previous = (v >> 1) & 1
            v = ((v >> 2) | ((u & 0b11) << 30)) & MASK32      # shift UV right by 2
            u >>= 2
        return u, v, steps

    def multiplier_fn(self):
        return {'iterative': self._iterative, 'booth': self._booth,
                'booth4': self._booth4}[self.multiplier]

    def mul(self, a, b):
        """Low 32 bits of a * b. The book's multipliers produce a 64-bit
        product in UV; RISC201 keeps only the low half, which is V."""
        return self.mul_full(a, b)[0]

    def mul_full(self, a, b):
        """(low 32 bits, overflowed?). The full product is (U << 32) | V;
        it overflows if it is not just V sign-extended."""
        n = to_signed(a)                             # multiplicand, sign extended
        u, v, steps = self.multiplier_fn()(n, b)
        self.stats['muls'] += 1
        self.stats['mul_addsub'] += steps
        product = (u << 32) | v                      # signed 64-bit result
        return v & MASK32, not (-(1 << 31) <= product < (1 << 31))

    # ==================================================================
    # 8.3  DIVIDERS   (33-bit U, 32-bit V, left shifts, positive operands)
    # ==================================================================
    @staticmethod
    def _shift_uv_left(u, v):
        """Left shift of the pair UV by one position. The MSB of V moves
        into the LSB of U (book section 8.3.2)."""
        return (u << 1) | ((v >> 31) & 1), (v << 1) & MASK32

    def _restoring(self, dividend, divisor):
        """8.3.2 Restoring division (book Algorithm 3).
        Subtract; if the result went negative, add the divisor back."""
        u, v, steps = 0, dividend & MASK32, 0
        for _ in range(32):
            u, v = self._shift_uv_left(u, v)
            u = u - divisor
            steps += 1
            if u >= 0:
                q = 1
            else:
                u = u + divisor                      # restore
                steps += 1
                q = 0
            v |= q
        return v & MASK32, u, steps

    def _nonrestoring(self, dividend, divisor):
        """8.3.3 Non-restoring division (book Algorithm 4).
        One add OR one subtract per step; never adds back mid-loop.
        A single correction at the end if U is left negative."""
        u, v, steps = 0, dividend & MASK32, 0
        for _ in range(32):
            u, v = self._shift_uv_left(u, v)
            if u >= 0:
                u = u - divisor
            else:
                u = u + divisor
            steps += 1
            v |= 1 if u >= 0 else 0
        if u < 0:                                    # final correction
            u = u + divisor
            steps += 1
        return v & MASK32, u, steps

    def divmod(self, a, b, pc=None):
        """Signed divide. The book's algorithms take positive operands, so
        the magnitudes go through the hardware and the signs are fixed
        afterwards (book section 8.3.1). Quotient rounds towards zero;
        the remainder takes the sign of the dividend (same as C / Java)."""
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

    # ==================================================================
    # The single entry point used by the processors
    # ==================================================================
    # ------------------------------------------------------------------
    # Overflow detection
    # ------------------------------------------------------------------
    @staticmethod
    def add_overflows(a, b, r):
        """Same-sign operands, different-sign result."""
        return bool(((a ^ r) & (b ^ r)) >> 31 & 1)

    @staticmethod
    def sub_overflows(a, b, r):
        """Different-sign operands, and the result's sign differs from A."""
        return bool(((a ^ b) & (a ^ r)) >> 31 & 1)

    def _overflow(self, op, overflowed, pc):
        if self.flags is not None:
            self.flags.V = int(overflowed)
        if overflowed:
            self.stats['overflows'] += 1
            if self.trap_overflow:
                raise ArithmeticOverflow(f"signed overflow in {op}", pc)

    # ==================================================================
    # The single entry point used by the processors
    # ==================================================================
    def compute(self, op, a, b, pc=None):
        a &= MASK32
        b &= MASK32
        if op == 'add':
            r = self.add(a, b)
            self._overflow(op, self.add_overflows(a, b, r), pc)
            return r
        if op == 'sub':
            r = self.sub(a, b)
            self._overflow(op, self.sub_overflows(a, b, r), pc)
            return r
        if op == 'mul':
            r, ovf = self.mul_full(a, b)
            self._overflow(op, ovf, pc)
            return r
        if op == 'div':
            q = self.divmod(a, b, pc)[0]
            self._overflow(op, a == 0x80000000 and b == MASK32, pc)   # -2^31 / -1
            return q
        if op in ('fadd', 'fsub', 'fmul', 'fdiv'):
            return getattr(self.fpu, op[1:])(a, b)
        if op == 'fsqrt':
            return self.fpu.sqrt(b)
        if op == 'froot':
            return self.fpu.root(a, b)                 # B holds n
        if op == 'itof':
            return self.fpu.from_int(b)
        if op == 'ftoi':
            return self.fpu.to_int(b)
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

    def compare(self, a, b, op='cmp'):
        """cmp: returns (E, GT). Signed compare, done with the subtractor.
        fcmp: the same flags, but comparing IEEE floats (fpu.compare)."""
        if op == 'fcmp':
            return self.fpu.compare(a & MASK32, b & MASK32)
        diff = self.sub(a, b)
        equal = int(diff == 0)
        greater = int(to_signed(a) > to_signed(b))
        return equal, greater


if __name__ == '__main__':
    import random
    rng = random.Random(8)
    print("checking every algorithm against Python's own arithmetic ...")
    for adder in ALU.ADDERS:
        for mul in ALU.MULTIPLIERS:
            for div in ALU.DIVIDERS:
                alu = ALU(adder, mul, div)
                for _ in range(300):
                    x, y = rng.getrandbits(32), rng.getrandbits(32)
                    assert alu.add(x, y) == (x + y) & MASK32
                    assert alu.mul(x, y) == (x * y) & MASK32
                print(f"  ok  {adder:8s} {mul:9s} {div}")
    for adder in ALU.ADDERS:
        alu = ALU(adder=adder)
        for _ in range(2000):
            alu.add(rng.getrandbits(32), rng.getrandbits(32))
        print(f"{adder:12s} average delay {alu.stats['adder_delay'] / alu.stats['adds']:5.1f} gate levels"
              f" (random operands)")
