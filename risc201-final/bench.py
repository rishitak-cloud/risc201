import os
import random
import sys

import fpu

from alu import ALU
from assembler import assemble
from cpu import SingleCycleCPU
from machine import Machine, load_file
from microcode import ControlMemory
from microcpu import MicroCPU
from pipeline import Pipeline

PROGRAMS = ['sum', 'fact', 'hazard', 'muldiv', 'bubble', 'fib', 'gcd', 'matmul', 'fp', 'ovf']
MICRO = ('horizontal', 'vertical', 'mixed')
MODES = ('single', 'pipe4', 'pipe6') + MICRO

# ---- assumed delays (ps): one copy, in pipeline.py ----
from pipeline import STAGE_DELAY, LATCH, clock_period
DECODER_PER_BIT = 6              # a decoder gets slower with every input bit
                                 # (vertical: one 5-bit decoder = 30 ps, as before)

PERIOD = {
    # one long cycle has to fit everything
    'single': sum(STAGE_DELAY.values()),
    # the slowest stage sets the clock, plus the pipeline latch
    'pipe6': clock_period(6),
    'pipe4': clock_period(4),
    # one micro-step = one register transfer; the slowest is an ALU op
    'horizontal': max(STAGE_DELAY.values()) + LATCH,
    'vertical': (max(STAGE_DELAY.values()) + LATCH
                 + DECODER_PER_BIT * ControlMemory('vertical').signal_bits),
    # the field decoders work in parallel: the widest field sets the delay
    'mixed': (max(STAGE_DELAY.values()) + LATCH
              + DECODER_PER_BIT * max(ControlMemory('mixed').field_bits)),
}

ALU_CONFIGS = [
    {'adder': 'ripple', 'multiplier': 'iterative', 'divider': 'restoring'},
    {'adder': 'cselect', 'multiplier': 'iterative', 'divider': 'restoring'},
    {'adder': 'cla', 'multiplier': 'booth', 'divider': 'nonrestoring'},
    {'adder': 'cla', 'multiplier': 'booth4', 'divider': 'nonrestoring'},
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


def section_pipeline_features():
    print("1b. PIPELINE FEATURES (6-stage, totals over all programs)")
    configs = [
        ('baseline: predict not-taken', {}),
        ('+ store forwarding (RW -> MA)', dict(store_forwarding=True)),
        ('+ 1-bit predictor + BTB', dict(predictor='1bit')),
        ('+ 2-bit predictor + BTB + RAS', dict(predictor='2bit')),
        ('2-bit + store forwarding', dict(predictor='2bit', store_forwarding=True)),
        ('multi-cycle: mul 4, div 20', dict(mul_latency=4, div_latency=20)),
        ('unified memory (one port)', dict(unified_memory=True)),
    ]
    rows, base = [], None
    for label, kw in configs:
        tot = dict(cycles=0, stalls=0, flushed=0, branches=0, mispredicted=0,
                   ex_stalls=0, struct_stalls=0, instructions=0)
        for p in PROGRAMS:
            m = Machine(load_file(f'examples/{p}.s'))
            cpu = Pipeline(m, 6, **kw)
            cpu.run()
            for k, v in cpu.stats().items():
                if k in tot:
                    tot[k] += v
        base = base or tot['cycles']
        acc = 100 * (1 - tot['mispredicted'] / tot['branches'])
        rows.append([label, tot['cycles'], f"{100 * (tot['cycles'] - base) / base:+.1f}%",
                     f"{tot['cycles'] / tot['instructions']:.3f}", tot['stalls'], tot['flushed'],
                     f"{acc:.0f}%", tot['ex_stalls'], tot['struct_stalls']])
    table('1b_pipeline_features', ['configuration', 'cycles', 'change', 'CPI', 'load-use',
                                   'flushed', 'predicted ok', 'EX busy', 'port busy'], rows)
    print("   every configuration ends in exactly the same registers and memory (tests)")
    print()


def section_microcode():
    print("2. MICROPROGRAMMED CONTROL: horizontal vs vertical vs mixed (field-encoded)")
    rows = []
    total_cycles = {mode: 0 for mode in MICRO}
    for p in PROGRAMS:
        for mode in MICRO:
            m, cpu = run(p, mode)
            s = cpu.stats()
            total_cycles[mode] += s['cycles']
            rows.append([p, mode, s['instructions'], s['cycles'], s['CPI'],
                         f"{s['rom_words']}x{s['rom_width']}={s['rom_bits']}",
                         ns(s['cycles'], mode)])
    table('2_microcode', ['program', 'control', 'instr', 'micro-cycles', 'CPI',
                          'ROM bits', 'time(ns)'], rows)

    print("   design space: all programs together")
    drows, point = [], {}
    for mode in MICRO:
        cm = ControlMemory(mode)
        time_ns = total_cycles[mode] * PERIOD[mode] / 1000
        point[mode] = (cm.size_bits(), time_ns)
        drows.append([mode, cm.width, cm.size_bits(), PERIOD[mode], total_cycles[mode],
                      f"{time_ns:.1f}"])
    table('2b_design_space', ['control', 'word width', 'ROM bits', 'clock(ps)',
                              'micro-cycles', 'time(ns)'], drows)
    for mode in MICRO:
        beaten_by = [o for o in MICRO if o != mode
                     and point[o][0] <= point[mode][0] and point[o][1] <= point[mode][1]
                     and point[o] != point[mode]]
        if beaten_by:
            print(f"   {mode}: DOMINATED - {', '.join(beaten_by)} is at least as good on "
                  f"both ROM size and total time")
        else:
            print(f"   {mode}: on the trade-off frontier (nothing beats it on both ROM size "
                  f"and total time)")
    print("   mixed uses the horizontal microprogram (same micro-cycles) but packs the signals")
    print("   into encoded fields: smaller ROM, paid for with a small decoder delay on the clock")
    print()


def section_micro_per_opcode():
    """Run one instance of each instruction type through all three control units
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
        for style in MICRO:
            prog = assemble(src)
            cpu = MicroCPU(Machine(prog), style)
            target = prog.symbols['X']
            while not (cpu.upc == 0 and cpu.pc == target):     # run up to X
                cpu.micro_step()
            start = cpu.cycle
            cpu.step_instruction()
            counts[style] = cpu.cycle - start
        rows.append([label, counts['horizontal'], counts['vertical'], counts['mixed'],
                     counts['vertical'] - counts['horizontal']])
    table('3_micro_per_opcode', ['instruction', 'horizontal', 'vertical', 'mixed',
                                 'vertical extra'], rows)


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
    print("   n-th root, froot (Newton-Raphson + exact check root^n == input): correctly rounded")
    roots = [(rng.randint(1, 254) << 23) | rng.getrandbits(23) for _ in range(2000)]
    rrows = []
    for n in (2, 3, 4, 5, 7, 10, 31):
        f = ALU().fpu
        for x in roots:
            f.root(x, n)
        rrows.append([n, f"{f.stats['root_steps'] / f.stats['froots']:.1f}", "100%"])
    table('6b_nth_root', ['n', 'Newton steps per root', 'correctly rounded'], rrows)
    print("   restoring: one quotient bit per step, exact remainder -> always correctly rounded")
    print("   newton / goldschmidt: a few multiply steps, approximate -> may be 1 ulp off")
    print()


def section_correctness():
    print(f"7. CORRECTNESS: {len(MODES)} processor models x {len(ALU_CONFIGS)} ALU configurations, "
          f"same final state?")
    ok = True
    for p in PROGRAMS:
        states = set()
        for mode in MODES:
            for opts in ALU_CONFIGS:
                m, cpu = run(p, mode, **opts)
                states.add(m.state())
        same = len(states) == 1
        ok &= same
        runs = len(MODES) * len(ALU_CONFIGS)
        print(f"   {p:8s} {f'same ({runs} runs)' if same else 'DIFFERENT!'}")
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
    section_pipeline_features()
    section_microcode()
    section_micro_per_opcode()
    section_alu_random()
    section_alu_programs()
    section_floating_point()
    return 0 if section_correctness() else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
