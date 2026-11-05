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
from microcode import check_rom, ControlMemory, SIGNALS, HORIZONTAL
from pipeline import Pipeline
from cpu import SingleCycleCPU
from microcpu import MicroCPU
from exceptions import MicrocodeFault, ArithmeticOverflow
import fpu
from preprocessor import expand_macros

MODES = ('single', 'pipe4', 'pipe6', 'horizontal', 'vertical', 'mixed')
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
EXPECTED = {'fp': {4: 0, 6: 5, 8: 0x40A00000, 10: isa.to_signed(0xC0400000), 11: 0x3FC00000}, 'ovf': {2: -2**31},
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
    expect(IllegalInstruction, ".word 0xF0000000\nhlt")           # opcode 11110 (unused)


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
    for style in ('horizontal', 'vertical', 'mixed'):
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


# ---------------------------------------------------------------- Member C extras
def test_every_rom_word_round_trips():
    """encode -> ROM integer -> decode gives back the microprogram, for all 3 styles."""
    for style in ControlMemory.STYLES:
        cm = ControlMemory(style)
        for word, (_, signals, seq, target) in zip(cm.rom, cm.source):
            assert cm.decode(word) == (sorted(signals, key=SIGNALS.index), seq,
                                       cm.labels.get(target, 0)), (style, hex(word))


def test_mixed_fields_are_legal():
    """Every signal is in exactly one field, and no horizontal word turns on
    two signals of the same field (otherwise the packing would lose information)."""
    cm = ControlMemory('mixed')
    assert sorted(s for f in cm.fields for s in f) == sorted(SIGNALS)
    for _, signals, _, _ in HORIZONTAL:
        for members in cm.fields:
            assert len([s for s in signals if s in members]) <= 1, (signals, members)


def test_mixed_is_the_middle_road():
    """Same micro-cycles as horizontal, ROM smaller than both horizontal and vertical."""
    h, v, x = (ControlMemory(s) for s in ('horizontal', 'vertical', 'mixed'))
    assert x.size_bits() < h.size_bits() and x.size_bits() < v.size_bits()
    assert x.width < h.width
    cycles = {}
    for style in ControlMemory.STYLES:
        cpu = MicroCPU(Machine(load_file('examples/fact.s')), style)
        cpu.run()
        cycles[style] = cpu.cycle
    assert cycles['mixed'] == cycles['horizontal'] < cycles['vertical']


def test_microcode_profile_counts():
    """The profiler counts every micro-cycle exactly once."""
    cpu = MicroCPU(Machine(load_file('examples/sum.s')), 'mixed')
    cpu.run()
    assert sum(cpu.upc_hits) == cpu.cycle
    assert cpu.upc_hits[0] == cpu.retired
    assert sum(cpu.opcode_hits.values()) == cpu.retired
    assert 'never executed' in cpu.profile_report() or 'control memory used' in cpu.profile_report()


def test_invalid_microinstruction_is_a_fault():
    """A control word whose bits name no signal raises MicrocodeFault, not a Python error."""
    cpu = MicroCPU(Machine(load_file('examples/sum.s')), 'vertical')
    cpu.cm.rom[0] |= 0b11111 << (cpu.cm.addr_bits + cpu.cm.seq_bits)    # micro-op code 31
    try:
        cpu.micro_step()
    except MicrocodeFault:
        return
    raise AssertionError("expected MicrocodeFault")


def test_verifier_passes_and_catches_injected_bugs():
    import verify
    prog = load_file('examples/sum.s')
    for style in verify.STYLES:
        assert verify.lockstep(prog, style)['ok'], style
    damaged = verify.lockstep(prog, 'horizontal', flips=[(3, 20)])      # switch ALU_OP off
    assert not damaged['ok']


def test_debugger_mode_switch():
    import contextlib
    import io
    import cli
    opts = {'adder': 'cla', 'multiplier': 'booth', 'divider': 'nonrestoring'}
    dbg = cli.Debugger('examples/sum.s', 'single', opts, True)
    with contextlib.redirect_stdout(io.StringIO()):
        for name in ('horizontal', 'vertical', 'mixed', 'pipe6', 'single'):
            dbg.do_mode(name)
            assert dbg.mode == name
            dbg.do_continue('')
            assert dbg.cpu.halted
            assert isa.to_signed(dbg.machine.regs.values[1]) == 55, name
        dbg.do_mode('nonsense')                       # rejected, nothing changes
        dbg.do_mode('vertical')
        dbg.do_uprof('')                              # profile works in a micro mode
    assert dbg.mode == 'vertical'


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


# ---------------------------------------------------------------- Member D extras
def test_booth4_halves_the_steps():
    a2, a4 = ALU(multiplier='booth'), ALU(multiplier='booth4')
    rng = random.Random(4)
    for _ in range(500):
        x, y = rng.getrandbits(32), rng.getrandbits(32)
        assert a4.mul(x, y) == (x * y) & isa.MASK32
        a2.mul(x, y)
    assert a4.stats['mul_addsub'] <= 16 * 500
    assert a4.stats['mul_addsub'] < a2.stats['mul_addsub']


def test_fsqrt():
    f = ALU().fpu
    b = fpu.float_to_bits
    assert f.sqrt(b(25.0)) == b(5.0)
    assert f.sqrt(b(2.0)) == b(1.4142135381698608)
    assert fpu.is_nan(f.sqrt(b(-1.0))) and f.status['invalid']
    assert f.sqrt(0x80000000) == 0x80000000             # sqrt(-0) = -0
    try:
        import numpy as np
    except ImportError:
        return
    rng = random.Random(5)
    for _ in range(3000):
        x = rng.getrandbits(31)                         # positive, incl. denormals
        ref = int(np.sqrt(np.array([x], np.uint32).view(np.float32)).view(np.uint32)[0])
        got = f.sqrt(x)
        assert (fpu.is_nan(got) and fpu.is_nan(ref)) or got == ref, hex(x)


def test_nth_root_correctly_rounded():
    """froot is correctly rounded for any n: checked with exact fractions,
    x must lie strictly between the n-th powers of the two midpoints around
    the result."""
    from fractions import Fraction
    f = ALU().fpu
    rng = random.Random(29)
    for n in (2, 3, 4, 5, 7, 12):
        for _ in range(300):
            x = (rng.randint(1, 254) << 23) | rng.getrandbits(23)
            if n % 2 and rng.random() < 0.3:
                x |= 1 << 31                             # odd roots of negatives
            r = f.root(x, n)
            assert (r >> 31) == ((x >> 31) if n % 2 else 0)
            m = r & 0x7FFFFFFF
            v = fpu.exact_value(m)
            lo = (fpu.exact_value(m - 1) + v) / 2
            hi = (v + fpu.exact_value(m + 1)) / 2
            assert lo ** n < abs(fpu.exact_value(x)) < hi ** n, (n, hex(x), hex(r))


def test_nth_root_special_cases():
    f = ALU().fpu
    b = fpu.float_to_bits
    assert f.root(b(-8.0), 3) == b(-2.0)
    assert f.root(b(1e10), 5) == b(100.0)
    assert fpu.is_nan(f.root(b(-16.0), 4))               # no real even root
    assert fpu.is_nan(f.root(b(2.0), 0))                 # n must be 1..255
    assert f.root(b(7.5), 1) == b(7.5)
    assert f.root(0x80000000, 3) == 0x80000000           # rootn(-0, odd) = -0
    assert f.root(0x80000000, 2) == 0                    # rootn(-0, even) = +0
    assert f.sqrt(0x80000000) == 0x80000000              # ... but sqrt(-0) = -0
    for x in range(1, 4000, 7):                          # froot(x, 2) == fsqrt(x)
        bits = (x * 997) & 0x7FFFFFFF
        if not fpu.is_nan(bits):
            assert f.root(bits, 2) == f.sqrt(bits)


# ---------------------------------------------------------------- Member B extras
def _run_pipe(src_or_file, depth=6, **opts):
    src = open(src_or_file).read() if src_or_file.endswith('.s') else src_or_file
    m = Machine(assemble(src))
    p = Pipeline(m, depth, **opts)
    p.run()
    return m, p


def test_pipeline_features_never_change_results():
    """Every option combination ends in exactly the single-cycle CPU's state."""
    import itertools
    for name in ('sum', 'fact', 'bubble', 'matmul', 'fp'):
        ref = Machine(load_file(f'examples/{name}.s'))
        SingleCycleCPU(ref).run()
        for depth, pred, sf, lat, um in itertools.product(
                (4, 6), ('none', '1bit', '2bit'), (False, True), (1, 5), (False, True)):
            m, _ = _run_pipe(f'examples/{name}.s', depth, predictor=pred, store_forwarding=sf,
                             mul_latency=lat, div_latency=lat * 4, unified_memory=um)
            assert m.state() == ref.state(), (name, depth, pred, sf, lat, um)


def test_branch_prediction_cuts_flushes():
    _, static = _run_pipe('examples/sum.s')
    _, one = _run_pipe('examples/sum.s', predictor='1bit')
    _, two = _run_pipe('examples/sum.s', predictor='2bit')
    assert two.mispredicted <= one.mispredicted < static.mispredicted
    assert two.cycle < static.cycle


def test_return_address_stack():
    """fact.s returns 6 times; the RAS predicts them, so 2-bit + RAS
    mispredicts far less than static prediction."""
    _, static = _run_pipe('examples/fact.s')
    _, ras = _run_pipe('examples/fact.s', predictor='2bit')
    assert ras.mispredicted < static.mispredicted / 2


def test_store_forwarding_removes_ld_st_stall():
    src = "mov r2, d\nnop\nnop\nld r1, 0[r2]\nst r1, 4[r2]\nhlt\nd: .word 7, 0"
    src = src.replace("d: .word 7, 0", "d: .word 7\n.space 4")
    src = "movu r2, 0x1000\nmov r1, 7\nst r1, 0[r2]\nnop\nld r1, 0[r2]\nst r1, 4[r2]\nhlt"
    m0, p0 = _run_pipe(src)
    m1, p1 = _run_pipe(src, store_forwarding=True)
    assert p0.stalls == 1 and p1.stalls == 0
    assert m1.mem.peek(0x1004) == 7


def test_multicycle_units():
    src = "mov r1, 3\nmov r2, 4\nmul r3, r1, r2\nadd r4, r3, 1\nhlt"
    m, p = _run_pipe(src, mul_latency=4)
    assert p.ex_stalls == 3 and m.regs.values[4] == 13


def test_unified_memory_structural_hazard():
    _, harvard = _run_pipe('examples/bubble.s')
    _, vonneumann = _run_pipe('examples/bubble.s', unified_memory=True)
    assert vonneumann.struct_stalls > 0 and vonneumann.cycle > harvard.cycle
    # in sum.s every load already causes a load-use stall, and IF does not
    # fetch during it - so the port conflict hides inside that bubble
    _, h, = _run_pipe('examples/sum.s')
    _, v = _run_pipe('examples/sum.s', unified_memory=True)
    assert v.struct_stalls == 0 and v.cycle == h.cycle


def test_precise_exceptions():
    """An older instruction always completes; a younger one never has an effect."""
    older_completes = "mov r2, d\nmov r4, 7\nnop\nnop\nld r1, 0[r2]\ndiv r3, r4, r0\nhlt\nd: .word 42"
    younger_no_effect = "sub sp, sp, 8192\ncmp r5, r5\nhlt"
    from exceptions import RiscError
    for depth in (4, 6):
        for src, check in ((older_completes, lambda m: m.regs.values[1] == 42),
                           (younger_no_effect, lambda m: m.flags.E == 0)):
            m = Machine(assemble(src))
            try:
                Pipeline(m, depth).run()
            except RiscError:
                assert check(m), (depth, src.splitlines()[-2])
                continue
            raise AssertionError("expected an exception")


def test_iron_law_stats():
    _, p4 = _run_pipe('examples/bubble.s', 4)
    _, p6 = _run_pipe('examples/bubble.s', 6)
    s4, s6 = p4.stats(), p6.stats()
    assert s4['CPI'] < s6['CPI']                      # 4-stage: fewer cycles ...
    assert s6['time_ns'] < s4['time_ns']              # ... but 6-stage finishes first
    assert s6['MIPS'] > s4['MIPS']


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
    import unittest
    import test_toolchain
    print("\ntoolchain suite (tests/test_toolchain.py):")
    result = unittest.TextTestRunner(verbosity=0).run(
        unittest.defaultTestLoader.loadTestsFromModule(test_toolchain))
    failed += len(result.failures) + len(result.errors)
    sys.exit(1 if failed else 0)

