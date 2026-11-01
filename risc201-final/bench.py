"""
bench.py - evaluation harness (assignment item 5, whole team)

    python bench.py              all tables
    python bench.py --csv out/   also write every table as a .csv file

The PDF asks to evaluate "for different test cases by considering 4-stage
and 6-stage ISAs, different assembly programs, horizontal vs. vertical
microprogrammed controllers, and different ALU algorithms". Each section
below is one of those:

  1. PROCESSORS        single-cycle vs 4-stage vs 6-stage    (Member B, C)
  2. MICROCODE         horizontal vs vertical, per program    (Member C)
  3. MICROCODE         micro-cycles per instruction type      (Member C)
  4. ALU               every algorithm on random operands     (Member D)
  5. ALU               every algorithm on the real programs   (Member D)
  6. FLOATING POINT    division algorithms: accuracy and work (Member D)
  7. CORRECTNESS       all 5 models x 3 ALU configurations give the same
                       registers and memory                   (everyone)

Clock period model. These are ASSUMED numbers (picoseconds), chosen to be
reasonable, and stated openly - change them here and every table updates.
The logic of one instruction is split along Sarangi's 5 stages, with OF
split into decode (ID) + register read (OF).
"""
import os
import random
import sys

import fpu

from alu import ALU
from assembler import assemble
from cpu import SingleCycleCPU
from machine import Machine, load_file
from microcpu import MicroCPU
from pipeline import Pipeline

PROGRAMS = ['sum', 'fact', 'hazard', 'muldiv', 'bubble', 'fib', 'gcd', 'matmul', 'fp', 'ovf']
MODES = ('single', 'pipe4', 'pipe6', 'horizontal', 'vertical')

# ---- assumed delays (ps) ----
STAGE_DELAY = {'IF': 200, 'ID': 100, 'OF': 150, 'EX': 250, 'MA': 200, 'RW': 100}
LATCH = 20                       # pipeline register overhead per stage
DECODER = 30                     # vertical microcode needs a micro-op decoder

PERIOD = {
    # one long cycle has to fit everything
    'single': sum(STAGE_DELAY.values()),
    # the slowest stage sets the clock, plus the pipeline latch
    'pipe6': max(STAGE_DELAY.values()) + LATCH,
    'pipe4': max(STAGE_DELAY['IF'], STAGE_DELAY['ID'] + STAGE_DELAY['OF'],
                 STAGE_DELAY['EX'] + STAGE_DELAY['MA'], STAGE_DELAY['RW']) + LATCH,
    # one micro-step = one register transfer; the slowest is an ALU op
    'horizontal': max(STAGE_DELAY.values()) + LATCH,
    'vertical': max(STAGE_DELAY.values()) + LATCH + DECODER,
}

ALU_CONFIGS = [
    {'adder': 'ripple', 'multiplier': 'iterative', 'divider': 'restoring'},
    {'adder': 'cselect', 'multiplier': 'iterative', 'divider': 'restoring'},
    {'adder': 'cla', 'multiplier': 'booth', 'divider': 'nonrestoring'},
]

CSV_DIR = None


def make_cpu(machine, mode):
    if mode == 'single':
        return SingleCycleCPU(machine)
    if mode.startswith('pipe'):
        return Pipeline(machine, int(mode[-1]))
    return MicroCPU(machine, mode)


def run(name, mode, **alu_opts):
    m = Machine(load_file(f'examples/{name}.s'), alu=ALU(**alu_opts))
    cpu = make_cpu(m, mode)
    cpu.run()
    return m, cpu


def ns(cycles, mode):
    return f"{cycles * PERIOD[mode] / 1000:.1f}"


def table(name, headers, rows):
    widths = [max(len(str(x)) for x in col) for col in zip(headers, *rows)]
    line = lambda r: '  '.join(str(x).rjust(w) for x, w in zip(r, widths))
    print(line(headers))
    print('  '.join('-' * w for w in widths))
    for r in rows:
        print(line(r))
    print()
    if CSV_DIR:
        with open(os.path.join(CSV_DIR, name + '.csv'), 'w') as f:
            f.write(','.join(headers) + '\n')
            for r in rows:
                f.write(','.join(str(x) for x in r) + '\n')


# ---------------------------------------------------------------------------
def section_processors():
    print("1. PROCESSORS: single-cycle vs 4-stage (IF OF EX+MA RW) vs 6-stage (IF ID OF EX MA RW)")
    rows, best = [], {}
    for p in PROGRAMS:
        times = {}
        for mode in ('single', 'pipe4', 'pipe6'):
            m, cpu = run(p, mode)
            s = cpu.stats()
            times[mode] = s['cycles'] * PERIOD[mode]
            rows.append([p, mode, s['instructions'], s['cycles'], s['CPI'],
                         s.get('stalls', '-'), s.get('flushed', '-'), ns(s['cycles'], mode)])
        best[p] = min(times, key=times.get)
    table('1_processors', ['program', 'model', 'instr', 'cycles', 'CPI', 'stalls', 'flushed',
                           'time(ns)'], rows)
    print("   fastest in real time:", ', '.join(f"{p}={b}" for p, b in best.items()))
    print()


def section_microcode():
    print("2. MICROPROGRAMMED CONTROL: horizontal vs vertical")
    rows = []
    for p in PROGRAMS:
        for mode in ('horizontal', 'vertical'):
            m, cpu = run(p, mode)
            s = cpu.stats()
            rows.append([p, mode, s['instructions'], s['cycles'], s['CPI'],
                         f"{s['rom_words']}x{s['rom_width']}={s['rom_bits']}",
                         ns(s['cycles'], mode)])
    table('2_microcode', ['program', 'control', 'instr', 'micro-cycles', 'CPI',
                          'ROM bits', 'time(ns)'], rows)


def section_micro_per_opcode():
    """Run one instance of each instruction type through both control units
    and count its micro-cycles. Branches are measured taken and not taken."""
    print("3. MICRO-CYCLES PER INSTRUCTION (fetch + decode + routine)")
    cases = [
        ('add/sub/mul/and.. reg', 'mov r2, 5\nmov r3, 7\nX: add r1, r2, r3\nhlt'),
        ('add/sub/mul/and.. imm', 'X: add r1, r1, 5\nhlt'),
        ('mov / not', 'X: mov r1, 5\nhlt'),
        ('cmp', 'X: cmp r1, 5\nhlt'),
        ('ld', 'mov r2, d\nX: ld r1, 0[r2]\nhlt\nd: .word 9'),
        ('st', 'movu r2, 0x1000\nX: st r1, 0[r2]\nhlt'),
        ('beq / bgt not taken', 'cmp r1, 1\nX: beq e\ne: hlt'),
        ('beq / bgt taken', 'cmp r1, 0\nX: beq e\ne: hlt'),
        ('b', 'X: b e\ne: hlt'),
        ('call', 'X: call f\nhlt\nf: hlt'),
        ('ret', 'call f\nhlt\nf:\nX: ret'),
        ('nop', 'X: nop\nhlt'),
    ]
    rows = []
    for label, src in cases:
        counts = {}
        for style in ('horizontal', 'vertical'):
            prog = assemble(src)
            cpu = MicroCPU(Machine(prog), style)
            target = prog.symbols['X']
            while not (cpu.upc == 0 and cpu.pc == target):     # run up to X
                cpu.micro_step()
            start = cpu.cycle
            cpu.step_instruction()
            counts[style] = cpu.cycle - start
        rows.append([label, counts['horizontal'], counts['vertical'],
                     counts['vertical'] - counts['horizontal']])
    table('3_micro_per_opcode', ['instruction', 'horizontal', 'vertical', 'extra'], rows)


def section_alu_random():
    print("4. ALU ALGORITHMS on random operands")
    rng = random.Random(201)
    pairs = [(rng.getrandbits(32), rng.getrandbits(32)) for _ in range(1000)]
    small = [(rng.randint(-5000, 5000), rng.randint(1, 300)) for _ in range(1000)]
    rows = []
    for adder in ALU.ADDERS:
        a = ALU(adder=adder)
        for x, y in pairs:
            a.add(x, y)
        rows.append(['add', 'random 32-bit', adder,
                     f"{a.stats['adder_delay'] / a.stats['adds']:.0f} gate levels"])
    mul_sets = {
        'random 32-bit': pairs,
        'small positive': [(x, rng.randint(1, 300)) for x, _ in small],
        'small negative': [(x, rng.randint(-300, -1)) for x, _ in small],
    }
    for label, data in mul_sets.items():
        for mul in ALU.MULTIPLIERS:
            a = ALU(multiplier=mul)
            for x, y in data:
                a.mul(x, y)
            rows.append(['mul', label, mul,
                         f"{a.stats['mul_addsub'] / a.stats['muls']:.1f} add/sub"])
    for div in ALU.DIVIDERS:
        a = ALU(divider=div)
        for x, y in small:
            a.divmod(x, y)
        rows.append(['div', 'small', div, f"{a.stats['div_addsub'] / a.stats['divs']:.1f} add/sub"])
    table('4_alu_random', ['op', 'operands', 'algorithm', 'average cost'], rows)


def section_alu_programs():
    print("5. ALU ALGORITHMS on the real programs (6-stage pipeline)")
    rows = []
    for p in PROGRAMS:
        for opts in ALU_CONFIGS:
            m, cpu = run(p, 'pipe6', **opts)
            st = m.alu.stats
            rows.append([p, '/'.join(opts.values()), st['adds'], st['adder_delay'],
                         st['muls'], st['mul_addsub'], st['divs'], st['div_addsub']])
    table('5_alu_programs', ['program', 'adder/mul/div', 'adds', 'adder delay', 'muls',
                             'mul add/sub', 'divs', 'div add/sub'], rows)


def section_floating_point():
    print("6. FLOATING POINT DIVISION (book 8.6) on 20000 random normal floats")
    rng = random.Random(754)
    pairs = [((rng.randint(60, 190) << 23) | rng.getrandbits(23),
              (rng.randint(60, 190) << 23) | rng.getrandbits(23)) for _ in range(20000)]
    reference = ALU().fpu
    exact = [reference.div(x, y) for x, y in pairs]
    rows = []
    for d in fpu.DIVIDERS:
        f = ALU(fdivider=d).fpu
        errors = [abs(f.div(x, y) - e) for (x, y), e in zip(pairs, exact)]
        rows.append([d, f"{100 * errors.count(0) / len(errors):.2f}%", max(errors),
                     f"{f.stats['div_steps'] / f.stats['fdivs']:.0f}",
                     {'restoring': 'O(n log n)', 'newton': 'O(log(n)^2)',
                      'goldschmidt': 'O(log(n)^2)'}[d]])
    table('6_fp_division', ['algorithm', 'correctly rounded', 'worst error (ulp)',
                            'steps per divide', 'book complexity'], rows)
    print("   restoring: one quotient bit per step, exact remainder -> always correctly rounded")
    print("   newton / goldschmidt: a few multiply steps, approximate -> may be 1 ulp off")
    print()


def section_correctness():
    print("7. CORRECTNESS: 5 processor models x 3 ALU configurations, same final state?")
    ok = True
    for p in PROGRAMS:
        states = set()
        for mode in MODES:
            for opts in ALU_CONFIGS:
                m, cpu = run(p, mode, **opts)
                states.add(m.state())
        same = len(states) == 1
        ok &= same
        print(f"   {p:8s} {'same (15 runs)' if same else 'DIFFERENT!'}")
    print("\nall models agree" if ok else "\nMISMATCH - a model has a bug")
    return ok


def main(argv):
    global CSV_DIR
    if '--csv' in argv:
        CSV_DIR = argv[argv.index('--csv') + 1]
        os.makedirs(CSV_DIR, exist_ok=True)
    print("clock periods used (ps):", ', '.join(f"{k}={v}" for k, v in PERIOD.items()))
    print()
    section_processors()
    section_microcode()
    section_micro_per_opcode()
    section_alu_random()
    section_alu_programs()
    section_floating_point()
    return 0 if section_correctness() else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
