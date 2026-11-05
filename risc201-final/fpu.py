import math
import struct
from fractions import Fraction

BIAS = 127
FRAC = 23
EXTRA = 3                                  # guard, round, sticky
HIDDEN = 1 << FRAC
QNAN = 0x7FC00000
POS_INF = 0x7F800000
MAX_FINITE = 0x7F7FFFFF
SIGN = 1 << 31
MASK32 = 0xFFFFFFFF

ROUNDING = ('nearest', 'zero', 'up', 'down')
DIVIDERS = ('restoring', 'newton', 'goldschmidt')


# ---------------------------------------------------------------------------
# Converting between Python floats and bit patterns (assembler, CLI, tests)
# ---------------------------------------------------------------------------
def isa_signed(v):
    v &= 0xFFFFFFFF
    return v - (1 << 32) if v >> 31 else v


def float_to_bits(value):
    return struct.unpack('<I', struct.pack('<f', value))[0]


def bits_to_float(bits):
    return struct.unpack('<f', struct.pack('<I', bits & MASK32))[0]


def unpack(x):
    """32-bit pattern -> (sign, exponent field, mantissa field)"""
    return (x >> 31) & 1, (x >> 23) & 0xFF, x & 0x7FFFFF


def pack(s, e, m):
    return (s << 31) | (e << 23) | (m & 0x7FFFFF)


def is_nan(x):
    _, e, m = unpack(x)
    return e == 255 and m != 0


def is_inf(x):
    _, e, m = unpack(x)
    return e == 255 and m == 0


def is_zero(x):
    return x & 0x7FFFFFFF == 0


def significand(x):
    """(P, E) with value = P x 2^(E - 127 - 23). Denormals use E = 1 and no
    hidden bit (book eq. 8.23: 0.M x 2^-126)."""
    _, e, m = unpack(x)
    if e == 0:
        return m, 1
    return HIDDEN | m, e


def sticky_shift_right(w, n):
    """Shift right by n, OR-ing every bit that falls off into the LSB.
    This is how the sticky bit is maintained (book 8.4.3)."""
    if n <= 0:
        return w << -n
    lost = w & ((1 << n) - 1)
    return (w >> n) | (1 if lost else 0)


class FPU:
    def __init__(self, alu=None, rounding='nearest', divider='restoring'):
        assert rounding in ROUNDING and divider in DIVIDERS
        self.alu = alu                  # integer ALU, for its adder and multiplier
        self.rounding = rounding
        self.divider = divider
        self.reset_stats()

    def reset_stats(self):
        self.status = {'invalid': 0, 'divzero': 0, 'overflow': 0, 'underflow': 0, 'inexact': 0}
        self.stats = {'fadds': 0, 'fmuls': 0, 'fdivs': 0, 'div_steps': 0,
                      'fsqrts': 0, 'froots': 0, 'root_steps': 0}

    def _flag(self, name):
        self.status[name] += 1

    # ------------------------------------------------------------------
    # Normalise, round, pack  (book 8.4.1 - 8.4.3, figure 8.17 right half)
    # ------------------------------------------------------------------
    def _round_increment(self, s, lsb, r, sticky):
        """Book table 8.6. r = round bit (MSB of the residue),
        sticky = OR of the rest of the residue."""
        if self.rounding == 'nearest':
            return r and (sticky or lsb)          # ties go to the even value
        if self.rounding == 'zero':
            return 0                              # truncation
        if self.rounding == 'up':
            return (r or sticky) and not s        # towards +infinity
        return (r or sticky) and s                # 'down': towards -infinity

    def _overflow(self, s):
        self._flag('overflow')
        self._flag('inexact')
        to_inf = (self.rounding == 'nearest' or
                  (self.rounding == 'up' and not s) or
                  (self.rounding == 'down' and s))
        return (s << 31) | (POS_INF if to_inf else MAX_FINITE)

    def round_and_pack(self, s, e, w, fb):
        """Build a float from value = W x 2^(e - 127 - fb), W > 0.
        W may have any number of bits; the low ones become G/R/S."""
        target = FRAC + EXTRA                      # hidden bit lands here
        top = w.bit_length() - 1
        E = e + top - fb                           # exponent once normalised
        shift = top - target
        if E < 1:                                  # too small: make a denormal
            shift += 1 - E
            E = 1
        w = sticky_shift_right(w, shift)           # left shifts are exact

        residue = w & 0b111
        lsb = (w >> EXTRA) & 1
        r, sticky = (residue >> 2) & 1, residue & 0b11
        w >>= EXTRA
        if residue:
            self._flag('inexact')
        if self._round_increment(s, lsb, r, sticky):
            w += 1
            if w == 1 << (FRAC + 1):               # renormalise after rounding
                w >>= 1
                E += 1

        if E >= 255:
            return self._overflow(s)
        if w < HIDDEN:                             # denormal or zero
            if residue:
                self._flag('underflow')
            return pack(s, 0, w)
        return pack(s, E, w - HIDDEN)

    # ------------------------------------------------------------------
    # 8.4  Addition and subtraction  (book figure 8.17)
    # ------------------------------------------------------------------
    def add(self, a, b, subtract=False):
        self.stats['fadds'] += 1
        if subtract:
            b ^= SIGN                              # A - B = A + (-B)
        if is_nan(a) or is_nan(b):
            return QNAN
        if is_inf(a) or is_inf(b):
            if is_inf(a) and is_inf(b) and (a ^ b) & SIGN:
                self._flag('invalid')              # inf - inf
                return QNAN
            return a if is_inf(a) else b
        if is_zero(a) and is_zero(b):              # +0 + -0
            if (a ^ b) & SIGN:
                return SIGN if self.rounding == 'down' else 0
            return a
        if is_zero(a):                             # "A = 0?  C = B"
            return b
        if is_zero(b):                             # "B = 0?  C = A"
            return a

        sa, sb = a >> 31, b >> 31
        pa, ea = significand(a)
        pb, eb = significand(b)
        if ea < eb:                                # swap so that EB <= EA
            sa, sb, pa, pb, ea, eb = sb, sa, pb, pa, eb, ea

        # W <- PB >> (EA - EB), with 3 extra G/R/S bits;  E <- EA;  S <- sign(A)
        w = sticky_shift_right(pb << EXTRA, ea - eb)
        E, S = ea, sa
        add = self.alu.add if self.alu else (lambda x, y: (x + y) & MASK32)
        if sa != sb:                               # signs differ: W <- -W
            w = add(~w & MASK32, 1)
        w = add(w, pa << EXTRA)                    # W <- W + PA  (integer adder)
        if w & SIGN:                               # W < 0: W <- -W, flip sign
            w = add(~w & MASK32, 1)
            S ^= 1
        if w == 0:                                 # exact cancellation
            return SIGN if self.rounding == 'down' else 0
        return self.round_and_pack(S, E, w, FRAC + EXTRA)

    def sub(self, a, b):
        return self.add(a, b, subtract=True)

    # ------------------------------------------------------------------
    # 8.5  Multiplication  (book figure 8.18)
    # ------------------------------------------------------------------
    def _multiply_significands(self, pa, pb):
        """Uses the integer ALU's multiplier (iterative or Booth): it
        produces a 64-bit product in UV, wide enough for 24 x 24 bits."""
        if self.alu is None:
            return pa * pb
        u, v, _ = self.alu.multiplier_fn()(pa, pb)
        return (u << 32) | v

    def mul(self, a, b):
        self.stats['fmuls'] += 1
        s = ((a ^ b) >> 31) & 1                    # S <- sign(A) xor sign(B)
        if is_nan(a) or is_nan(b):
            return QNAN
        if (is_inf(a) and is_zero(b)) or (is_zero(a) and is_inf(b)):
            self._flag('invalid')                  # inf x 0
            return QNAN
        if is_inf(a) or is_inf(b):
            return (s << 31) | POS_INF
        if is_zero(a) or is_zero(b):               # "A = 0?  C = 0"
            return s << 31
        pa, ea = significand(a)
        pb, eb = significand(b)
        w = self._multiply_significands(pa, pb)    # W <- PA x PB (46 fraction bits)
        E = ea + eb - BIAS                         # E <- EA + EB - bias
        return self.round_and_pack(s, E, w, 2 * FRAC)

    # ------------------------------------------------------------------
    # 8.6  Division
    # ------------------------------------------------------------------
    QBITS = FRAC + EXTRA + 2                       # enough quotient bits to round

    def _div_restoring(self, pa, pb):
        """8.6.1 simple division: the restoring algorithm (8.3.2) run on the
        significands. Exact: the remainder becomes the sticky bit."""
        rem, q = pa, 0
        for _ in range(self.QBITS + 1):
            q <<= 1
            if rem >= pb:                          # U - D >= 0: quotient bit 1
                rem -= pb
                q |= 1
            rem <<= 1                              # (subtract-then-restore, merged)
            self.stats['div_steps'] += 1
        return (q << 1) | (1 if rem else 0), self.QBITS + 1

    F = 40                                         # fixed-point fraction bits

    def _reciprocal_newton(self, pb):
        """8.6.3 Newton-Raphson on f(x) = 1/x - b:  x(n+1) = 2x - b x^2
        = x (2 - b x). Start at x0 = 1/2; the error squares every step, so
        5 iterations reach 2^-32 (book table 8.8)."""
        F = self.F
        b = pb << (F - FRAC)                       # b in [1, 2), fixed point
        x = 1 << (F - 1)                           # x0 = 1/2
        for _ in range(5):
            bx = (b * x) >> F
            x = (x * ((2 << F) - bx)) >> F
            self.stats['div_steps'] += 1
        return x                                   # ~ 1/b, F fraction bits

    def _reciprocal_goldschmidt(self, pb):
        """8.6.2 Goldschmidt: with P = 1 + X, Y = (1 - X)/2,
        1/P = 1/2 x 1/(1 - Y) ~ 1/2 x (1+Y)(1+Y^2)(1+Y^4)(1+Y^8)(1+Y^16)."""
        F = self.F
        b = pb << (F - FRAC)
        y = ((2 << F) - b) >> 1                    # Y = (2 - P)/2,  Y < 1/2
        product, term = 1 << F, y
        for _ in range(5):
            product = (product * ((1 << F) + term)) >> F
            term = (term * term) >> F              # Y^2, Y^4, ... by squaring
            self.stats['div_steps'] += 1
        return product >> 1

    def div(self, a, b):
        self.stats['fdivs'] += 1
        s = ((a ^ b) >> 31) & 1
        if is_nan(a) or is_nan(b):
            return QNAN
        if (is_inf(a) and is_inf(b)) or (is_zero(a) and is_zero(b)):
            self._flag('invalid')                  # inf/inf, 0/0
            return QNAN
        if is_inf(a) or is_zero(b):
            if is_zero(b) and not is_inf(a):
                self._flag('divzero')
            return (s << 31) | POS_INF
        if is_zero(a) or is_inf(b):
            return s << 31
        pa, ea = significand(a)
        pb, eb = significand(b)
        # normalise denormal significands so the hidden bit is set
        while pa < HIDDEN:
            pa <<= 1
            ea -= 1
        while pb < HIDDEN:
            pb <<= 1
            eb -= 1
        E = ea - eb + BIAS                         # E <- EA - EB + bias
        if self.divider == 'restoring':
            q, fb = self._div_restoring(pa, pb)
        else:
            recip = (self._reciprocal_newton(pb) if self.divider == 'newton'
                     else self._reciprocal_goldschmidt(pb))
            q, fb = pa * recip, FRAC + self.F      # (PA) x (1/PB)
        return self.round_and_pack(s, E, q, fb)

    # ------------------------------------------------------------------
    # n-th root  (book exercise 33, generalised from square root to any n)
    # ------------------------------------------------------------------
    MAX_ROOT = 255
    SEED_BITS = 8            # size of the seed lookup table index

    def _iroot_newton(self, N, n):
        """floor(N ** (1/n)) for integers, by Newton-Raphson on f(x) = x^n - N:
            x <- x - f/f' = ((n - 1) x + N / x^(n-1)) / n
        For n = 2 this is the familiar (x + N/x) / 2.

        Seed: real hardware reads a first guess from a small lookup table
        indexed by the leading bits. We model an 8-bit table: the seed is
        computed from the top 8 bits of N only, rounded UP so the guess is
        ABOVE the root. From above, Newton falls every step (the error
        roughly squares: 8 -> 16 -> 32 good bits), so the first value that
        stops falling is the root. A final correction makes it exact."""
        bits = N.bit_length()
        top = min(self.SEED_BITS, bits)
        lead = (N >> (bits - top)) or 1                      # leading 8 bits = table index
        log2_root = (math.log2(lead + 1) + (bits - top)) / n # +1: round the index UP
        x = int(2.0 ** log2_root) + 2                        # seed, safely above the root
        while True:
            self.stats['root_steps'] += 1
            y = ((n - 1) * x + N // x ** (n - 1)) // n
            if y >= x:
                break
            x = y
        while x ** n > N:                                    # final correction
            x -= 1
        while (x + 1) ** n <= N:
            x += 1
        return x

    def root(self, x, n):
        """froot rd, rs1, n: the n-th root of a float, CORRECTLY ROUNDED,
        for n = 1 .. 255 (fsqrt is the case n = 2).

        Same three steps as the square root, each one generalised:
          1. value = P x 2^t. Make t a MULTIPLE OF n (shift P left by t mod n)
             so the exponent divides exactly: root(P x 2^t) = root(P) x 2^(t/n).
          2. widen P by 27n bits (a multiple of n) so the integer root has the
             24 + guard/round/sticky bits we need, then take the integer n-th
             root by Newton-Raphson.
          3. exact check: is root^n == the widened P? If not, that is the
             sticky bit, and the normal rounding logic does the rest.

        Signs: a negative number has a real odd root (cube root of -8 = -2)
        but no real even root (NaN, 'invalid').  root(+-0) follows IEEE
        754-2008 rootn: -0 for odd n, +0 for even n."""
        self.stats['froots'] += 1
        n = isa_signed(n)
        if not 1 <= n <= self.MAX_ROOT:
            self._flag('invalid')
            return QNAN
        if n == 1 or is_nan(x):
            return QNAN if is_nan(x) else x
        s = (x >> 31) & 1
        odd = n & 1
        if s and not odd and not is_zero(x):
            self._flag('invalid')                          # even root of a negative
            return QNAN
        if is_zero(x):
            return (s << 31) if odd else 0
        if is_inf(x):
            return x
        p, e = significand(x)
        while p < HIDDEN:                                  # normalise a denormal
            p <<= 1
            e -= 1
        t = e - BIAS - FRAC                                # |value| = p x 2^t
        r = t % n                                          # make t a multiple of n
        p <<= r
        t -= r
        widen = 27 * n                                     # a multiple of n
        N = p << widen                                     # |value| = N x 2^(t - widen)
        rt = self._iroot_newton(N, n)
        sticky = 1 if rt ** n != N else 0
        return self.round_and_pack(s if odd else 0, BIAS + (t - widen) // n,
                                   (rt << 1) | sticky, 1)

    def sqrt(self, x):
        """fsqrt = froot with n = 2. The one difference is IEEE's rule for
        sqrt(-0), which is -0 (rootn(-0, 2) is +0)."""
        self.stats['fsqrts'] += 1
        if is_zero(x):
            return x
        return self.root(x, 2)

    # ------------------------------------------------------------------
    # Comparison and conversions
    # ------------------------------------------------------------------
    def compare(self, a, b):
        """fcmp -> (E, GT). Book exercise 26: for non-NaN floats, sign +
        magnitude orders exactly like the numbers. NaN is unordered."""
        if is_nan(a) or is_nan(b):
            self._flag('invalid')
            return 0, 0
        key = lambda x: -(x & 0x7FFFFFFF) if x & SIGN else x
        ka, kb = key(a), key(b)
        return int(ka == kb), int(ka > kb)         # +0 == -0 falls out naturally

    def from_int(self, x):
        """itof: signed 32-bit integer -> nearest float."""
        x &= MASK32
        if x == 0:
            return 0
        s = x >> 31
        mag = (-x & MASK32) if s else x
        if s and mag == 0:                         # -2^31
            mag = 1 << 31
        return self.round_and_pack(s, BIAS, mag, 0)

    def to_int(self, x):
        """ftoi: float -> signed 32-bit integer, rounding towards zero (like
        C). NaN and out-of-range values saturate and raise 'invalid'."""
        s, e, m = unpack(x)
        if is_nan(x):
            self._flag('invalid')
            return 0x7FFFFFFF
        p, E = significand(x)
        shift = E - BIAS - FRAC
        if shift >= 0:
            if shift > 8:                          # far too big
                mag = 1 << 40
            else:
                mag = p << shift
        else:
            mag = p >> min(-shift, 64)
            if p & ((1 << min(-shift, 64)) - 1):
                self._flag('inexact')
        limit = (1 << 31) if s else (1 << 31) - 1
        if is_inf(x) or mag > limit:
            self._flag('invalid')
            return 0x80000000 if s else 0x7FFFFFFF
        return (-mag if s else mag) & MASK32


def exact_value(x):
    """Exact rational value of a finite float pattern (used by tests)."""
    p, e = significand(x)
    v = Fraction(p) * Fraction(2) ** (e - BIAS - FRAC)
    return -v if x & SIGN else v


if __name__ == '__main__':
    f = FPU()
    show = lambda bits: f"{bits:08x} = {bits_to_float(bits)!r}"
    print("0.1 + 0.2       ", show(f.add(float_to_bits(0.1), float_to_bits(0.2))))
    print("1e38 x 10       ", show(f.mul(float_to_bits(1e38), float_to_bits(10.0))), " (overflow)")
    print("1 / 3           ", show(f.div(float_to_bits(1.0), float_to_bits(3.0))))
    print("1e-38 / 1e6     ", show(f.div(float_to_bits(1e-38), float_to_bits(1e6))), " (denormal)")
    print("0 / 0           ", show(f.div(0, 0)))
    print("itof(-7)        ", show(f.from_int(-7 & MASK32)))
    print("ftoi(-2.75)     ", f.to_int(float_to_bits(-2.75)) - (1 << 32))
    print("sqrt(2)         ", show(f.sqrt(float_to_bits(2.0))))
    print("sqrt(-1)        ", show(f.sqrt(float_to_bits(-1.0))))
    print("cube root(-27)  ", show(f.root(float_to_bits(-27.0), 3)))
    print("5th root(1e10)  ", show(f.root(float_to_bits(1e10), 5)))
    print("7th root(2)     ", show(f.root(float_to_bits(2.0), 7)))
    print("status flags    ", f.status)
