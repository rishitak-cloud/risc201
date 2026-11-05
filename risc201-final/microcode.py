import itertools
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
    # floating point reuses the integer routines: only the ALU operation
    # differs, and ALU_OP takes the operation from the opcode
    'fadd': 'ALU', 'fsub': 'ALU', 'fmul': 'ALU', 'fdiv': 'ALU', 'froot': 'ALU',
    'itof': 'MOVNOT', 'ftoi': 'MOVNOT', 'fsqrt': 'MOVNOT', 'fcmp': 'CMP',
}

# ---------------------------------------------------------------------------
# MIXED (field-encoded) microcode: which signals can share an encoded field?
# ---------------------------------------------------------------------------
def code_bits(n):
    """Bits needed to name one of n signals, or 'none': n + 1 codes."""
    return n.bit_length()


def signal_conflicts(program=None):
    """Two signals CONFLICT if some word of the horizontal microprogram turns
    both on in the same cycle. Conflicting signals need separate fields;
    signals that never fire together can share one field."""
    program = HORIZONTAL if program is None else program
    conflicts = {s: set() for s in SIGNALS}
    for _, signals, _, _ in program:
        for a, b in itertools.combinations(signals, 2):
            conflicts[a].add(b)
            conflicts[b].add(a)
    return conflicts


def _bit_budgets(total, parts, largest=None):
    """All ways to split `total` bits into `parts` fields, biggest first."""
    largest = total if largest is None else largest
    if parts == 1:
        if 1 <= total <= largest:
            yield (total,)
        return
    for first in range(min(total - parts + 1, largest), 0, -1):
        for rest in _bit_budgets(total - first, parts - 1, first):
            yield (first,) + rest


def _fit(order, conflicts, capacities):
    """Try to put every signal in a field without breaking a conflict or a
    field's capacity. Simple backtracking. Returns the fields or None."""
    fields = [[] for _ in capacities]

    def place(i):
        if i == len(order):
            return True
        s = order[i]
        for f, cap in zip(fields, capacities):
            if len(f) < cap and not (conflicts[s] & set(f)):
                f.append(s)
                if place(i + 1):
                    return True
                f.pop()
        return False

    return fields if place(0) else None


_FIELDS_CACHE = []


def derive_fields():
    """Find the encoded fields for the mixed style, AUTOMATICALLY.

    Idea: try the smallest total number of signal bits first (1, 2, 3 ...).
    A field of b bits can name 2**b - 1 signals (code 0 = 'no signal').
    For every way of splitting that many bits into fields, check whether all
    18 signals fit without two conflicting signals in one field. The first
    split that works is the narrowest possible, so the ROM is as small as it
    can be for this microprogram. Returns a list of fields (lists of signal
    names)."""
    if _FIELDS_CACHE:
        return [list(f) for f in _FIELDS_CACHE[0]]
    conflicts = signal_conflicts()
    # hardest signals first: fails fast, finds a fit fast
    order = sorted(SIGNALS, key=lambda s: (-len(conflicts[s]), SIGNALS.index(s)))
    for total in range(1, len(SIGNALS) + 1):
        for parts in range(1, total + 1):                 # fewer fields = fewer decoders
            for budget in _bit_budgets(total, parts):
                capacities = [(1 << b) - 1 for b in budget]
                if sum(capacities) < len(SIGNALS):
                    continue
                fields = _fit(order, conflicts, capacities)
                if fields:
                    fields = [sorted(f, key=SIGNALS.index) for f in fields]
                    _FIELDS_CACHE.append(fields)
                    return [list(f) for f in fields]
    raise ValueError("no field assignment found")          # cannot happen: 18 fields always work


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

    STYLES = ('horizontal', 'vertical', 'mixed')

    def __init__(self, style):
        assert style in self.STYLES
        self.style = style
        # mixed runs the horizontal microprogram, only the encoding differs
        program = VERTICAL if style == 'vertical' else HORIZONTAL
        self.labels = {lab: i for i, (lab, _, _, _) in enumerate(program) if lab}
        self.dispatch = {op: self.labels[lab] for op, lab in DISPATCH.items()}
        self.source = program

        self.addr_bits = max(1, math.ceil(math.log2(len(program))))
        self.seq_bits = math.ceil(math.log2(len(SEQUENCES)))
        self.fields = None                    # only for mixed
        self.field_bits = None
        if style == 'horizontal':
            self.signal_bits = len(SIGNALS)                          # one bit each
        elif style == 'vertical':
            self.signal_bits = code_bits(len(SIGNALS))               # one code, 0 = none
        else:
            self.fields = derive_fields()
            self.field_bits = [code_bits(len(f)) for f in self.fields]
            self.signal_bits = sum(self.field_bits)
        self.width = self.signal_bits + self.seq_bits + self.addr_bits

        check_rom(program, style, self.fields)
        self.rom = [self.encode(sig, seq, self.labels.get(tgt, 0))
                    for (_, sig, seq, tgt) in program]

    # word layout:  | signals | sequencing | next address |
    #   mixed:      | field 1 | field 2 | ... | sequencing | next address |
    def encode(self, signals, seq, target):
        if self.style == 'horizontal':
            field = 0
            for s in signals:
                field |= 1 << (len(SIGNALS) - 1 - SIGNALS.index(s))
        elif self.style == 'vertical':
            field = SIGNALS.index(signals[0]) + 1 if signals else 0
        else:
            field = 0
            for members, bits in zip(self.fields, self.field_bits):
                code = 0                                    # 0 = this field is idle
                for s in signals:
                    if s in members:
                        code = members.index(s) + 1
                field = (field << bits) | code
        word = field
        word = (word << self.seq_bits) | SEQUENCES.index(seq)
        word = (word << self.addr_bits) | target
        return word

    def decode(self, word):
        """Integer microinstruction -> (signals, sequencing, target).
        For vertical / mixed this is the micro-operation DECODER. A bit
        pattern that names no signal / sequencing raises IndexError (the CPU
        turns that into a MicrocodeFault)."""
        target = word & ((1 << self.addr_bits) - 1)
        seq = SEQUENCES[(word >> self.addr_bits) & ((1 << self.seq_bits) - 1)]
        field = word >> (self.addr_bits + self.seq_bits)
        if self.style == 'horizontal':
            signals = [s for i, s in enumerate(SIGNALS)
                       if field & (1 << (len(SIGNALS) - 1 - i))]
        elif self.style == 'vertical':
            signals = [SIGNALS[field - 1]] if field else []
        else:
            signals, shift = [], self.signal_bits
            for members, bits in zip(self.fields, self.field_bits):
                shift -= bits
                code = (field >> shift) & ((1 << bits) - 1)
                if code:
                    signals.append(members[code - 1])       # IndexError if unused code
            signals.sort(key=SIGNALS.index)                 # apply in SIGNALS order
        return signals, seq, target

    def bit_meaning(self, bit):
        """What does bit number `bit` (0 = least significant) of a word do?
        Used by verify.py to explain injected faults."""
        if bit < self.addr_bits:
            return f"next-address bit {bit}"
        if bit < self.addr_bits + self.seq_bits:
            return f"sequencing bit {bit - self.addr_bits}"
        p = bit - self.addr_bits - self.seq_bits            # 0 = lowest signal bit
        if self.style == 'horizontal':
            return f"signal {SIGNALS[len(SIGNALS) - 1 - p]}"
        if self.style == 'vertical':
            return f"micro-op code bit {p}"
        low = 0                                             # last field sits lowest
        for n in range(len(self.fields), 0, -1):
            bits = self.field_bits[n - 1]
            if p < low + bits:
                return f"field {n} code bit {p - low}"
            low += bits
        return f"bit {bit}"

    def size_bits(self):
        return len(self.rom) * self.width

    def listing(self):
        names = {i: lab for lab, i in self.labels.items()}
        digits = (self.width + 3) // 4
        out = [f"{self.style} control memory: {len(self.rom)} words x {self.width} bits "
               f"= {self.size_bits()} bits"]
        if self.fields:
            out.append("  signal fields (signals that never fire together share a field):")
            for n, (members, bits) in enumerate(zip(self.fields, self.field_bits), start=1):
                out.append(f"    field {n}: {bits} bit{'s' if bits > 1 else ''}, "
                           f"{len(members)} signal{'s' if len(members) > 1 else ''}: "
                           f"{', '.join(members)}")
        for i, word in enumerate(self.rom):
            sig, seq, tgt = self.decode(word)
            nxt = seq if seq in ('NEXT', 'FETCH', 'DISPATCH') else \
                f"{seq} {names.get(tgt, tgt)}"
            out.append(f"  {i:3d} {names.get(i, ''):7s} {word:0{digits}x}  "
                       f"{', '.join(sig) or '-':32s} {nxt}")
        return '\n'.join(out)


def check_rom(program, style, fields=None):
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
        if fields:                       # mixed: one signal per field per word
            for members in fields:
                both = [x for x in signals if x in members]
                if len(both) > 1:
                    raise ValueError(f"micro word {i}: {both} share one encoded field")
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
    memories = [ControlMemory(style) for style in ControlMemory.STYLES]
    for cm in memories:
        print(cm.listing())
        print()
    print("design space (same instructions, three ways to store the control):")
    print(f"  {'style':11s} {'words':>5s} {'width':>6s} {'ROM bits':>9s}   decoder")
    for cm in memories:
        if cm.style == 'horizontal':
            decoder = 'none'
        elif cm.style == 'vertical':
            decoder = f"1 x {cm.signal_bits}-bit"
        else:
            decoder = ' + '.join(f"{b}-bit" for b in cm.field_bits)
        print(f"  {cm.style:11s} {len(cm.rom):5d} {cm.width:6d} {cm.size_bits():9d}   {decoder}")
