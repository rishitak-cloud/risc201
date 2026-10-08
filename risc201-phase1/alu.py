from isa import MASK32, to_signed
from exceptions import DivideByZero

MASK33 = (1 << 33) - 1


class ALU:
    ADDERS = ('ripple', 'cselect', 'cla')
    MULTIPLIERS = ('iterative', 'booth')
    DIVIDERS = ('restoring', 'nonrestoring')

    CSELECT_BLOCK = 6        # k = sqrt(32), rounded up
    CLA_LEAF = 2             # 2-bit ripple adders at the leaves

    def __init__(self, adder='cla', multiplier='booth', divider='nonrestoring'):
        assert adder in self.ADDERS and multiplier in self.MULTIPLIERS \
            and divider in self.DIVIDERS
        self.adder = adder
        self.multiplier = multiplier
        self.divider = divider
        self.reset_stats()

    def reset_stats(self):
        self.stats = {
            'adds': 0, 'adder_delay': 0, 
            'muls': 0, 'mul_addsub': 0, 
            'divs': 0, 'div_addsub': 0, 
        }

    # ==================================================================
    # ADDERS
    # ==================================================================
    @staticmethod
    def _full_adder(a, b, cin):
        s = a ^ b ^ cin
        cout = (a & b) | (a & cin) | (b & cin)
        return s, cout

    def _ripple_block(self, a, b, cin, lo, hi):
        total, carry = 0, cin
        for i in range(lo, hi):
            s, carry = self._full_adder((a >> i) & 1, (b >> i) & 1, carry)
            total |= s << i
        return total, carry

    def _ripple(self, a, b, cin):
        total, _ = self._ripple_block(a, b, cin, 0, 32)
        return total, 2 * 32

    def _cselect(self, a, b, cin):
        k = self.CSELECT_BLOCK
        total, carry = 0, cin
        boundaries = 0
        for lo in range(0, 32, k):
            hi = min(lo + k, 32)
            sum0, carry0 = self._ripple_block(a, b, 0, lo, hi)
            sum1, carry1 = self._ripple_block(a, b, 1, lo, hi)
            if lo == 0:                                          # first block: real cin
                chosen_sum, carry = (sum1, carry1) if cin else (sum0, carry0)
            else:                                                # later blocks: the mux
                chosen_sum, carry = (sum1, carry1) if carry else (sum0, carry0)
                boundaries += 1
            total |= chosen_sum
        return total, 2 * k + boundaries

    # ---- carry lookahead ----
    @staticmethod
    def _combine(upper, lower):
        gu, pu = upper
        gl, pl = lower
        return (gu | (pu & gl), pu & pl)

    def _cla(self, a, b, cin):
        leaf = self.CLA_LEAF
        # ---- Stage I ----
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

        # ---- Stage II ----
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

        # ---- Stage 0 ----
        total = 0
        for lo, _ in levels[0]:
            part, _ = self._ripple_block(a, b, carry_in[lo], lo, min(lo + leaf, 32))
            total |= part
        depth = len(levels) - 1
        return total, 1 + 2 * depth * 2 + 1

    def add(self, a, b, cin=0):
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
    # MULTIPLIERS   (33-bit U, 32-bit V)
    # ==================================================================
    @staticmethod
    def _shift_uv_right(u, v):
        return u >> 1, ((v >> 1) | ((u & 1) << 31)) & MASK32

    def _iterative(self, n, m):
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
        n = to_signed(a)                             # multiplicand, sign extended
        fn = self._booth if self.multiplier == 'booth' else self._iterative
        _, v, steps = fn(n, b)
        self.stats['muls'] += 1
        self.stats['mul_addsub'] += steps
        return v & MASK32

    # ==================================================================
    # DIVIDERS   (33-bit U, 32-bit V)
    # ==================================================================
    @staticmethod
    def _shift_uv_left(u, v):
        return (u << 1) | ((v >> 31) & 1), (v << 1) & MASK32

    def _restoring(self, dividend, divisor):
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