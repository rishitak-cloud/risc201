"""
cpu.py - single-cycle CPU execution simulator (Member D)

The simplest possible processor: one call to step() runs ONE whole
instruction, start to finish, before the next one begins. No pipeline,
no overlap, no hazards. (Sarangi Chapter 8: the single-cycle processor
before pipelining.)

Every step does the same five things, in order:

    1. FETCH       word = memory[PC]
    2. DECODE      split the word into fields (isa.decode)
    3. EXECUTE     read registers, run the ALU / compare / branch decision
    4. MEMORY      ld reads memory, st writes memory
    5. WRITE BACK  put the result in rd; set the next PC

Because nothing overlaps, this model is easy to trust. The other models
(4/6-stage pipelines, microprogrammed CPU) must end in exactly the same
state as this one - only their timing is different.

For the debugger, every step remembers what it changed (self.last), so
`step` can print e.g.   0x000c  ld r4, 0[r2]   r4: 0 -> 1
"""
import isa
from exceptions import MemoryFault, IllegalInstruction


class SingleCycleCPU:
    def __init__(self, machine):
        self.m = machine
        self.pc = 0
        self.cycle = 0              # 1 cycle per instruction
        self.retired = 0
        self.halted = False
        self.breakpoints = set()
        self.hit_breakpoint = None
        self.last = None            # what the last instruction did

    # ------------------------------------------------------------------
    # small helpers that also record changes for the debugger
    # ------------------------------------------------------------------
    def _write_reg(self, r, value, changes):
        old = self.m.regs.read(r)
        self.m.regs.write(r, value)
        new = self.m.regs.read(r)
        if old != new:
            changes.append(f"{isa.reg_name(r)}: {isa.to_signed(old)} -> {isa.to_signed(new)}")
        else:
            changes.append(f"{isa.reg_name(r)} = {isa.to_signed(new)} (same)")

    # ------------------------------------------------------------------
    # one instruction
    # ------------------------------------------------------------------
    def step(self):
        if self.halted:
            return
        self.hit_breakpoint = None
        m = self.m
        pc = self.pc
        m.pc_now = pc
        changes = []

        # 1. FETCH
        if not 0 <= pc < m.code_end:
            raise MemoryFault(f"instruction fetch from {pc:#x}, outside the code", pc)
        word = m.mem.load(pc)

        # 2. DECODE
        d = isa.decode(word)
        name = d['name']
        if name is None:
            raise IllegalInstruction(f"opcode {d['opcode']:#07b} does not exist", pc)

        next_pc = pc + 4                     # default: the next instruction

        # 3. EXECUTE  (+ 4. MEMORY  + 5. WRITE BACK)
        if name in isa.THREE_ADDR:                       # add sub mul ... asr
            a = m.regs.read(d['rs1'])
            b = d['imm'] if d['I'] else m.regs.read(d['rs2'])
            self._write_reg(d['rd'], m.alu.compute(name, a, b, pc), changes)

        elif name in isa.TWO_ADDR:                       # mov, not
            b = d['imm'] if d['I'] else m.regs.read(d['rs2'])
            self._write_reg(d['rd'], m.alu.compute(name, 0, b, pc), changes)

        elif name == 'cmp':
            a = m.regs.read(d['rs1'])
            b = d['imm'] if d['I'] else m.regs.read(d['rs2'])
            m.flags.E, m.flags.GT = m.alu.compare(a, b)
            changes.append(f"flags: {m.flags}")

        elif name == 'ld':
            address = m.alu.add(m.regs.read(d['rs1']), d['imm'])
            self._write_reg(d['rd'], m.mem.load(address), changes)

        elif name == 'st':
            address = m.alu.add(m.regs.read(d['rs1']), d['imm'])
            value = m.regs.read(d['rd'])
            m.mem.store(address, value)
            changes.append(f"mem[{address:#x}] = {isa.to_signed(value)}")

        elif name in ('beq', 'bgt', 'b'):
            taken = (name == 'b' or
                     (name == 'beq' and m.flags.E) or
                     (name == 'bgt' and m.flags.GT))
            if taken:
                next_pc = pc + 4 * d['offset']
            changes.append("taken" if taken else "not taken")

        elif name == 'call':
            self._write_reg(isa.RA, pc + 4, changes)
            next_pc = pc + 4 * d['offset']
            m.note_call(pc, next_pc)

        elif name == 'ret':
            next_pc = m.regs.read(isa.RA)
            m.check_return(next_pc, pc)
            m.note_return()

        elif name == 'hlt':
            self.halted = True
            changes.append("halted")
        # nop: nothing

        # the PC update is the last part of write back
        if next_pc != pc + 4:
            changes.append(f"pc -> {next_pc:#06x}")
        self.pc = next_pc
        self.cycle += 1
        self.retired += 1
        self.last = {'pc': pc, 'text': isa.format_instr(d, pc, m.labels), 'changes': changes}

        if self.pc in self.breakpoints and not self.halted:
            self.hit_breakpoint = self.pc

    def run(self, max_cycles=2000000):
        while not self.halted:
            if self.cycle >= max_cycles:
                raise RuntimeError(f"no hlt after {max_cycles} instructions (infinite loop?)")
            self.step()

    # ------------------------------------------------------------------
    # display (same method names as the other models, so the CLI works)
    # ------------------------------------------------------------------
    def stats(self):
        return {'cycles': self.cycle, 'instructions': self.retired,
                'CPI': 1.0 if self.retired else 0}

    def show(self):
        if self.last is None:
            return f"single-cycle CPU   nothing executed yet   next pc={self.pc:#06x}"
        last = self.last
        effect = ',  '.join(last['changes']) or '(no visible change)'
        return (f"cycle {self.cycle:<5d} {last['pc']:#06x}  {last['text']:26s} {effect}\n"
                f"            next pc={self.pc:#06x}")
