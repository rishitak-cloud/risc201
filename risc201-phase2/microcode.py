"""
microcode.py - the control memory (Member C)

This file is the DESIGN of the control unit (assignment item 1b).
A microprogrammed processor has no hardwired control: every clock cycle
it reads ONE microinstruction from control memory at address microPC,
turns on the control signals written in it, and picks the next microPC.
(Sarangi Chapter 8.)

Run `python microcode.py` to print both control memories. Actually
EXECUTING a program from this ROM is assignment item 3, next phase.

A microinstruction has two parts:
  * control signals  - which register transfers happen this cycle
  * sequencing       - how to get the next microPC

Control signals (the datapath has registers A, B, ALU, MAR, MDR, IR, PC):

  IR_FETCH   IR  <- MEM[PC]
  DECODE     split IR into fields (rd, rs1, rs2, imm, offset)
  A_RS1      A   <- R[rs1]
  B_OPND     B   <- imm if I=1 else R[rs2]      (the immediate mux)
  MDR_RD     MDR <- R[rd]                        (value to store)
  ALU_OP     ALU <- A (op) B, op comes from the opcode
  ALU_ADD    ALU <- A + B                        (ld / st address)
  FLAGS_CMP  flags <- compare(A, B)
  MAR_ALU    MAR <- ALU
  MEM_READ   MDR <- MEM[MAR]
  MEM_WRITE  MEM[MAR] <- MDR
  RD_ALU     R[rd] <- ALU
  RD_MDR     R[rd] <- MDR
  RA_LINK    R[15] <- PC + 4
  PC_INC     PC  <- PC + 4
  PC_BRANCH  PC  <- PC + offset * 4
  PC_RA      PC  <- R[15]
  HALT       stop the machine

Sequencing:
  NEXT      microPC + 1
  JUMP L    go to label L
  DISPATCH  go to the routine for this opcode (the "mswitch" in Sarangi)
  IF_E L    go to L if flags.E = 1, else NEXT
  IF_GT L   go to L if flags.GT = 1, else NEXT
  FETCH     back to microPC 0 (start the next instruction)

HORIZONTAL vs VERTICAL
  Horizontal: one bit per control signal. Many signals can be on in the
              same microinstruction -> fewer micro-steps, WIDE words.
  Vertical:   one ENCODED micro-operation per microinstruction; a small
              decoder turns the code into a signal -> NARROW words,
              more micro-steps per instruction.

Rule inside one horizontal word: all signals happen at the same clock
edge, so each one sees the values from the START of the cycle. So a word
must not need a value that another signal of the same word produces
(e.g. ALU_OP together with A_RS1 is illegal: the ALU would need the new A).
The simulator applies signals in SIGNALS order; check_rom() makes sure no
signal reads anything written by an earlier one, which gives exactly the
hardware behaviour.
"""
import math

SIGNALS = ['IR_FETCH', 'DECODE', 'A_RS1', 'B_OPND', 'MDR_RD', 'ALU_OP',
           'ALU_ADD', 'FLAGS_CMP', 'MAR_ALU', 'MEM_READ', 'MEM_WRITE',
           'RD_ALU', 'RD_MDR', 'RA_LINK', 'PC_INC', 'PC_BRANCH', 'PC_RA', 'HALT']

SEQUENCES = ['NEXT', 'JUMP', 'DISPATCH', 'IF_E', 'IF_GT', 'FETCH']

# what each signal reads and writes (for check_rom)
READS = {
    'IR_FETCH': {'PC', 'MEM'}, 'DECODE': {'IR'}, 'A_RS1': {'FIELDS', 'REGS'},
    'B_OPND': {'FIELDS', 'REGS'}, 'MDR_RD': {'FIELDS', 'REGS'},
    'ALU_OP': {'A', 'B', 'FIELDS'}, 'ALU_ADD': {'A', 'B'}, 'FLAGS_CMP': {'A', 'B'},
    'MAR_ALU': {'ALU'}, 'MEM_READ': {'MAR', 'MEM'}, 'MEM_WRITE': {'MAR', 'MDR'},
    'RD_ALU': {'ALU', 'FIELDS'}, 'RD_MDR': {'MDR', 'FIELDS'}, 'RA_LINK': {'PC'},
    'PC_INC': {'PC'}, 'PC_BRANCH': {'PC', 'FIELDS'}, 'PC_RA': {'REGS'}, 'HALT': set(),
}
WRITES = {
    'IR_FETCH': {'IR'}, 'DECODE': {'FIELDS'}, 'A_RS1': {'A'}, 'B_OPND': {'B'},
    'MDR_RD': {'MDR'}, 'ALU_OP': {'ALU'}, 'ALU_ADD': {'ALU'}, 'FLAGS_CMP': {'FLAGS'},
    'MAR_ALU': {'MAR'}, 'MEM_READ': {'MDR'}, 'MEM_WRITE': {'MEM'}, 'RD_ALU': {'REGS'},
    'RD_MDR': {'REGS'}, 'RA_LINK': {'REGS'}, 'PC_INC': {'PC'}, 'PC_BRANCH': {'PC'},
    'PC_RA': {'PC'}, 'HALT': set(),
}

# which routine each opcode starts at (the dispatch table)
DISPATCH = {
    'add': 'ALU', 'sub': 'ALU', 'mul': 'ALU', 'div': 'ALU', 'mod': 'ALU',
    'and': 'ALU', 'or': 'ALU', 'lsl': 'ALU', 'lsr': 'ALU', 'asr': 'ALU',
    'not': 'MOVNOT', 'mov': 'MOVNOT', 'cmp': 'CMP', 'ld': 'LD', 'st': 'ST',
    'beq': 'BEQ', 'bgt': 'BGT', 'b': 'TAKEN', 'call': 'CALL', 'ret': 'RET',
    'nop': 'NOP', 'hlt': 'HLT',
}

# ---------------------------------------------------------------------------
# Microprograms, written as (label, [signals], sequencing, target label)
# ---------------------------------------------------------------------------
HORIZONTAL = [
    ('FETCH',  ['IR_FETCH'],                     'NEXT',     None),
    (None,     ['DECODE'],                       'DISPATCH', None),
    # add sub mul div mod and or lsl lsr asr
    ('ALU',    ['A_RS1', 'B_OPND'],              'NEXT',     None),
    ('ALU_GO', ['ALU_OP'],                       'NEXT',     None),
    (None,     ['RD_ALU', 'PC_INC'],             'FETCH',    None),
    # mov / not only need B, then share the ALU routine
    ('MOVNOT', ['B_OPND'],                       'JUMP',     'ALU_GO'),
    ('CMP',    ['A_RS1', 'B_OPND'],              'NEXT',     None),
    (None,     ['FLAGS_CMP', 'PC_INC'],          'FETCH',    None),
    ('LD',     ['A_RS1', 'B_OPND'],              'NEXT',     None),
    (None,     ['ALU_ADD'],                      'NEXT',     None),
    (None,     ['MAR_ALU'],                      'NEXT',     None),
    (None,     ['MEM_READ'],                     'NEXT',     None),
    (None,     ['RD_MDR', 'PC_INC'],             'FETCH',    None),
    ('ST',     ['A_RS1', 'B_OPND', 'MDR_RD'],    'NEXT',     None),
    (None,     ['ALU_ADD'],                      'NEXT',     None),
    (None,     ['MAR_ALU'],                      'NEXT',     None),
    (None,     ['MEM_WRITE', 'PC_INC'],          'FETCH',    None),
    ('BEQ',    [],                               'IF_E',     'TAKEN'),
    (None,     ['PC_INC'],                       'FETCH',    None),
    ('BGT',    [],                               'IF_GT',    'TAKEN'),
    (None,     ['PC_INC'],                       'FETCH',    None),
    ('TAKEN',  ['PC_BRANCH'],                    'FETCH',    None),
    ('CALL',   ['RA_LINK', 'PC_BRANCH'],         'FETCH',    None),
    ('RET',    ['PC_RA'],                        'FETCH',    None),
    ('NOP',    ['PC_INC'],                       'FETCH',    None),
    ('HLT',    ['HALT'],                         'FETCH',    None),
]

VERTICAL = [
    ('FETCH',  ['IR_FETCH'],  'NEXT',     None),
    (None,     ['DECODE'],    'DISPATCH', None),
    ('ALU',    ['A_RS1'],     'NEXT',     None),
    (None,     ['B_OPND'],    'NEXT',     None),
    ('ALU_GO', ['ALU_OP'],    'NEXT',     None),
    (None,     ['RD_ALU'],    'NEXT',     None),
    (None,     ['PC_INC'],    'FETCH',    None),
    ('MOVNOT', ['B_OPND'],    'JUMP',     'ALU_GO'),
    ('CMP',    ['A_RS1'],     'NEXT',     None),
    (None,     ['B_OPND'],    'NEXT',     None),
    (None,     ['FLAGS_CMP'], 'NEXT',     None),
    (None,     ['PC_INC'],    'FETCH',    None),
    ('LD',     ['A_RS1'],     'NEXT',     None),
    (None,     ['B_OPND'],    'NEXT',     None),
    (None,     ['ALU_ADD'],   'NEXT',     None),
    (None,     ['MAR_ALU'],   'NEXT',     None),
    (None,     ['MEM_READ'],  'NEXT',     None),
    (None,     ['RD_MDR'],    'NEXT',     None),
    (None,     ['PC_INC'],    'FETCH',    None),
    ('ST',     ['A_RS1'],     'NEXT',     None),
    (None,     ['B_OPND'],    'NEXT',     None),
    (None,     ['MDR_RD'],    'NEXT',     None),
    (None,     ['ALU_ADD'],   'NEXT',     None),
    (None,     ['MAR_ALU'],   'NEXT',     None),
    (None,     ['MEM_WRITE'], 'NEXT',     None),
    (None,     ['PC_INC'],    'FETCH',    None),
    ('BEQ',    [],            'IF_E',     'TAKEN'),
    (None,     ['PC_INC'],    'FETCH',    None),
    ('BGT',    [],            'IF_GT',    'TAKEN'),
    (None,     ['PC_INC'],    'FETCH',    None),
    ('TAKEN',  ['PC_BRANCH'], 'FETCH',    None),
    ('CALL',   ['RA_LINK'],   'NEXT',     None),
    (None,     ['PC_BRANCH'], 'FETCH',    None),
    ('RET',    ['PC_RA'],     'FETCH',    None),
    ('NOP',    ['PC_INC'],    'FETCH',    None),
    ('HLT',    ['HALT'],      'FETCH',    None),
]


# ---------------------------------------------------------------------------
# Turning a microprogram into control memory (a list of integers)
# ---------------------------------------------------------------------------
class ControlMemory:
    """Holds the ROM as integers plus what is needed to decode them."""

    def __init__(self, style):
        assert style in ('horizontal', 'vertical')
        self.style = style
        program = HORIZONTAL if style == 'horizontal' else VERTICAL
        self.labels = {lab: i for i, (lab, _, _, _) in enumerate(program) if lab}
        self.dispatch = {op: self.labels[lab] for op, lab in DISPATCH.items()}
        self.source = program

        self.addr_bits = max(1, math.ceil(math.log2(len(program))))
        self.seq_bits = math.ceil(math.log2(len(SEQUENCES)))
        if style == 'horizontal':
            self.signal_bits = len(SIGNALS)                          # one bit each
        else:
            self.signal_bits = math.ceil(math.log2(len(SIGNALS) + 1))  # code, 0 = none
        self.width = self.signal_bits + self.seq_bits + self.addr_bits

        check_rom(program, style)
        self.rom = [self.encode(sig, seq, self.labels.get(tgt, 0))
                    for (_, sig, seq, tgt) in program]

    # word layout:  | signals | sequencing | next address |
    def encode(self, signals, seq, target):
        if self.style == 'horizontal':
            field = 0
            for s in signals:
                field |= 1 << (len(SIGNALS) - 1 - SIGNALS.index(s))
        else:
            field = SIGNALS.index(signals[0]) + 1 if signals else 0
        word = field
        word = (word << self.seq_bits) | SEQUENCES.index(seq)
        word = (word << self.addr_bits) | target
        return word

    def decode(self, word):
        """Integer microinstruction -> (signals, sequencing, target).
        For vertical this is the micro-operation DECODER."""
        target = word & ((1 << self.addr_bits) - 1)
        seq = SEQUENCES[(word >> self.addr_bits) & ((1 << self.seq_bits) - 1)]
        field = word >> (self.addr_bits + self.seq_bits)
        if self.style == 'horizontal':
            signals = [s for i, s in enumerate(SIGNALS)
                       if field & (1 << (len(SIGNALS) - 1 - i))]
        else:
            signals = [SIGNALS[field - 1]] if field else []
        return signals, seq, target

    def size_bits(self):
        return len(self.rom) * self.width

    def listing(self):
        names = {i: lab for lab, i in self.labels.items()}
        digits = (self.width + 3) // 4
        out = [f"{self.style} control memory: {len(self.rom)} words x {self.width} bits "
               f"= {self.size_bits()} bits"]
        for i, word in enumerate(self.rom):
            sig, seq, tgt = self.decode(word)
            nxt = seq if seq in ('NEXT', 'FETCH', 'DISPATCH') else \
                f"{seq} {names.get(tgt, tgt)}"
            out.append(f"  {i:3d} {names.get(i, ''):7s} {word:0{digits}x}  "
                       f"{', '.join(sig) or '-':32s} {nxt}")
        return '\n'.join(out)


def check_rom(program, style):
    """Catch microcode mistakes before running anything."""
    labels = {lab for lab, _, _, _ in program if lab}
    for i, (lab, signals, seq, target) in enumerate(program):
        for s in signals:
            if s not in SIGNALS:
                raise ValueError(f"micro word {i}: unknown signal {s}")
        if seq not in SEQUENCES:
            raise ValueError(f"micro word {i}: unknown sequencing {seq}")
        if seq in ('JUMP', 'IF_E', 'IF_GT') and target not in labels:
            raise ValueError(f"micro word {i}: jump to unknown label {target}")
        if style == 'vertical' and len(signals) > 1:
            raise ValueError(f"micro word {i}: vertical words hold one micro-op")
        written = set()
        for s in sorted(signals, key=SIGNALS.index):
            if READS[s] & written:
                raise ValueError(f"micro word {i}: {s} reads {READS[s] & written}, "
                                 f"which another signal in the same word writes")
            if WRITES[s] & written:
                raise ValueError(f"micro word {i}: two signals write {WRITES[s] & written}")
            written |= WRITES[s]
    missing = set(DISPATCH.values()) - labels
    if missing:
        raise ValueError(f"dispatch table points at missing routines {missing}")


if __name__ == '__main__':
    for style in ('horizontal', 'vertical'):
        print(ControlMemory(style).listing())
        print()
