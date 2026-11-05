import sys
import isa
from machine import STACK_BASE, STACK_LIMIT

GREEN, YELLOW, RED, DIM, BOLD = '\033[32m', '\033[33m', '\033[31m', '\033[2m', '\033[1m'
RESET = '\033[0m'


def _paint(text, style, on):
    return f"{style}{text}{RESET}" if on else text


def render(machine, max_rows=24, color=None):
    if color is None:
        color = sys.stdout.isatty()

    sp = machine.regs.values[isa.SP]
    used = STACK_BASE - sp
    size = STACK_BASE - STACK_LIMIT
    bar_len = 30

    # ---- what is wrong with the stack pointer, if anything? ----
    problem = None
    if sp > STACK_BASE:
        problem = (f"STACK UNDERFLOW: sp is {sp - STACK_BASE} bytes above the base "
                   f"(more pops than pushes)")
    elif sp < STACK_LIMIT:
        problem = (f"STACK OVERFLOW: sp is {STACK_LIMIT - sp} bytes below the limit "
                   f"(too many pushes, or recursion without an end)")
    elif sp % 4:
        problem = "WARNING: sp is not word aligned (should be a multiple of 4)"

    if size and 0 < used <= size:
        filled = min(bar_len, round(bar_len * used / size))
    else:
        filled = bar_len if used > size else 0

    out = [f"stack  base={STACK_BASE:#x}  limit={STACK_LIMIT:#x}  sp={sp:#x}",
           f"used {max(used, 0)} of {size} bytes  [{'#' * filled}{'.' * (bar_len - filled)}]"]
    if problem:
        out.append(_paint(problem, RED + BOLD, color))
    out.append("")

    if sp > STACK_BASE or used == 0:
        out.append("  (stack is empty)")
        return '\n'.join(out)

    # ---- sp at each call -> frame label. Words just below that sp belong to it ----
    starts = {}
    for n, fr in enumerate(machine.frames, start=1):
        name = machine.labels.get(fr['target'], f"{fr['target']:#06x}")
        starts[fr['sp']] = f"frame {n}: called from {fr['caller']:#06x} -> {name}"
    return_addresses = {fr['caller'] + 4: fr['caller'] for fr in machine.frames}

    # never read outside the real stack area, even when sp has run past the limit
    low = max(sp - sp % 4, STACK_LIMIT)
    addresses = list(range(STACK_BASE - 4, low - 4, -4))
    hidden = 0
    if len(addresses) > max_rows:                       # keep the newest words
        hidden = len(addresses) - max_rows
        addresses = addresses[hidden:]
        out.append(f"   ... {hidden} older words not shown ...")

    bar = "               +------------+"
    out.append("   address     value")
    out.append(_paint(bar, DIM, color))
    for addr in addresses:
        if addr + 4 in starts and addr + 4 != STACK_BASE:
            out.append(_paint(bar, DIM, color))
        value = machine.mem.peek(addr)
        notes = [starts.get(addr + 4, '')]
        is_return = value in return_addresses
        if is_return:
            notes.append(f"return address (to {value:#06x})")
        if addr == sp - sp % 4:
            notes.insert(0, '<- sp')
        row = f"   {addr:#010x} | {value:#010x} |  " + '  '.join(n for n in notes if n)
        if addr == sp - sp % 4:
            row = _paint(row, GREEN + BOLD, color)
        elif is_return:
            row = _paint(row, YELLOW, color)
        out.append(row.rstrip())
    out.append(_paint(bar, DIM, color))
    if sp < STACK_LIMIT:
        out.append(_paint("   ==== stack limit reached; sp is further down ====", RED, color))
    return '\n'.join(out)
