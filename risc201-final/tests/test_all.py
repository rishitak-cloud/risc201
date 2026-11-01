"""
tests/test_all.py - run with:  python tests/test_all.py
(no extra libraries needed; every test is a plain function with asserts)
"""
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import isa
from alu import ALU
from assembler import assemble, AsmError, to_hex
from disassembler import disassemble, read_words
from exceptions import (StackOverflow, StackUnderflow, ProtectionFault, DivideByZero,
                        StackAccessViolation, IllegalInstruction)
from machine import Machine, load_file
from microcode import check_rom
from pipeline import Pipeline
from cpu import SingleCycleCPU
from microcpu import MicroCPU
from exceptions import MicrocodeFault, ArithmeticOverflow
import fpu
from preprocessor import expand_macros

MODES = ('single', 'pipe4', 'pipe6', 'horizontal', 'vertical')
EXAMPLES = ['sum', 'fact', 'hazard', 'muldiv', 'bubble', 'fib', 'gcd', 'matmul', 'fp', 'ovf']


def make_cpu(machine, mode):
    if mode == 'single':
        return SingleCycleCPU(machine)
    if mode.startswith('pipe'):
        return Pipeline(machine, int(mode[-1]))
    return MicroCPU(machine, mode)


def run_source(src, mode='pipe6', guard=True):
    m = Machine(assemble(src), guard=guard)
    cpu = make_cpu(m, mode)
    cpu.run(max_cycles=200000)
    return m, cpu


# ---------------------------------------------------------------- ISA
def test_encode_decode():
    w = isa.encode('add', rd=1, rs1=2, rs2=3)
    d = isa.decode(w)
    assert (d['name'], d['I'], d['rd'], d['rs1'], d['rs2']) == ('add', 0, 1, 2, 3)
    d = isa.decode(isa.encode('sub', rd=4, rs1=5, imm=-7))
    assert d['I'] == 1 and isa.to_signed(d['imm']) == -7
    d = isa.decode(isa.encode('mov', rd=1, imm=0x1234, mod=isa.MOD_H))
    assert d['imm'] == 0x12340000
    d = isa.decode(isa.encode('beq', offset=-3))
    assert d['offset'] == -3


def test_modifiers():
    assert isa.expand_immediate(0xFFFF, isa.MOD_DEFAULT) == 0xFFFFFFFF
    assert isa.expand_immediate(0xFFFF, isa.MOD_U) == 0x0000FFFF
    assert isa.expand_immediate(0xFFFF, isa.MOD_H) == 0xFFFF0000


# ---------------------------------------------------------------- assembler
def test_assembler_errors():
    for bad in ['add r1, r2', 'mov r1, 70000', 'foo r1, r2', 'b nowhere',
                'ld r1, r2', 'add r1, r2, r16', 'x: nop\nx: nop']:
        try:
            assemble(bad)
        except AsmError:
            continue
        raise AssertionError(f"should have failed: {bad!r}")


def test_forward_reference():
    prog = assemble("b end\nnop\nend: hlt")
    assert isa.decode(prog.words[0])['offset'] == 2


def test_round_trip():
    """.s -> words -> disassembly -> words must give the same words."""
    for name in EXAMPLES + ['overflow', 'smash', 'divzero']:
        prog = load_file(f'examples/{name}.s')
        words, code_end = read_words(to_hex(prog))
        text = '\n'.join(line.split('@')[0] for line in disassemble(words, code_end))
        again = assemble(text)
        assert again.words == prog.words, name


def test_preprocessor():
    out = [t.strip() for _, t in expand_macros("push {r2, ra}\npop {r2, ra}")]
    assert out == ['sub sp, sp, 8', 'st r2, 4[sp]', 'st ra, 0[sp]',
                   'ld r2, 4[sp]', 'ld ra, 0[sp]', 'add sp, sp, 8']


# ---------------------------------------------------------------- ALU
def test_alu_algorithms():
    rng = random.Random(1)
    for adder in ALU.ADDERS:
        for mul in ALU.MULTIPLIERS:
            for div in ALU.DIVIDERS:
                a = ALU(adder, mul, div)
                for _ in range(200):
                    x, y = rng.getrandbits(32), rng.getrandbits(32)
                    assert a.add(x, y) == (x + y) & isa.MASK32
                    assert a.sub(x, y) == (x - y) & isa.MASK32
                    assert a.mul(x, y) == (x * y) & isa.MASK32
                    sx, sy = isa.to_signed(x), isa.to_signed(y) or 1
                    q, r = a.divmod(sx, sy)
                    exp_q = abs(sx) // abs(sy) * (1 if (sx < 0) == (sy < 0) else -1)
                    assert isa.to_signed(q) == exp_q
                    assert isa.to_signed(r) == sx - exp_q * sy


# ---------------------------------------------------------------- processors
EXPECTED = {'fp': {4: 0, 6: 5}, 'ovf': {2: -2**31},
            'fib': {1: 6765}, 'gcd': {1: 21}, 'matmul': {10: -14, 11: -54},
            'sum': {1: 55}, 'fact': {1: 720}, 'hazard': {5: 40},
            'muldiv': {3: -42, 4: -3, 5: -1, 7: -2}, 'bubble': {9: -15, 10: 99}}


def test_all_models_agree():
    for name in EXAMPLES:
        states = []
        for mode in MODES:
            m = Machine(load_file(f'examples/{name}.s'))
            make_cpu(m, mode).run()
            for reg, value in EXPECTED[name].items():
                assert isa.to_signed(m.regs.values[reg]) == value, (name, mode, reg)
            states.append(m.state())
        assert all(s == states[0] for s in states), name


def test_pipeline_hazards():
    _, p4 = run_source(open('examples/hazard.s').read(), 'pipe4')
    _, p6 = run_source(open('examples/hazard.s').read(), 'pipe6')
    assert p4.stalls == 0 and p6.stalls == 2          # load-use only hurts the 6-stage
    assert p4.flushed == 2 and p6.flushed == 3        # branch penalty = stages before EX


def test_no_hazard_timing():
    """n independent instructions: cycles = n + depth - 1."""
    src = '\n'.join(f"mov r{i}, {i}" for i in range(1, 9)) + '\nhlt'
    for depth in (4, 6):
        _, p = run_source(src, f'pipe{depth}')
        assert p.cycle == 9 + depth - 1


# ---------------------------------------------------------------- exceptions
def expect(exc, src_or_file, guard=True):
    src = open(src_or_file).read() if src_or_file.endswith('.s') else src_or_file
    for mode in MODES:
        try:
            run_source(src, mode, guard)
        except exc:
            continue
        raise AssertionError(f"{mode}: expected {exc.__name__}")


def test_exceptions():
    expect(StackOverflow, 'examples/overflow.s')
    expect(ProtectionFault, 'examples/smash.s')
    expect(DivideByZero, 'examples/divzero.s')
    expect(StackAccessViolation, "ld r1, -4[sp]\nhlt")          # below sp
    expect(StackUnderflow, "pop {r1}\nhlt")                      # empty stack
    expect(ProtectionFault, "mov r1, 0\nst r1, 0[r1]\nhlt")       # write into code
    expect(IllegalInstruction, ".word 0xE0000000\nhlt")           # opcode 11100 (unused)


def test_wrong_path_fetch_is_harmless():
    """The fetch after the final branch goes past the code; it must be
    flushed, not reported as an error."""
    run_source("b end\nend: hlt", 'pipe6')


def test_single_cycle_cpi():
    """single-cycle: exactly one cycle per instruction."""
    m, cpu = run_source(open('examples/sum.s').read(), 'single')
    assert cpu.cycle == cpu.retired == 64


def test_microcode_checker():
    bad = [('FETCH', ['A_RS1', 'ALU_OP'], 'FETCH', None)]
    try:
        check_rom(bad, 'horizontal')
    except ValueError:
        return
    raise AssertionError("A_RS1 + ALU_OP in one word must be rejected")


def test_microcode_runs_from_rom():
    """Item 3: the microprogrammed CPU executes from control memory and
    visits microPC 0 (FETCH) once per instruction."""
    for style in ('horizontal', 'vertical'):
        m = Machine(load_file('examples/sum.s'))
        cpu = MicroCPU(m, style)
        fetches = 0
        while not cpu.halted:
            if cpu.upc == 0:
                fetches += 1
            cpu.micro_step()
        assert fetches == cpu.retired == 64, style
        assert isa.to_signed(m.regs.values[1]) == 55


def test_horizontal_fewer_cycles_vertical_smaller_rom():
    mh = Machine(load_file('examples/fact.s')); h = MicroCPU(mh, 'horizontal'); h.run()
    mv = Machine(load_file('examples/fact.s')); v = MicroCPU(mv, 'vertical'); v.run()
    assert h.cycle < v.cycle
    assert v.cm.size_bits() < h.cm.size_bits()


def test_microcode_fault():
    m = Machine(load_file('examples/sum.s'))
    cpu = MicroCPU(m, 'horizontal')
    cpu.upc = 999                               # microPC outside the ROM
    try:
        cpu.micro_step()
    except MicrocodeFault:
        return
    raise AssertionError("expected MicrocodeFault")


# ---------------------------------------------------------------- overflow
def test_overflow_detection():
    a = ALU()
    cases = [('add', 0x7FFFFFFF, 1, True), ('add', 5, 7, False),
             ('add', 0x80000000, 0xFFFFFFFF, True),           # -2^31 + -1
             ('sub', 0x80000000, 1, True), ('sub', 5, 7, False),
             ('mul', 0x10000, 0x10000, True), ('mul', 0xFFFFFFFF, 0xFFFFFFFF, False),
             ('mul', 46341, 46341, True), ('mul', 46340, 46340, False),
             ('div', 0x80000000, 0xFFFFFFFF, True), ('div', 7, 2, False)]
    for mul in ALU.MULTIPLIERS:
        a = ALU(multiplier=mul)
        m = Machine(assemble("hlt"), alu=a)            # connects the V flag
        for op, x, y, expect in cases:
            a.compute(op, x, y)
            assert m.flags.V == int(expect), (mul, op, hex(x), hex(y))


def test_trap_overflow():
    for mode in MODES:
        m = Machine(load_file('examples/ovf.s'), alu=ALU(trap_overflow=True))
        try:
            make_cpu(m, mode).run()
        except ArithmeticOverflow as e:
            assert e.pc == 8, mode                      # precise: the add itself
            continue
        raise AssertionError(f"{mode}: expected ArithmeticOverflow")


# ---------------------------------------------------------------- floating point
def _fp_reference_check(n=3000):
    import numpy as np
    np.seterr(all='ignore')
    as_f = lambda b: np.array([b], dtype=np.uint32).view(np.float32)[0]
    as_b = lambda f: int(np.array([f], dtype=np.float32).view(np.uint32)[0])
    rng = random.Random(754)
    specials = [0, 0x80000000, 0x7F800000, 0xFF800000, 0x7FC00000, 0x7F7FFFFF, 0x00000001,
                0x00800000, 0x3F800000]
    pick = lambda: rng.choice(specials) if rng.random() < 0.1 else rng.getrandbits(32)
    f = ALU().fpu
    for _ in range(n):
        x, y = pick(), pick()
        for ours, ref in ((f.add(x, y), as_f(x) + as_f(y)), (f.sub(x, y), as_f(x) - as_f(y)),
                          (f.mul(x, y), as_f(x) * as_f(y)), (f.div(x, y), as_f(x) / as_f(y))):
            rb = as_b(ref)
            if fpu.is_nan(rb):
                assert fpu.is_nan(ours)
            else:
                assert ours == rb, (hex(x), hex(y), hex(ours), hex(rb))


def test_fpu_matches_ieee_hardware():
    try:
        import numpy  # noqa: F401
    except ImportError:
        return                                          # reference not available
    _fp_reference_check()


def test_fpu_special_values():
    f = ALU().fpu
    b = fpu.float_to_bits
    assert fpu.is_nan(f.div(0, 0)) and f.status['invalid']
    assert f.div(b(1.0), 0) == fpu.POS_INF and f.status['divzero']
    assert f.mul(b(1e38), b(10.0)) == fpu.POS_INF and f.status['overflow']
    assert f.add(b(0.1), b(0.2)) == b(0.30000001192092896)
    assert f.compare(0, 0x80000000) == (1, 0)           # +0 == -0
    assert f.compare(fpu.QNAN, b(1.0)) == (0, 0)        # NaN is unordered


def test_fpu_rounding_modes():
    one, three = fpu.float_to_bits(1.0), fpu.float_to_bits(3.0)
    results = {}
    for mode in fpu.ROUNDING:
        results[mode] = ALU(rounding=mode).fpu.div(one, three)
    exact = fpu.exact_value(one) / fpu.exact_value(three)
    assert fpu.exact_value(results['down']) < exact < fpu.exact_value(results['up'])
    assert results['zero'] == results['down']           # positive: towards zero = down
    assert results['nearest'] in (results['up'], results['down'])


def test_fp_division_algorithms():
    rng = random.Random(3)
    for d in fpu.DIVIDERS:
        f = ALU(fdivider=d).fpu
        exact = ALU().fpu
        for _ in range(500):
            x = (rng.randint(60, 190) << 23) | rng.getrandbits(23)
            y = (rng.randint(60, 190) << 23) | rng.getrandbits(23)
            assert abs(f.div(x, y) - exact.div(x, y)) <= 1, d     # within 1 ulp


def test_float_constants_and_macros():
    prog = assemble("li r1, 0x12345678\nlif r2, -2.5\nhlt\nd: .float 3.25")
    m = Machine(prog)
    SingleCycleCPU(m).run()
    assert m.regs.values[1] == 0x12345678
    assert fpu.bits_to_float(m.regs.values[2]) == -2.5
    assert prog.words[-1] == fpu.float_to_bits(3.25)


if __name__ == '__main__':
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith('test_')]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception as e:
            failed += 1
            print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)

