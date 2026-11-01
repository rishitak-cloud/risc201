"""
disassembler.py - machine code back to assembly (Member A)

Reads a .hex file (8 hex digits per line) or a .bin file (32 bits per
line), decodes every word with isa.decode and prints assembly.

Branch targets become labels L0, L1, ... so the output can be fed back
into the assembler (round trip: .s -> .hex -> .s -> .hex gives the same
words; see tests).

Usage
    python disassembler.py prog.hex
"""
import sys
import isa


def read_words(text):
    """Parse hex or binary word strings. Returns (words, code_end)."""
    words, code_end = [], None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith('# code_end'):
            code_end = int(line.split()[-1], 0)
            continue
        if not line or line.startswith('#'):
            continue
        if len(line) == 32 and set(line) <= {'0', '1'}:
            words.append(int(line, 2))
        else:
            words.append(int(line.replace('0x', ''), 16))
    if code_end is None:
        code_end = 4 * len(words)
    return words, code_end


def disassemble(words, code_end=None):
    """Returns a list of text lines."""
    if code_end is None:
        code_end = 4 * len(words)

    # 1. find every branch target and give it a label
    labels = {}
    for i, w in enumerate(words[:code_end // 4]):
        d = isa.decode(w)
        if d['name'] in isa.BRANCH:
            target = 4 * i + 4 * d['offset']
            labels.setdefault(target, f"L{len(labels)}")

    # 2. print each word
    out = []
    for i, w in enumerate(words):
        address = 4 * i
        if address in labels:
            out.append(f"{labels[address]}:")
        if address < code_end:
            text = isa.format_instr(isa.decode(w), pc=address, labels=labels)
        else:
            text = f".word {w:#010x}"            # data after the code
        out.append(f"    {text:30s} @ {address:#06x}: {w:08x}")
    return out


def main(argv):
    if not argv:
        print(__doc__)
        return 1
    words, code_end = read_words(open(argv[0]).read())
    print('\n'.join(disassemble(words, code_end)))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
