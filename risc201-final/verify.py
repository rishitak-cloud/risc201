import argparse
import sys

import isa
from alu import ALU
from cpu import SingleCycleCPU
from exceptions import RiscError
from machine import Machine, load_file
from microcpu import MicroCPU

STYLES = ('horizontal', 'vertical', 'mixed')
MICRO_CAP = 64          # no instruction needs more micro-steps than this


class MicroStuck(Exception):
    """A (damaged) microprogram never returned to FETCH."""


def run_one_instruction(cpu):
    """Micro-steps until the instruction is finished (like step_instruction,
    but it gives up instead of looping forever on a corrupted ROM)."""
    cpu.hit_breakpoint = None
    cpu.micro_step()
    steps = 1
    while cpu.upc != 0 and not cpu.halted:
        if steps >= MICRO_CAP:
            raise MicroStuck(f"more than {MICRO_CAP} micro-steps without returning to FETCH")
        cpu.micro_step()
        steps += 1


def diff_state(ref, mic):
    """Human-readable differences between the reference and the micro CPU."""
    lines = []
    rm, mm = ref.m, mic.m
    for r in range(16):
        a, b = rm.regs.values[r], mm.regs.values[r]
        if a != b:
            lines.append(f"{isa.reg_name(r)}: reference={isa.to_signed(a)}  "
                         f"microcoded={isa.to_signed(b)}")
    for flag in ('E', 'GT', 'V'):
        a, b = getattr(rm.flags, flag), getattr(mm.flags, flag)
        if a != b:
            lines.append(f"flag {flag}: reference={a}  microcoded={b}")
    if rm.mem.words != mm.mem.words:
        shown = 0
        for i, (a, b) in enumerate(zip(rm.mem.words, mm.mem.words)):
            if a != b:
                lines.append(f"mem[{4 * i:#x}]: reference={isa.to_signed(a)}  "
                             f"microcoded={isa.to_signed(b)}")
                shown += 1
                if shown == 4:
                    lines.append("... more memory differences")
                    break
    if ref.halted != mic.halted:
        lines.append(f"halted: reference={ref.halted}  microcoded={mic.halted}")
    elif not ref.halted and ref.pc != mic.pc:
        lines.append(f"pc: reference={ref.pc:#06x}  microcoded={mic.pc:#06x}")
    return lines


def lockstep(program, style, flips=(), max_instr=200000, alu_opts=None):
    """Run reference and micro CPU together.
    flips: [(control word, bit)] to damage in the micro CPU's control memory.
    Returns a dict: ok, kind, instructions, lines (explanation)."""
    alu_opts = alu_opts or {}
    ref = SingleCycleCPU(Machine(program, alu=ALU(**alu_opts)))
    mic = MicroCPU(Machine(program, alu=ALU(**alu_opts)), style)
    for word, bit in flips:
        mic.cm.rom[word] ^= 1 << bit

    n = 0
    while True:
        n += 1
        ref_err = mic_err = None
        try:
            ref.step()
        except RiscError as e:
            ref_err = e
        try:
            run_one_instruction(mic)
        except RiscError as e:
            mic_err = e
        except Exception as e:                      # damaged microcode can crash in odd ways
            mic_err = e

        where = ""
        if ref.last:
            where = f"instruction #{n} at {ref.last['pc']:#06x}  {ref.last['text']}"

        if ref_err or mic_err:
            if ref_err and mic_err and type(ref_err) is type(mic_err):
                return {'ok': True, 'kind': 'match', 'instructions': n - 1,
                        'lines': [f"both stop with {type(ref_err).__name__} "
                                  f"(identical behaviour)"]}
            if isinstance(mic_err, MicroStuck):
                kind = 'stuck'
            elif mic_err and not isinstance(mic_err, RiscError):
                kind = 'crash'
            elif mic_err and not ref_err:
                kind = 'exception'
            else:
                kind = 'exception'
            return {'ok': False, 'kind': kind, 'instructions': n, 'lines': [
                f"differs at {where or f'instruction #{n}'}",
                f"reference:  {type(ref_err).__name__ if ref_err else 'ran normally'}",
                f"microcoded: {f'{type(mic_err).__name__}: {mic_err}' if mic_err else 'ran normally'}"]}

        diffs = diff_state(ref, mic)
        if diffs:
            return {'ok': False, 'kind': 'state', 'instructions': n,
                    'lines': [f"differs after {where}"] + diffs}
        if ref.halted:
            if ref.retired != mic.retired:
                return {'ok': False, 'kind': 'state', 'instructions': n, 'lines': [
                    f"instruction counts differ: reference={ref.retired} "
                    f"microcoded={mic.retired}"]}
            return {'ok': True, 'kind': 'match', 'instructions': n, 'lines': [
                "registers, flags and memory identical after every instruction"]}
        if n >= max_instr:
            return {'ok': True, 'kind': 'match', 'instructions': n, 'lines': [
                f"stopped after {max_instr} instructions, no difference so far"]}


# ---------------------------------------------------------------------------
# Mutation testing: damage every bit of the control memory, one at a time
# ---------------------------------------------------------------------------
def mutate(program, style, max_instr, alu_opts, verbose=False):
    clean = MicroCPU(Machine(program, alu=ALU(**(alu_opts or {}))), style)
    clean.run()
    used = clean.upc_hits                       # which words this program executes
    cm = clean.cm
    names = {i: lab for lab, i in cm.labels.items()}
    total = len(cm.rom) * cm.width

    caught = {'state': 0, 'crash': 0, 'exception': 0, 'stuck': 0}
    survivors_unused, survivors_used = [], []
    for word in range(len(cm.rom)):
        for bit in range(cm.width):
            res = lockstep(program, style, flips=[(word, bit)], max_instr=max_instr,
                           alu_opts=alu_opts)
            if res['ok']:
                (survivors_used if used[word] else survivors_unused).append((word, bit))
            else:
                caught[res['kind']] += 1

    n_caught = sum(caught.values())
    in_used = sum(1 for h in used if h) * cm.width
    print(f"mutation test: {style} control memory on this program")
    print(f"  {total} single-bit faults injected ({len(cm.rom)} words x {cm.width} bits)")
    print(f"  caught:   {n_caught:4d}  ({100 * n_caught / total:.1f}%)   "
          f"wrong registers/memory: {caught['state']},  crash or invalid microinstruction: "
          f"{caught['crash'] + caught['exception']},  stuck in a micro-loop: {caught['stuck']}")
    print(f"  survived: {len(survivors_unused) + len(survivors_used):4d}")
    print(f"     {len(survivors_unused):4d} in control words this program never executes "
          f"(nothing to catch: it never reads them)")
    print(f"     {len(survivors_used):4d} in executed words but with no visible effect on this "
          f"program (don't-care bits such as the")
    print("          next-address of a NEXT / FETCH word, or a signal that fires without "
          "changing anything)")
    if in_used:
        hit = in_used - len(survivors_used)
        print(f"  of the {in_used} bits in the words this program executes, a flip is detected "
              f"for {hit} ({100 * hit / in_used:.0f}%)")
    show = survivors_used if verbose else survivors_used[:8]
    if show:
        print("  undetected faults in executed words" + ("" if verbose else " (first 8; use -v for all)") + ":")
        for word, bit in show:
            print(f"     word {word:2d} {names.get(word, ''):7s} bit {bit:2d}  {cm.bit_meaning(bit)}")
    print()
    return n_caught, total


def main(argv):
    ap = argparse.ArgumentParser(description="lockstep verifier for the microprogrammed CPU")
    ap.add_argument('program', help="a .s, .hex or .bin file")
    ap.add_argument('--style', choices=STYLES + ('all',), default='all')
    ap.add_argument('--flip', nargs=3, metavar=('STYLE', 'WORD', 'BIT'),
                    help="damage one bit of the control memory and verify")
    ap.add_argument('--mutate', action='store_true',
                    help="damage every bit, one at a time, and report how many are caught")
    ap.add_argument('--max-instr', type=int, default=200000)
    ap.add_argument('-v', '--verbose', action='store_true')
    args = ap.parse_args(argv)
    program = load_file(args.program)

    if args.flip:
        style, word, bit = args.flip[0], int(args.flip[1]), int(args.flip[2])
        if style not in STYLES:
            raise SystemExit(f"STYLE must be one of {', '.join(STYLES)}")
        probe = MicroCPU(Machine(program), style)
        if not (0 <= word < len(probe.cm.rom) and 0 <= bit < probe.cm.width):
            raise SystemExit(f"{style}: word must be 0..{len(probe.cm.rom) - 1}, "
                             f"bit 0..{probe.cm.width - 1}")
        names = {i: lab for lab, i in probe.cm.labels.items()}
        print(f"injected fault: {style} control word {word} {names.get(word, '')}, "
              f"bit {bit} = {probe.cm.bit_meaning(bit)}")
        print(f"   word before: {probe.cm.rom[word]:#x}   after: "
              f"{probe.cm.rom[word] ^ (1 << bit):#x}")
        res = lockstep(program, style, [(word, bit)], args.max_instr)
        print(f"  {style:11s} {'PASS' if res['ok'] else 'FAIL  <- bug caught'}")
        for line in res['lines']:
            print(f"      {line}")
        return 0 if res['ok'] else 1

    styles = STYLES if args.style == 'all' else (args.style,)
    if args.mutate:
        for style in styles:
            mutate(program, style, args.max_instr, None, args.verbose)
        return 0

    print(f"verify {args.program}   reference = single-cycle CPU")
    failed = False
    for style in styles:
        res = lockstep(program, style, max_instr=args.max_instr)
        failed |= not res['ok']
        print(f"  {style:11s} {'PASS' if res['ok'] else 'FAIL'}  "
              f"({res['instructions']} instructions)")
        for line in res['lines']:
            print(f"      {line}")
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
