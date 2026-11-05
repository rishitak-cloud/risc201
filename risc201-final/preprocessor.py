import struct


class MacroError(Exception):
    pass


def _parse_reg_list(text, line_no):
    text = text.strip()
    if text.startswith('{'):
        if not text.endswith('}'):
            raise MacroError(f"line {line_no}: missing '}}' in register list")
        text = text[1:-1]
    regs = [r.strip() for r in text.split(',') if r.strip()]
    if not regs:
        raise MacroError(f"line {line_no}: push/pop needs at least one register")
    return regs


def _load_constant(op, operands, line_no):
    ops = [o.strip() for o in operands.split(',')]
    if len(ops) != 2:
        raise MacroError(f"line {line_no}: {op} needs a register and a value")
    reg, text = ops
    try:
        if op == 'li':
            value = int(text, 0) & 0xFFFFFFFF
        else:
            value = struct.unpack('<I', struct.pack('<f', float(text)))[0]
    except (ValueError, OverflowError):
        raise MacroError(f"line {line_no}: bad value '{text}' for {op}")
    return [f"    movh {reg}, {value >> 16:#x}",
            f"    oru {reg}, {reg}, {value & 0xFFFF:#x}"]


def expand_macros(source):
    """source: the whole .s file as a string.
    Returns a list of (original_line_number, line_text)."""
    out = []
    for line_no, raw in enumerate(source.splitlines(), start=1):
        code = raw
        for marker in ('@', ';', '//'):             # drop comments
            code = code.split(marker, 1)[0]

        label = ''
        if ':' in code:                              # keep a label on the line
            label, code = code.split(':', 1)
            label = label.strip() + ':'

        parts = code.strip().split(None, 1)
        if not parts or parts[0].lower() not in ('push', 'pop', 'li', 'lif'):
            out.append((line_no, raw))
            continue

        op = parts[0].lower()
        if op in ('li', 'lif'):
            if label:
                out.append((line_no, label))
            out += [(line_no, t) for t in _load_constant(op, parts[1] if len(parts) > 1 else '',
                                                          line_no)]
            continue
        regs = _parse_reg_list(parts[1] if len(parts) > 1 else '', line_no)
        n = len(regs)
        if label:
            out.append((line_no, label))

        if op == 'push':
            out.append((line_no, f"    sub sp, sp, {4 * n}"))
            for i, r in enumerate(regs):
                out.append((line_no, f"    st {r}, {4 * (n - 1 - i)}[sp]"))
        else:
            for i, r in enumerate(regs):
                out.append((line_no, f"    ld {r}, {4 * (n - 1 - i)}[sp]"))
            out.append((line_no, f"    add sp, sp, {4 * n}"))
    return out


if __name__ == '__main__':
    import sys
    text = open(sys.argv[1]).read()
    for num, line in expand_macros(text):
        print(f"{num:4d} | {line}")
