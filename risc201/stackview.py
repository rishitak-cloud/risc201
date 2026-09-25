"""
stackview.py - ASCII picture of the stack (Phase 2 - owner not assigned yet)

Draws memory from the stack base (top of the picture, high addresses)
down to sp (bottom), because our stack is full descending: pushes move
sp DOWN, and sp points at the last word pushed.

Each call starts a new frame; the machine remembers the sp at every
call (Machine.frames), which is where the frame lines are drawn.

    0x0000fffc | 0x00000008 |   frame 1: called from 0x0004 -> fact
    0x0000fff8 | 0x00000006 |
               +------------+
    0x0000fff4 | 0x0000001c |   frame 2: called from 0x0020 -> fact
    0x0000fff0 | 0x00000005 | <- sp
"""
import isa
from machine import STACK_BASE, STACK_LIMIT


def render(machine, max_rows=24):
    sp = machine.regs.values[isa.SP]
    used = STACK_BASE - sp
    size = STACK_BASE - STACK_LIMIT
    bar_len = 30
    filled = min(bar_len, round(bar_len * used / size)) if size else 0

    out = [f"stack  base={STACK_BASE:#x}  limit={STACK_LIMIT:#x}  sp={sp:#x}",
           f"used {used} of {size} bytes  [{'#' * filled}{'.' * (bar_len - filled)}]",
           ""]
    if used <= 0:
        out.append("  (stack is empty)")
        return '\n'.join(out)

    # sp at each call -> frame label. Words just below that sp belong to it.
    starts = {}
    for n, fr in enumerate(machine.frames, start=1):
        name = machine.labels.get(fr['target'], f"{fr['target']:#06x}")
        starts[fr['sp']] = f"frame {n}: called from {fr['caller']:#06x} -> {name}"

    addresses = list(range(STACK_BASE - 4, sp - 4, -4))
    hidden = 0
    if len(addresses) > max_rows:                       # keep the newest words
        hidden = len(addresses) - max_rows
        addresses = addresses[hidden:]
        out.append(f"   ... {hidden} older words not shown ...")

    out.append("   address     value")
    out.append("               +------------+")
    for addr in addresses:
        if addr + 4 in starts and addr + 4 != STACK_BASE:
            out.append("               +------------+")
        note = starts.get(addr + 4, '')
        value = machine.mem.peek(addr)
        if addr == sp:
            note = ('<- sp   ' + note).strip()
        out.append(f"   {addr:#010x} | {value:#010x} |  {note}")
    out.append("               +------------+")
    return '\n'.join(out)
