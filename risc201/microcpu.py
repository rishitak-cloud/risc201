"""
microcpu.py - the microprogrammed (non-pipelined) processor (Member C)

This is Sarangi's microprogrammed processor idea (Chapter 8): the
datapath is a set of registers joined by buses, and the CONTROL comes
entirely from the control memory in microcode.py. There is no
"if opcode == add" anywhere in the control path - the only place an
opcode is looked at is the DISPATCH table.

One call to micro_step() = one clock cycle = one microinstruction:
    1. word   = control_memory[microPC]
    2. decode the word into signals + sequencing (vertical needs a
       decoder here; horizontal signals are just bits)
    3. work out the next microPC from values at the START of the cycle
    4. perform every signal (register transfers)
    5. microPC = next
"""
import isa
from microcode import ControlMemory
from exceptions import MicrocodeFault, IllegalInstruction, MemoryFault


class MicroCPU:
    def __init__(self, machine, style='horizontal'):
        self.m = machine
        self.cm = ControlMemory(style)
        self.style = style
        self.upc = 0
        # internal datapath registers (invisible to the programmer)
        self.pc = 0
        self.IR = 0
        self.A = self.B = self.ALU = self.MAR = self.MDR = 0
        self.fields = None
        # counters
        self.cycle = 0            # micro-cycles = clock cycles
        self.retired = 0
        self.halted = False
        self.trace = []           # recent micro-steps for the CLI
        self.breakpoints = set()
        self.hit_breakpoint = None
        self.instr_pc = 0         # pc of the instruction being executed

    # ------------------------------------------------------------------
    # The datapath: what each control signal does
    # ------------------------------------------------------------------
    def perform(self, signal):
        m, f = self.m, self.fields
        if signal == 'IR_FETCH':
            if not 0 <= self.pc < m.code_end:
                raise MemoryFault(f"instruction fetch from {self.pc:#x}, outside the code",
                                  self.pc)
            self.IR = m.mem.load(self.pc)
        elif signal == 'DECODE':
            self.fields = isa.decode(self.IR)
        elif signal == 'A_RS1':
            self.A = m.regs.read(f['rs1'])
        elif signal == 'B_OPND':
            self.B = f['imm'] if f['I'] else m.regs.read(f['rs2'])
        elif signal == 'MDR_RD':
            self.MDR = m.regs.read(f['rd'])
        elif signal == 'ALU_OP':
            self.ALU = m.alu.compute(f['name'], self.A, self.B, self.instr_pc)
        elif signal == 'ALU_ADD':
            self.ALU = m.alu.add(self.A, self.B)
        elif signal == 'FLAGS_CMP':
            m.flags.E, m.flags.GT = m.alu.compare(self.A, self.B)
        elif signal == 'MAR_ALU':
            self.MAR = self.ALU
        elif signal == 'MEM_READ':
            self.MDR = m.mem.load(self.MAR)
        elif signal == 'MEM_WRITE':
            m.mem.store(self.MAR, self.MDR)
        elif signal == 'RD_ALU':
            m.regs.write(f['rd'], self.ALU)
        elif signal == 'RD_MDR':
            m.regs.write(f['rd'], self.MDR)
        elif signal == 'RA_LINK':
            m.regs.write(isa.RA, self.pc + 4)
            m.note_call(self.pc, self.pc + 4 * f['offset'])
        elif signal == 'PC_INC':
            self.pc += 4
        elif signal == 'PC_BRANCH':
            self.pc = self.pc + 4 * f['offset']
        elif signal == 'PC_RA':
            target = m.regs.read(isa.RA)
            m.check_return(target, self.instr_pc)
            self.pc = target
            m.note_return()
        elif signal == 'HALT':
            self.halted = True

    # ------------------------------------------------------------------
    # The sequencer: next microPC
    # ------------------------------------------------------------------
    def next_upc(self, seq, target):
        if seq == 'NEXT':
            return self.upc + 1
        if seq == 'JUMP':
            return target
        if seq == 'FETCH':
            return 0
        if seq == 'IF_E':
            return target if self.m.flags.E else self.upc + 1
        if seq == 'IF_GT':
            return target if self.m.flags.GT else self.upc + 1
        if seq == 'DISPATCH':
            opcode = (self.IR >> 27) & 0x1F           # opcode bits straight from IR
            name = isa.NAMES.get(opcode)
            if name not in self.cm.dispatch:
                raise IllegalInstruction(f"opcode {opcode:#07b} has no micro-routine",
                                         self.instr_pc)
            return self.cm.dispatch[name]
        raise MicrocodeFault(f"bad sequencing field {seq}", self.instr_pc)

    # ------------------------------------------------------------------
    # One micro-cycle
    # ------------------------------------------------------------------
    def micro_step(self):
        if self.halted:
            return None
        if not 0 <= self.upc < len(self.cm.rom):
            raise MicrocodeFault(f"microPC {self.upc} is outside control memory",
                                 self.instr_pc)
        if self.upc == 0:                              # a new instruction starts
            self.instr_pc = self.pc
            self.m.pc_now = self.pc

        word = self.cm.rom[self.upc]
        signals, seq, target = self.cm.decode(word)
        before = self.upc
        self.m.pc_now = self.instr_pc
        nxt = self.next_upc(seq, target)               # uses start-of-cycle values
        for s in signals:
            self.perform(s)
        self.cycle += 1
        self.upc = nxt
        if seq == 'FETCH':
            self.retired += 1
            if self.pc in self.breakpoints and not self.halted:
                self.hit_breakpoint = self.pc

        step = {'cycle': self.cycle, 'upc': before, 'word': word, 'signals': signals,
                'seq': seq, 'next': nxt, 'pc': self.instr_pc}
        self.trace.append(step)
        self.trace = self.trace[-40:]
        return step

    def step(self):
        """One clock cycle (same name as Pipeline.step, used by the CLI)."""
        self.hit_breakpoint = None
        self.micro_step()

    def step_instruction(self):
        """Run micro-steps until this instruction is finished."""
        self.hit_breakpoint = None
        self.micro_step()
        while self.upc != 0 and not self.halted:
            self.micro_step()

    def run(self, max_cycles=2000000):
        while not self.halted:
            if self.cycle >= max_cycles:
                raise RuntimeError(f"no hlt after {max_cycles} micro-cycles (infinite loop?)")
            self.micro_step()

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------
    def stats(self):
        return {
            'cycles': self.cycle,
            'instructions': self.retired,
            'CPI': round(self.cycle / self.retired, 3) if self.retired else 0,
            'rom_words': len(self.cm.rom),
            'rom_width': self.cm.width,
            'rom_bits': self.cm.size_bits(),
        }

    def format_step(self, st):
        names = {i: lab for lab, i in self.cm.labels.items()}
        digits = (self.cm.width + 3) // 4
        where = names.get(st['upc'], '')
        return (f"cyc {st['cycle']:5d}  uPC {st['upc']:3d} {where:7s} "
                f"[{st['word']:0{digits}x}]  {', '.join(st['signals']) or '-':28s} "
                f"{st['seq']:8s} -> uPC {st['next']}")

    def show(self):
        f = self.fields
        text = isa.format_instr(f, self.instr_pc, self.m.labels) if f else '-'
        lines = [f"{self.style} microprogrammed CPU   micro-cycle {self.cycle}",
                 f"  microPC = {self.upc}    PC = {self.pc:#06x}    current/last instruction: {text}",
                 f"  IR={self.IR:08x}  A={self.A:08x}  B={self.B:08x}  ALU={self.ALU:08x}",
                 f"  MAR={self.MAR:08x}  MDR={self.MDR:08x}  flags: {self.m.flags}"]
        if self.trace:
            lines.append("  last micro-steps:")
            for st in self.trace[-6:]:
                lines.append("    " + self.format_step(st))
        return '\n'.join(lines)
