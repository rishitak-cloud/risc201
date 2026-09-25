"""
machine.py - the architectural state that every processor model shares
(Member B owns this file; the stack-guard checks inside it are Phase 2)

    Machine
      .regs    RegisterFile   16 x 32-bit, writes to sp are checked
      .flags   Flags          E and GT
      .mem     Memory         64 KB, word access, protection checks
      .alu     ALU
      .frames  list           one entry per active call (for the stack view)

Memory map (byte addresses)

    0x0000 ............ code_end    program (write protected)
    code_end .......... 0xF000      free / data
    0xF000 ............ 0x10000     stack, 4 KB
                        ^ stack limit    ^ stack base (sp starts here)

Stack safety rules (checked when guard=True)
    1. writing sp below STACK_LIMIT            -> StackOverflow
    2. writing sp above STACK_BASE             -> StackUnderflow
    3. sp not a multiple of 4                  -> AlignmentFault
    4. ld/st into the stack BELOW sp           -> StackAccessViolation
       ld/st just ABOVE the stack base         -> StackUnderflow (pop on empty stack)
       (that memory is not allocated yet; full-descending means the
        valid stack is sp .. base)
    5. ret to an address outside the code      -> ProtectionFault
       (catches a saved ra that was overwritten on the stack)
"""
import isa
from alu import ALU
from exceptions import (MemoryFault, AlignmentFault, ProtectionFault,
                        StackOverflow, StackUnderflow, StackAccessViolation)

MEM_SIZE = 0x10000
STACK_BASE = 0x10000
STACK_LIMIT = 0xF000


class Memory:
    def __init__(self, machine, size=MEM_SIZE):
        self.machine = machine
        self.size = size
        self.words = [0] * (size // 4)
        self.reads = 0
        self.writes = 0

    def _check(self, address, writing):
        pc = self.machine.pc_now
        if self.machine.guard and STACK_BASE <= address < STACK_BASE + 64:
            raise StackUnderflow(f"access to {address:#x}, above the stack base "
                                 f"(pop on an empty stack?)", pc)
        if not 0 <= address < self.size:
            raise MemoryFault(f"address {address:#x} is outside memory", pc)
        if address % 4:
            raise AlignmentFault(f"address {address:#x} is not word aligned", pc)
        if writing and address < self.machine.code_end:
            raise ProtectionFault(f"write to code region at {address:#x}", pc)
        if self.machine.guard and STACK_LIMIT <= address < STACK_BASE:
            sp = self.machine.regs.values[isa.SP]
            if address < sp:
                raise StackAccessViolation(
                    f"access to {address:#x} is below sp={sp:#x} (unallocated stack)", pc)

    def load(self, address):
        self._check(address, writing=False)
        self.reads += 1
        return self.words[address // 4]

    def store(self, address, value):
        self._check(address, writing=True)
        self.writes += 1
        self.words[address // 4] = value & isa.MASK32

    def peek(self, address):
        """Read without any checks (for the debugger display)."""
        if 0 <= address < self.size:
            return self.words[address // 4]
        return None


class RegisterFile:
    def __init__(self, machine):
        self.machine = machine
        self.values = [0] * 16
        self.values[isa.SP] = STACK_BASE
        self.writes = 0

    def read(self, r):
        return self.values[r]

    def write(self, r, value):
        value &= isa.MASK32
        if r == isa.SP and self.machine.guard:
            pc = self.machine.pc_now
            if value < STACK_LIMIT:
                raise StackOverflow(f"sp={value:#x} is below the stack limit {STACK_LIMIT:#x}", pc)
            if value > STACK_BASE:
                raise StackUnderflow(f"sp={value:#x} is above the stack base {STACK_BASE:#x}", pc)
            if value % 4:
                raise AlignmentFault(f"sp={value:#x} is not word aligned", pc)
        self.values[r] = value
        self.writes += 1


class Flags:
    def __init__(self):
        self.E = 0
        self.GT = 0

    def __str__(self):
        return f"E={self.E} GT={self.GT}"


class Machine:
    def __init__(self, program, alu=None, guard=True):
        """program: an assembler.Program (or anything with .words, .code_end,
        .symbols)."""
        self.guard = guard
        self.pc_now = None             # pc of the instruction doing the access
        self.code_end = 0              # protection off while loading
        self.mem = Memory(self)
        self.regs = RegisterFile(self)
        self.flags = Flags()
        self.alu = alu or ALU()
        self.symbols = dict(getattr(program, 'symbols', {}))
        self.labels = {a: n for n, a in self.symbols.items()}
        self.frames = []
        for i, w in enumerate(program.words):
            self.mem.store(4 * i, w)
        self.code_end = program.code_end
        self.program_words = list(program.words)

    def check_return(self, target, pc):
        """Rule 5: a return address must point into the code."""
        if self.guard and (target >= self.code_end or target % 4):
            raise ProtectionFault(
                f"ret to {target:#x}: not a code address - saved ra was corrupted?", pc)

    def note_call(self, pc, target):
        self.frames.append({'caller': pc, 'target': target,
                            'sp': self.regs.values[isa.SP]})

    def note_return(self):
        if self.frames:
            self.frames.pop()

    def state(self):
        """Registers + flags + memory writes: used to check that every
        processor model computes the same thing."""
        return (tuple(self.regs.values), self.flags.E, self.flags.GT,
                tuple(self.mem.words))


def load_file(path):
    """Build a Program from a .s, .hex or .bin file."""
    from assembler import assemble, Program
    from disassembler import read_words
    text = open(path).read()
    if path.endswith('.s') or path.endswith('.asm'):
        return assemble(text)
    words, code_end = read_words(text)
    prog = Program()
    prog.words = words
    prog.code_end = code_end
    return prog
