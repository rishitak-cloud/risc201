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
from microcpu import MicroCPU
from pipeline import Pipeline
from preprocessor import expand_macros

MODES = ('pipe4', 'pipe6', 'horizontal', 'vertical')
EXAMPLES = ['sum', 'fact', 'hazard', 'muldiv', 'bubble']


def make_cpu(machine, mode):
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
EXPECTED = {'sum': {1: 55}, 'fact': {1: 720}, 'hazard': {5: 40},
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
    expect(IllegalInstruction, ".word 0xB0000000\nhlt")           # opcode 10110


def test_wrong_path_fetch_is_harmless():
    """The fetch after the final branch goes past the code; it must be
    flushed, not reported as an error."""
    run_source("b end\nend: hlt", 'pipe6')


def test_microcode_checker():
    bad = [('FETCH', ['A_RS1', 'ALU_OP'], 'FETCH', None)]
    try:
        check_rom(bad, 'horizontal')
    except ValueError:
        return
    raise AssertionError("A_RS1 + ALU_OP in one word must be rejected")


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
