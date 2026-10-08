"""
alu.py - the RISC201 ALU (Member D)

Every add, sub, mul, div, mod, compare, logic and shift in the simulators
goes through ALU.compute() or ALU.compare().

All the hardware algorithms here follow Sarangi, "Computer Organisation
and Architecture", Chapter 8 (Computer Arithmetic). Section numbers are
given so each one can be checked against the book.

  adder      'ripple'   8.1.3 Ripple Carry Adder     O(n)
                        half adder for bit 1, then 31 full adders; the
                        carry walks all the way from the LSB to the MSB.
             'cselect'  8.1.4 Carry Select Adder     O(sqrt(n))
                        blocks of k bits; each block is added TWICE in
                        parallel, once assuming carry-in 0 and once
                        assuming 1, and a multiplexer picks the right
                        answer when the real carry arrives. Optimal
                        k = sqrt(n) (book eq. 8.1), so k = 6 for n = 32.
             'cla'      8.1.5 Carry Lookahead Adder  O(log(n))
                        a TREE of (G,P) blocks. Stage I combines the
                        generate / propagate functions of two child
                        blocks with the book's rule (eq. 8.9):
                            G(1,n) = G(m+1,n) + P(m+1,n).G(1,m)
                            P(1,n) = P(m+1,n).P(1,m)
                        Stage II pushes carries back down the tree, and
                        2-bit ripple adders at the leaves make the sum.

  multiplier 'iterative' 8.2.2 Iterative Multiplier   O(n log n)
                         33-bit U, 32-bit V, multiplicand in N.
                         Multiplier sits in V. Each step: if LSB(V) = 1
                         then U += N (but U -= N on the LAST step, which
                         is how a negative multiplier is handled), then
                         arithmetic right shift the pair UV.
             'booth'     8.2.3 Booth Multiplier
                         Same U/V datapath, but it looks at the bit PAIR
                         (current, previous) of the multiplier:
                            1,0 -> U = U - N   (a run of 1s starts)
                            0,1 -> U = U + N   (a run of 1s ends)
                            0,0 / 1,1 -> nothing
                         A run of j-i+1 additions becomes one add and
                         one subtract (book eq. 8.10), so it is much
                         faster on runs of 1s. Same complexity class.

  divider    'restoring'    8.3.2  U = U - D; if that went negative, add
                            D back ("restore"). Up to TWO add/subtracts
                            per step.
             'nonrestoring' 8.3.3  if U >= 0 then U = U - D else
                            U = U + D. Exactly ONE add/subtract per
                            step, plus one final correction if U < 0.

The multiplier and divider share one datapath shape, straight from the
book: a 33-bit U (the extra bit stops overflow when N or D is added to
it, book Important Points 9-12) joined to a 32-bit V, shifted as one
65-bit register. Multiplication shifts UV RIGHT, division shifts it LEFT.

All versions give the same answers. They differ in how much work they
do, which the ALU counts in self.stats (used by bench.py).

Delay model, in gate levels. Stated openly; these are our assumptions,
not the book's numbers, but they follow the book's complexity classes:
  ripple   2 per bit                            = 2*32      = 64
  cselect  2k to add a block, then 1 mux per
           block boundary (5 boundaries)        = 2*6 + 5   = 17
  cla      1 (make g,p) + 2 per tree level, up
           the tree and back down (4 levels),
           + 1 (final xor)                      = 1+2*4*2+1 = 18
Run `python alu.py` to print these and to check every algorithm against
Python's own arithmetic.

Note what the numbers say: at n = 32 the carry select adder (17) is
already as good as the lookahead adder (18) -- sqrt(32) = 5.7 and
log2(32) = 5 are close. The lookahead adder pulls ahead as n grows,
because log(n) beats sqrt(n); at n = 1024 it would be 1+2*9*2+1 = 38
against 2*32 + 31 = 95.
"""
from isa import MASK32, to_signed
from exceptions import DivideByZero

MASK33 = (1 << 33) - 1


class ALU:
    ADDERS = ('ripple', 'cselect', 'cla')
    MULTIPLIERS = ('iterative', 'booth')
    DIVIDERS = ('restoring', 'nonrestoring')

    CSELECT_BLOCK = 6        # k = sqrt(32), rounded up (book eq. 8.1)
    CLA_LEAF = 2             # 2-bit ripple adders at the leaves (book fig. 8.9)

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

    def mul(self, a, b):
        """Low 32 bits of a * b. The book's multipliers produce a 64-bit
        product in UV; RISC201 keeps only the low half, which is V."""
        n = to_signed(a)                             # multiplicand, sign extended
        fn = self._booth if self.multiplier == 'booth' else self._iterative
        _, v, steps = fn(n, b)
        self.stats['muls'] += 1
        self.stats['mul_addsub'] += steps
        return v & MASK32

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
        alu.add(0, 0)
        print(f"{adder:8s} delay {alu.stats['adder_delay']} gate levels")