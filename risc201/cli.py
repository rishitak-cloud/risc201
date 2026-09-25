"""
cli.py - interactive simulator and debugger for RISC201 (Member D)

    python cli.py examples/fact.s                     6-stage pipeline (default)
    python cli.py examples/fact.s --mode single       single-cycle CPU, one instruction per step
    python cli.py examples/fact.s --mode pipe4        4-stage pipeline
    python cli.py examples/fact.s --mode horizontal   microprogrammed, horizontal
    python cli.py examples/fact.s --mode vertical     microprogrammed, vertical
    python cli.py examples/sum.s --run                run to the end, print results

  other options:
    --adder ripple|cla   --mul shiftadd|booth   --div restoring|nonrestoring
    --no-guard           turn the stack safety checks off

Type 'help' inside the debugger for the commands.
"""
import cmd
import sys

import isa
from alu import ALU
from assembler import AsmError
from exceptions import RiscError
from machine import Machine, load_file
from microcpu import MicroCPU
from pipeline import Pipeline
from cpu import SingleCycleCPU
import stackview

MODES = ('single', 'pipe4', 'pipe6', 'horizontal', 'vertical')


def build(path, mode, alu_opts, guard):
    prog = load_file(path)
    machine = Machine(prog, alu=ALU(**alu_opts), guard=guard)
    if mode == 'single':
        cpu = SingleCycleCPU(machine)
    elif mode.startswith('pipe'):
        cpu = Pipeline(machine, int(mode[-1]))
    else:
        cpu = MicroCPU(machine, mode)
    return prog, machine, cpu


def format_regs(machine, pc):
    v = machine.regs.values
    lines = []
    for row in range(4):
        cells = []
        for r in range(row * 4, row * 4 + 4):
            cells.append(f"{isa.reg_name(r):>3s} = {v[r]:08x} ({isa.to_signed(v[r]):>6d})")
        lines.append('   '.join(cells))
    lines.append(f"flags: {machine.flags}      pc: {pc:#06x}")
    return '\n'.join(lines)


class Debugger(cmd.Cmd):
    intro = "RISC201 simulator. Type 'help' for commands, 'quit' to leave."

    def __init__(self, path, mode, alu_opts, guard):
        super().__init__()
        self.path, self.mode, self.alu_opts, self.guard = path, mode, alu_opts, guard
        self.prompt = f"({mode}) "
        self.do_reset('', quiet=True)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    @property
    def is_pipe(self):
        return self.mode.startswith('pipe')

    @property
    def is_single(self):
        return self.mode == 'single'

    def current_pc(self):
        return self.cpu.pc

    def address(self, text):
        text = text.strip()
        if text in self.machine.symbols:
            return self.machine.symbols[text]
        return int(text, 0)

    def ready(self):
        if self.error:
            print(f"stopped by an exception: {self.error}\n(type 'reset' to start again)")
            return False
        if self.cpu.halted:
            print("program has halted (type 'reset' to start again)")
            return False
        return True

    def guarded(self, action):
        """Run one step; catch runtime exceptions and report them."""
        try:
            action()
            return True
        except RiscError as e:
            self.error = e
            print(f"\n!! {e}")
            if e.pc is not None and e.pc in self.prog.lines:
                print(f"   (source line {self.prog.lines[e.pc]} of {self.path})")
            print("   use 'regs', 'stack', 'pipe' to look around")
            return False

    # ------------------------------------------------------------------
    # commands
    # ------------------------------------------------------------------
    def do_reset(self, arg, quiet=False):
        """reset - reload the program and start again"""
        self.prog, self.machine, self.cpu = build(self.path, self.mode, self.alu_opts, self.guard)
        self.error = None
        self.cpu.breakpoints = getattr(self, 'breakpoints', set())
        self.breakpoints = self.cpu.breakpoints
        self.watches = getattr(self, 'watches', [])
        if not quiet:
            print("reset.")

    def do_step(self, arg):
        """step [n] - n clock cycles (pipeline) or n micro-steps (microprogrammed)"""
        n = int(arg) if arg.strip() else 1
        for _ in range(n):
            if not self.ready():
                return
            if not self.guarded(self.cpu.step):
                return
            if self.is_single:
                print(self.cpu.show())
            elif not self.is_pipe:
                print(self.cpu.format_step(self.cpu.trace[-1]))
        if self.is_pipe:
            print(self.cpu.show())
        if self.cpu.halted:
            print("-- halted --")
    do_s = do_step

    def do_next(self, arg):
        """next - finish exactly one more instruction"""
        if not self.ready():
            return
        if self.is_single:
            if self.guarded(self.cpu.step):
                print(self.cpu.show())
        elif self.is_pipe:
            target = self.cpu.retired + 1
            while self.cpu.retired < target and not self.cpu.halted:
                if not self.guarded(self.cpu.step):
                    return
            print(self.cpu.show())
        else:
            start = self.cpu.cycle
            ok = self.guarded(self.cpu.step_instruction)
            for st in self.cpu.trace:
                if st['cycle'] > start:
                    print(self.cpu.format_step(st))
            if not ok:
                return
        if self.cpu.halted:
            print("-- halted --")
    do_n = do_next

    def watched_values(self):
        values = {}
        for w in self.watches:
            if w in isa.REG_NAMES:
                values[w] = self.machine.regs.read(isa.REG_NAMES[w])
            else:
                values[w] = self.machine.mem.peek(self.address(w))
        return values

    def do_continue(self, arg):
        """continue - run until hlt, a breakpoint, a watch change or an exception"""
        if not self.ready():
            return
        before = self.watched_values()
        while not self.cpu.halted:
            if not self.guarded(self.cpu.step):
                return
            if self.cpu.hit_breakpoint is not None:
                print(f"breakpoint at {self.cpu.hit_breakpoint:#06x}")
                break
            if self.watches:
                now = self.watched_values()
                changed = [w for w in now if now[w] != before[w]]
                if changed:
                    for w in changed:
                        print(f"watch {w}: {before[w]:#x} -> {now[w]:#x}")
                    break
            if self.cpu.cycle > 2000000:
                print("stopped: 2,000,000 cycles without hlt (infinite loop?)")
                return
        print(self.cpu.show())
        if self.cpu.halted:
            print("-- halted --")
            self.do_stats('')
    do_c = do_continue
    do_run = do_continue

    def do_break(self, arg):
        """break <label|address> - stop when that instruction executes (reaches EX). No argument: list"""
        if not arg.strip():
            print(', '.join(f"{b:#06x}" for b in sorted(self.breakpoints)) or "no breakpoints")
            return
        try:
            self.breakpoints.add(self.address(arg))
        except ValueError:
            print(f"unknown label or address '{arg}'")
    do_b = do_break

    def do_watch(self, arg):
        """watch <register|address|label> - continue stops when it changes. No argument: list.
        unwatch <...|all> removes."""
        w = arg.strip().lower() if arg.strip().lower() in isa.REG_NAMES else arg.strip()
        if not w:
            print(', '.join(self.watches) or "no watches")
            return
        try:
            if w not in isa.REG_NAMES:
                self.address(w)
        except ValueError:
            print(f"unknown register, label or address '{arg}'")
            return
        self.watches.append(w)

    def do_unwatch(self, arg):
        """unwatch <register|address|label|all>"""
        if arg.strip() == 'all':
            self.watches.clear()
        elif arg.strip() in self.watches:
            self.watches.remove(arg.strip())

    def do_set(self, arg):
        """set <register> <value>   or   set mem <address|label> <value>"""
        parts = arg.split()
        try:
            if len(parts) == 2 and parts[0].lower() in isa.REG_NAMES:
                self.machine.regs.values[isa.REG_NAMES[parts[0].lower()]] = int(parts[1], 0) & isa.MASK32
            elif len(parts) == 3 and parts[0] == 'mem':
                a = self.address(parts[1])
                if not 0 <= a < self.machine.mem.size or a % 4:
                    print("address must be inside memory and a multiple of 4")
                    return
                self.machine.mem.words[a // 4] = int(parts[2], 0) & isa.MASK32
            else:
                print("usage: set r3 10   |   set mem 0x1000 42")
                return
        except ValueError:
            print("bad number or address")
            return
        print("ok  (note: a pipeline may already hold an old copy of this value)" if self.is_pipe else "ok")

    def do_delete(self, arg):
        """delete <label|address|all> - remove breakpoints"""
        if arg.strip() == 'all':
            self.breakpoints.clear()
        else:
            self.breakpoints.discard(self.address(arg))

    def do_regs(self, arg):
        """regs - register file, flags and pc"""
        print(format_regs(self.machine, self.current_pc()))
    do_r = do_regs

    def do_mem(self, arg):
        """mem <address|label> [count] - show memory words"""
        parts = arg.split()
        if not parts:
            print("usage: mem <address|label> [count]")
            return
        start = self.address(parts[0]) & ~3
        count = int(parts[1]) if len(parts) > 1 else 8
        for a in range(start, start + 4 * count, 4):
            v = self.machine.mem.peek(a)
            if v is None:
                break
            label = self.machine.labels.get(a, '')
            print(f"  {a:#06x}  {v:08x}  {isa.to_signed(v):>11d}   {label}")
    do_x = do_mem

    def do_pipe(self, arg):
        """pipe - what is in every stage now (or the microprogram state)"""
        print(self.cpu.show())
    do_p = do_pipe

    def do_diag(self, arg):
        """diag [n] - pipeline diagram of the last n instructions (pipeline modes)"""
        if not self.is_pipe:
            print("diag is for pipeline modes; use 'ucode' or 'step' here")
            return
        print(self.cpu.diagram(int(arg) if arg.strip() else 12))

    def do_ucode(self, arg):
        """ucode - print the control memory (microprogrammed modes)"""
        if self.is_pipe or self.is_single:
            print("ucode is for the horizontal / vertical modes")
            return
        print(self.cpu.cm.listing())

    def do_stack(self, arg):
        """stack - ASCII picture of the stack and its frames"""
        print(stackview.render(self.machine))

    def do_dis(self, arg):
        """dis - disassemble the program, marking where execution is"""
        marks = {}
        if self.is_pipe:
            for name, ins in self.cpu.slot.items():
                if ins:
                    marks.setdefault(ins['pc'], []).append(name)
        elif self.is_single:
            marks[self.cpu.pc] = ['<- next']
        else:
            marks[self.cpu.instr_pc] = ['<-']
        for a in range(0, self.machine.code_end, 4):
            if a in self.machine.labels:
                print(f"{self.machine.labels[a]}:")
            d = isa.decode(self.machine.mem.peek(a))
            text = isa.format_instr(d, a, self.machine.labels)
            flag = ' '.join(marks.get(a, []))
            print(f"  {a:#06x}  {text:28s} {flag}")

    def do_stats(self, arg):
        """stats - cycles, CPI, stalls, flushes, ALU work"""
        for k, v in self.cpu.stats().items():
            print(f"  {k:16s} {v}")
        a = self.machine.alu
        print(f"  ALU: adder={a.adder} multiplier={a.multiplier} divider={a.divider}")
        for k, v in a.stats.items():
            print(f"  {'alu.' + k:16s} {v}")

    def do_quit(self, arg):
        """quit - leave"""
        return True
    do_q = do_quit
    do_EOF = do_quit

    def emptyline(self):
        pass        # pressing enter does nothing (safer than repeating 'continue')


def parse_args(argv):
    opts = {'mode': 'pipe6', 'guard': True, 'run': False,
            'alu': {'adder': 'cla', 'multiplier': 'booth', 'divider': 'nonrestoring'}}
    path = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == '--mode':
            opts['mode'] = argv[i + 1]; i += 1
        elif a == '--adder':
            opts['alu']['adder'] = argv[i + 1]; i += 1
        elif a == '--mul':
            opts['alu']['multiplier'] = argv[i + 1]; i += 1
        elif a == '--div':
            opts['alu']['divider'] = argv[i + 1]; i += 1
        elif a == '--no-guard':
            opts['guard'] = False
        elif a == '--run':
            opts['run'] = True
        else:
            path = a
        i += 1
    if opts['mode'] not in MODES:
        raise SystemExit(f"--mode must be one of {', '.join(MODES)}")
    return path, opts


def main(argv):
    path, opts = parse_args(argv)
    if not path:
        print(__doc__)
        return 1
    try:
        dbg = Debugger(path, opts['mode'], opts['alu'], opts['guard'])
    except AsmError as e:
        print(f"{path}: error: {e}")
        return 1
    if opts['run']:
        dbg.do_continue('')
        print()
        dbg.do_regs('')
        return 0 if dbg.error is None else 2
    dbg.cmdloop()
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
