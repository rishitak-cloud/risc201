"""
isa.py - the RISC201 instruction set (Member A)

RISC201 follows SimpleRisc from Sarangi, "Computer Organisation and
Architecture", Chapter 3. Everything else in the project imports its
encodings from here, so this file is the single place to change if the
professor's RISC201 differs from the book.

Machine facts
  * 16 registers r0..r15, 32 bits each. r14 = sp, r15 = ra.
  * flags register with two bits: E (equal) and GT (greater than),
    written only by cmp, read only by beq / bgt.
  * every instruction is 32 bits, memory is byte addressed,
    ld / st move one 32-bit word.

Instruction word (bit 31 on the left)

  Register format   | opcode(5) | I=0 | rd(4) | rs1(4) | rs2(4) | unused(14) |
  Immediate format  | opcode(5) | I=1 | rd(4) | rs1(4) | mod(2) | imm(16)    |
  Branch format     | opcode(5) |          offset (27 bits, in words)        |

  mod = 00 default: imm is sign-extended          (add  r1, r2, -5)
        01 'u'    : imm is zero-extended          (addu r1, r2, 0xFFFF)
        10 'h'    : imm goes to the upper 16 bits (movh r1, 0x1234)

  Branch target = PC of the branch + offset * 4
"""

# ----------------------------------------------------------------------------
# Opcodes (5 bits). Numbers follow the order of Sarangi's instruction table.
# ----------------------------------------------------------------------------
OPCODES = {
    'add': 0b00000, 'sub': 0b00001, 'mul': 0b00010, 'div': 0b00011,
    'mod': 0b00100, 'cmp': 0b00101, 'and': 0b00110, 'or':  0b00111,
    'not': 0b01000, 'mov': 0b01001, 'lsl': 0b01010, 'lsr': 0b01011,
    'asr': 0b01100, 'nop': 0b01101, 'ld':  0b01110, 'st':  0b01111,
    'beq': 0b10000, 'bgt': 0b10001, 'b':   0b10010, 'call': 0b10011,
    'ret': 0b10100,
    'hlt': 0b11111,   # NOT in SimpleRisc - our extension to stop the simulator
}
NAMES = {code: name for name, code in OPCODES.items()}

# Groups decide the operand syntax and the encoding format
THREE_ADDR = {'add', 'sub', 'mul', 'div', 'mod', 'and', 'or', 'lsl', 'lsr', 'asr'}
TWO_ADDR   = {'not', 'mov'}          # rd, rs2/imm
COMPARE    = {'cmp'}                 # rs1, rs2/imm
MEMORY     = {'ld', 'st'}            # rd, imm[rs1]
BRANCH     = {'beq', 'bgt', 'b', 'call'}
NO_OPERAND = {'nop', 'ret', 'hlt'}
ALU_TYPE   = THREE_ADDR | TWO_ADDR | COMPARE   # these accept the u / h suffix

SP = 14
RA = 15
REG_NAMES = {f'r{i}': i for i in range(16)}
REG_NAMES['sp'] = SP
REG_NAMES['ra'] = RA

MOD_DEFAULT, MOD_U, MOD_H = 0, 1, 2
MOD_SUFFIX = {MOD_DEFAULT: '', MOD_U: 'u', MOD_H: 'h'}

MASK32 = 0xFFFFFFFF


# ----------------------------------------------------------------------------
# Small number helpers
# ----------------------------------------------------------------------------
def to_signed(value, bits=32):
    """Read the low `bits` bits of value as a two's complement number."""
    value &= (1 << bits) - 1
    if value & (1 << (bits - 1)):
        return value - (1 << bits)
    return value


def to_unsigned(value, bits=32):
    return value & ((1 << bits) - 1)


def expand_immediate(imm16, mod):
    """Turn the 16-bit immediate into a 32-bit value using the modifier."""
    if mod == MOD_U:
        return imm16
    if mod == MOD_H:
        return (imm16 << 16) & MASK32
    return to_unsigned(to_signed(imm16, 16))      # default: sign extend


# ----------------------------------------------------------------------------
# Encoding
# ----------------------------------------------------------------------------
def encode(name, rd=0, rs1=0, rs2=0, imm=None, mod=MOD_DEFAULT, offset=0):
    """Build one 32-bit instruction word.
    imm=None means register format (I=0), anything else means I=1."""
    word = OPCODES[name] << 27
    if name in BRANCH:
        return word | to_unsigned(offset, 27)
    if name in NO_OPERAND:
        return word
    if imm is None:                                   # register format
        return word | (rd << 22) | (rs1 << 18) | (rs2 << 14)
    word |= 1 << 26                                   # I bit
    return word | (rd << 22) | (rs1 << 18) | (mod << 16) | to_unsigned(imm, 16)


def decode(word):
    """Split a 32-bit word into its fields. Returns a dict.
    Raises KeyError-free: an unknown opcode gives name=None."""
    opcode = (word >> 27) & 0x1F
    d = {
        'word': word,
        'opcode': opcode,
        'name': NAMES.get(opcode),
        'I': (word >> 26) & 1,
        'rd': (word >> 22) & 0xF,
        'rs1': (word >> 18) & 0xF,
        'rs2': (word >> 14) & 0xF,
        'mod': (word >> 16) & 0x3,
        'imm16': word & 0xFFFF,
        'offset': to_signed(word & 0x7FFFFFF, 27),
    }
    d['imm'] = expand_immediate(d['imm16'], d['mod'])
    return d


# ----------------------------------------------------------------------------
# Which registers does an instruction read / write?  (used for hazards)
# ----------------------------------------------------------------------------
def source_regs(d):
    name = d['name']
    regs = []
    if name in THREE_ADDR or name in COMPARE:
        regs.append(d['rs1'])
        if not d['I']:
            regs.append(d['rs2'])
    elif name in TWO_ADDR:
        if not d['I']:
            regs.append(d['rs2'])
    elif name == 'ld':
        regs.append(d['rs1'])
    elif name == 'st':
        regs += [d['rs1'], d['rd']]      # st reads rd: it is the value to store
    elif name == 'ret':
        regs.append(RA)
    return regs


def dest_reg(d):
    name = d['name']
    if name in THREE_ADDR or name in TWO_ADDR or name == 'ld':
        return d['rd']
    if name == 'call':
        return RA
    return None


# ----------------------------------------------------------------------------
# Turning a decoded instruction back into text (disassembler, CLI, traces)
# ----------------------------------------------------------------------------
def reg_name(r):
    return {SP: 'sp', RA: 'ra'}.get(r, f'r{r}')


def _imm_text(d):
    if d['mod'] == MOD_DEFAULT:
        return str(to_signed(d['imm16'], 16))
    return hex(d['imm16'])


def format_instr(d, pc=None, labels=None):
    """Assembly text for a decoded word. labels maps address -> name."""
    name = d['name']
    if name is None:
        return f".word {d['word']:#010x}"
    if name in NO_OPERAND:
        return name
    if name in BRANCH:
        if pc is None:
            return f"{name} {d['offset']:+d}"
        target = pc + d['offset'] * 4
        if labels and target in labels:
            return f"{name} {labels[target]}"
        return f"{name} {target:#06x}"
    if name in MEMORY:
        return f"{name} {reg_name(d['rd'])}, {to_signed(d['imm16'], 16)}[{reg_name(d['rs1'])}]"

    mnemonic = name + (MOD_SUFFIX[d['mod']] if d['I'] else '')
    second = _imm_text(d) if d['I'] else reg_name(d['rs2'])
    if name in THREE_ADDR:
        return f"{mnemonic} {reg_name(d['rd'])}, {reg_name(d['rs1'])}, {second}"
    if name in TWO_ADDR:
        return f"{mnemonic} {reg_name(d['rd'])}, {second}"
    return f"{mnemonic} {reg_name(d['rs1'])}, {second}"          # cmp
