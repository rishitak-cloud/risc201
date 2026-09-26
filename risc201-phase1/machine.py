"""
machine.py - the architectural state that every processor model shares
(Member B)

    Machine
      .regs    RegisterFile   16 x 32-bit
      .flags   Flags          E and GT
      .mem     Memory         64 KB, word access
      .alu     ALU

Memory map (byte addresses)

    0x0000 ............ code_end    program (write protected)
    code_end .......... 0xF000      free / data
    0xF000 ............ 0x10000     stack, 4 KB (sp starts at the top)

Memory rules checked on every access
    1. address inside memory                  -> else MemoryFault
    2. address a multiple of 4                -> else AlignmentFault
    3. no writing into the program itself     -> else ProtectionFault

(Stack bound checking is item 2f and comes later.)
"""
import isa
from alu import ALU
from exceptions import MemoryFault, AlignmentFault, ProtectionFault

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
        if not 0 <= address < self.size:
            raise MemoryFault(f"address {address:#x} is outside memory", pc)
        if address % 4:
            raise AlignmentFault(f"address {address:#x} is not word aligned", pc)
        if writing and address < self.machine.code_end:
            raise ProtectionFault(f"write to code region at {address:#x}", pc)

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
        self.values[r] = value & isa.MASK32
        self.writes += 1


class Flags:
    def __init__(self):
        self.E = 0
        self.GT = 0

    def __str__(self):
        return f"E={self.E} GT={self.GT}"


class Machine:
    def __init__(self, program, alu=None):
        """program: an assembler.Program (or anything with .words, .code_end,
        .symbols)."""
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
        """A return address must point into the code."""
        if target >= self.code_end or target % 4:
            raise ProtectionFault(
                f"ret to {target:#x}: not a code address", pc)

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
