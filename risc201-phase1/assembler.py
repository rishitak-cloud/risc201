"""
assembler.py - two-pass assembler for RISC201 (Member A)

Pass 1: walk the program, give every instruction / data word an address,
        and record where every label is (the symbol table).
Pass 2: walk it again and turn each line into a 32-bit word. Now every
        label is known, so forward references (branching to a label that
        is defined later) work.

Syntax
    label:  add  r1, r2, r3        @ comment  (also ; and //)
            addu r1, r2, 0xFFFF    @ u = unsigned immediate
            movh r1, 0x1234        @ h = immediate into upper half
            ld   r1, 8[sp]         @ memory operand is imm[reg]
            mov  r2, array         @ a label can be used as an immediate
            .word 1, 2, 0x10       @ raw data words
            .space 16              @ 16 zero bytes

Usage
    python assembler.py prog.s              -> prog.hex
    python assembler.py prog.s --bin        -> prog.bin
    python assembler.py prog.s -l prog.lst  -> also write a listing
"""
import sys
import isa


class AsmError(Exception):
    def __init__(self, line_no, message):
        super().__init__(f"line {line_no}: {message}")
        self.line_no = line_no


class Program:
    """What the assembler produces."""
    def __init__(self):
        self.words = []          # the memory image, one int per 4 bytes
        self.symbols = {}        # label -> address
        self.code_end = 0        # address just after the last INSTRUCTION
        self.listing = []        # (address, word, source text)
        self.lines = {}          # address -> original source line number


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def strip_comment(text):
    for marker in ('@', ';', '//'):
        text = text.split(marker, 1)[0]
    return text.strip()


def split_label(text):
    """'loop: add r1, r1, 1' -> ('loop', 'add r1, r1, 1')"""
    if ':' in text:
        label, rest = text.split(':', 1)
        label = label.strip()
        if not label.isidentifier():
            return None, text
        return label, rest.strip()
    return None, text


def split_operands(text):
    """'r1, 4[sp]' -> ['r1', '4[sp]']"""
    return [p.strip() for p in text.split(',') if p.strip()]


def parse_mnemonic(word):
    """'addu' -> ('add', MOD_U).  Returns (name, modifier)."""
    word = word.lower()
    if word in isa.OPCODES:
        return word, isa.MOD_DEFAULT
    base, suffix = word[:-1], word[-1:]
    if base in isa.ALU_TYPE and suffix in ('u', 'h'):
        return base, isa.MOD_U if suffix == 'u' else isa.MOD_H
    return None, None


def item_size(line_no, text):
    """How many bytes a line takes (pass 1 only needs this)."""
    if not text:
        return 0
    first = text.split(None, 1)[0].lower()
    if first == '.word':
        return 4 * len(split_operands(text.split(None, 1)[1]))
    if first == '.space':
        n = int(text.split(None, 1)[1], 0)
        if n % 4:
            raise AsmError(line_no, ".space must be a multiple of 4")
        return n
    return 4                                         # every instruction


# ----------------------------------------------------------------------------
# Operand parsers (pass 2)
# ----------------------------------------------------------------------------
def parse_reg(line_no, text):
    r = text.strip().lower()
    if r not in isa.REG_NAMES:
        raise AsmError(line_no, f"'{text}' is not a register")
    return isa.REG_NAMES[r]


def parse_value(line_no, text, symbols):
    text = text.strip()
    if text in symbols:
        return symbols[text]
    try:
        return int(text, 0)                          # 12, -3, 0x1F, 0b101
    except ValueError:
        raise AsmError(line_no, f"unknown value or label '{text}'")


def check_imm(line_no, value, mod):
    if mod == isa.MOD_DEFAULT:
        if not -32768 <= value <= 32767:
            raise AsmError(line_no, f"immediate {value} does not fit in 16 signed bits "
                                    f"(use the u or h form, e.g. movu / movh)")
    elif not 0 <= value <= 0xFFFF:
        raise AsmError(line_no, f"immediate {value} does not fit in 16 unsigned bits")


def is_register(text):
    return text.strip().lower() in isa.REG_NAMES


def encode_line(line_no, text, address, symbols):
    """Pass 2 for one instruction. Returns the 32-bit word."""
    parts = text.split(None, 1)
    name, mod = parse_mnemonic(parts[0])
    if name is None:
        raise AsmError(line_no, f"unknown instruction '{parts[0]}'")
    ops = split_operands(parts[1]) if len(parts) > 1 else []

    def need(n):
        if len(ops) != n:
            raise AsmError(line_no, f"'{name}' needs {n} operand(s), got {len(ops)}")

    def reg_or_imm(text_op):
        """Last operand of ALU instructions: register or immediate."""
        if is_register(text_op):
            if mod != isa.MOD_DEFAULT:
                raise AsmError(line_no, "u / h suffix only makes sense with an immediate")
            return {'rs2': parse_reg(line_no, text_op)}
        value = parse_value(line_no, text_op, symbols)
        check_imm(line_no, value, mod)
        return {'imm': value, 'mod': mod}

    if name in isa.NO_OPERAND:
        need(0)
        return isa.encode(name)

    if name in isa.BRANCH:
        need(1)
        target = parse_value(line_no, ops[0], symbols)
        if target % 4:
            raise AsmError(line_no, "branch target must be word aligned")
        return isa.encode(name, offset=(target - address) // 4)

    if name in isa.THREE_ADDR:
        need(3)
        return isa.encode(name, rd=parse_reg(line_no, ops[0]),
                          rs1=parse_reg(line_no, ops[1]), **reg_or_imm(ops[2]))

    if name in isa.TWO_ADDR:
        need(2)
        return isa.encode(name, rd=parse_reg(line_no, ops[0]), **reg_or_imm(ops[1]))

    if name in isa.COMPARE:
        need(2)
        return isa.encode(name, rs1=parse_reg(line_no, ops[0]), **reg_or_imm(ops[1]))

    # ld / st :  rd, imm[rs1]
    need(2)
    mem = ops[1].replace(' ', '')
    if not (mem.endswith(']') and '[' in mem):
        raise AsmError(line_no, "memory operand must look like 8[r2]")
    imm_text, reg_text = mem[:-1].split('[', 1)
    imm = parse_value(line_no, imm_text, symbols) if imm_text else 0
    check_imm(line_no, imm, isa.MOD_DEFAULT)
    return isa.encode(name, rd=parse_reg(line_no, ops[0]),
                      rs1=parse_reg(line_no, reg_text), imm=imm)


# ----------------------------------------------------------------------------
# The two passes
# ----------------------------------------------------------------------------
def assemble(source):
    lines = list(enumerate(source.splitlines(), start=1))

    # ---------------- pass 1: addresses and labels ----------------
    symbols = {}
    items = []                         # (line_no, address, text) with code
    address = 0
    for line_no, raw in lines:
        label, text = split_label(strip_comment(raw))
        if label:
            if label in symbols:
                raise AsmError(line_no, f"label '{label}' defined twice")
            if label in isa.REG_NAMES or label.lower() in isa.OPCODES:
                raise AsmError(line_no, f"'{label}' is reserved and can't be a label")
            symbols[label] = address
        if text:
            items.append((line_no, address, text))
            address += item_size(line_no, text)

    # ---------------- pass 2: encode ----------------
    prog = Program()
    prog.symbols = symbols
    for line_no, address, text in items:
        first = text.split(None, 1)[0].lower()
        if first == '.word':
            words = [isa.to_unsigned(parse_value(line_no, v, symbols))
                     for v in split_operands(text.split(None, 1)[1])]
        elif first == '.space':
            words = [0] * (int(text.split(None, 1)[1], 0) // 4)
        elif first.startswith('.'):
            raise AsmError(line_no, f"unknown directive '{first}'")
        else:
            words = [encode_line(line_no, text, address, symbols)]
            prog.code_end = address + 4
        for i, w in enumerate(words):
            prog.words.append(w)
            prog.lines[address + 4 * i] = line_no
            prog.listing.append((address + 4 * i, w, text if i == 0 else ''))
    return prog


# ----------------------------------------------------------------------------
# Output formats
# ----------------------------------------------------------------------------
def to_hex(prog):
    lines = [f"# code_end {prog.code_end:#x}"]
    lines += [f"{w:08x}" for w in prog.words]
    return '\n'.join(lines) + '\n'


def to_bin(prog):
    lines = [f"# code_end {prog.code_end:#x}"]
    lines += [f"{w:032b}" for w in prog.words]
    return '\n'.join(lines) + '\n'


def to_listing(prog):
    names = {a: n for n, a in prog.symbols.items()}
    out = ["address   word        source", "-" * 50]
    for address, word, text in prog.listing:
        label = f"{names[address]}:" if address in names else ''
        out.append(f"{address:#06x}    {word:08x}    {label:10s}{text}")
    out.append("")
    out.append("symbol table")
    for name, address in sorted(prog.symbols.items(), key=lambda kv: kv[1]):
        out.append(f"  {name:12s} {address:#06x}")
    return '\n'.join(out) + '\n'


def main(argv):
    if not argv:
        print(__doc__)
        return 1
    src = argv[0]
    out = None
    use_bin = '--bin' in argv
    listing = None
    if '-o' in argv:
        out = argv[argv.index('-o') + 1]
    if '-l' in argv:
        listing = argv[argv.index('-l') + 1]
    if out is None:
        out = src.rsplit('.', 1)[0] + ('.bin' if use_bin else '.hex')

    try:
        prog = assemble(open(src).read())
    except AsmError as e:
        print(f"{src}: error: {e}")
        return 1

    with open(out, 'w') as f:
        f.write(to_bin(prog) if use_bin else to_hex(prog))
    if listing:
        with open(listing, 'w') as f:
            f.write(to_listing(prog))
    print(f"{src} -> {out}  ({len(prog.words)} words, {len(prog.symbols)} labels)")
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
