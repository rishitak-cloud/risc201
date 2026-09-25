"""
bench.py - evaluation harness (Part 5 of the assignment, whole team)

    python bench.py

Runs every test program on every processor model and prints:
  1. 4-stage vs 6-stage pipeline   (cycles, CPI, stalls, flushes, time)
  2. horizontal vs vertical microcode (cycles, CPI, control memory size)
  3. ALU algorithms                 (adder delay, multiply / divide steps)
  4. a correctness check: every model must end in the same state

Clock period model (assumed numbers, in picoseconds - change them here):
  Logic delay of each part of the work, from Sarangi's 5 stages with OF
  split into ID + OF:
"""
import random

from alu import ALU
from machine import Machine, load_file
from microcpu import MicroCPU
from pipeline import Pipeline

PROGRAMS = ['sum', 'fact', 'hazard', 'muldiv', 'bubble']

# ---- assumed delays (ps) ----
STAGE_DELAY = {'IF': 200, 'ID': 100, 'OF': 150, 'EX': 250, 'MA': 200, 'RW': 100}
LATCH = 20                       # pipeline register overhead per stage
DECODER = 30                     # extra delay of the vertical micro-op decoder

PERIOD = {
    'pipe6': max(STAGE_DELAY.values()) + LATCH,
    'pipe4': max(STAGE_DELAY['IF'], STAGE_DELAY['ID'] + STAGE_DELAY['OF'],
                 STAGE_DELAY['EX'] + STAGE_DELAY['MA'], STAGE_DELAY['RW']) + LATCH,
    'horizontal': max(STAGE_DELAY.values()) + LATCH,           # slowest single transfer
    'vertical': max(STAGE_DELAY.values()) + LATCH + DECODER,
}


def run(name, mode, **alu_opts):
    m = Machine(load_file(f'examples/{name}.s'), alu=ALU(**alu_opts))
    cpu = Pipeline(m, int(mode[-1])) if mode.startswith('pipe') else MicroCPU(m, mode)
    cpu.run()
    return m, cpu


def table(headers, rows):
    widths = [max(len(str(x)) for x in col) for col in zip(headers, *rows)]
    line = lambda r: '  '.join(str(x).rjust(w) for x, w in zip(r, widths))
    print(line(headers))
    print('  '.join('-' * w for w in widths))
    for r in rows:
        print(line(r))
    print()


def main():
    print(f"clock periods used (ps): {PERIOD}\n")

    # 1. pipelines ----------------------------------------------------------
    print("1. PIPELINES: 4-stage (IF OF EX+MA RW) vs 6-stage (IF ID OF EX MA RW)")
    rows = []
    for p in PROGRAMS:
        for mode in ('pipe4', 'pipe6'):
            m, cpu = run(p, mode)
            s = cpu.stats()
            rows.append([p, mode, s['instructions'], s['cycles'], s['CPI'], s['stalls'],
                         s['flushed'], f"{s['cycles'] * PERIOD[mode] / 1000:.1f}"])
    table(['program', 'model', 'instr', 'cycles', 'CPI', 'stalls', 'flushed', 'time(ns)'], rows)

    # 2. microcode ----------------------------------------------------------
    print("2. MICROPROGRAMMED CONTROL: horizontal vs vertical")
    rows = []
    for p in PROGRAMS:
        for mode in ('horizontal', 'vertical'):
            m, cpu = run(p, mode)
            s = cpu.stats()
            rows.append([p, mode, s['instructions'], s['cycles'], s['CPI'],
                         f"{s['rom_words']}x{s['rom_width']}={s['rom_bits']}",
                         f"{s['cycles'] * PERIOD[mode] / 1000:.1f}"])
    table(['program', 'model', 'instr', 'cycles', 'CPI', 'ROM bits', 'time(ns)'], rows)

    # 3. ALU algorithms -----------------------------------------------------
    print("3. ALU ALGORITHMS on 2000 random operand pairs")
    rng = random.Random(201)
    pairs = [(rng.getrandbits(32), rng.getrandbits(32)) for _ in range(2000)]
    small = [(rng.randint(-5000, 5000), rng.randint(1, 300)) for _ in range(2000)]
    rows = []
    for adder in ALU.ADDERS:
        a = ALU(adder=adder)
        for x, y in pairs:
            a.add(x, y)
        rows.append(['add', adder, f"{a.stats['adder_delay'] / a.stats['adds']:.0f} gate delays"])
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
            rows.append([f'mul ({label})', mul,
                         f"{a.stats['mul_addsub'] / a.stats['muls']:.1f} add/sub per mul"])
    for div in ALU.DIVIDERS:
        a = ALU(divider=div)
        for x, y in small:
            a.divmod(x, y)
        rows.append(['div', div, f"{a.stats['div_addsub'] / a.stats['divs']:.1f} add/sub per div"])
    table(['op', 'algorithm', 'average cost'], rows)

    print("   same programs, pipe6, ALU work counted:")
    rows = []
    for p in PROGRAMS:
        for opts in ({'adder': 'ripple', 'multiplier': 'shiftadd', 'divider': 'restoring'},
                     {'adder': 'cla', 'multiplier': 'booth', 'divider': 'nonrestoring'}):
            m, cpu = run(p, 'pipe6', **opts)
            st = m.alu.stats
            rows.append([p, '/'.join(opts.values()), st['adds'], st['adder_delay'],
                         st['mul_addsub'], st['div_addsub']])
    table(['program', 'adder/mul/div', 'adds', 'adder delay', 'mul add/sub', 'div add/sub'], rows)

    # 4. correctness --------------------------------------------------------
    print("4. CORRECTNESS: every model and every ALU choice ends in the same state")
    ok = True
    for p in PROGRAMS:
        states = set()
        for mode in ('pipe4', 'pipe6', 'horizontal', 'vertical'):
            for opts in ({'adder': 'ripple', 'multiplier': 'shiftadd', 'divider': 'restoring'},
                         {'adder': 'cla', 'multiplier': 'booth', 'divider': 'nonrestoring'}):
                m, cpu = run(p, mode, **opts)
                states.add(m.state())
        same = len(states) == 1
        ok &= same
        print(f"   {p:8s} {'same' if same else 'DIFFERENT!'}")
    print("\nall models agree" if ok else "\nMISMATCH - a model has a bug")


if __name__ == '__main__':
    main()
