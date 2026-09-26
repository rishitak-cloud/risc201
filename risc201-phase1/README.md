# RISC201 — Processor Design (CSC-201) — Phase 1

**First evaluation scope: PDF items 1 to 2d only.** Stack macros (2e), stack protection (2f), the stack visualizer (2g) and running from microcode (3) are deliberately left out and come in the next phase.

The ISA follows **SimpleRisc** (Sarangi, Ch. 3). Plain Python 3, no libraries needed.

## Quick start

```bash
python tests/test_all.py                          # 13 tests, all should pass
python bench.py                                   # evaluation tables
python cli.py examples/fact.s --mode single       # single-cycle CPU
python cli.py examples/fact.s                     # 6-stage pipeline (default)
python cli.py examples/hazard.s --mode pipe4      # 4-stage pipeline
python microcode.py                               # both control memories
python assembler.py examples/fact.s -l fact.lst   # -> fact.hex + listing
python disassembler.py examples/fact.hex
```

## Who owns what

| Member | PDF items | Files | Diagram |
|---|---|---|---|
| A | 2a, 2b (+ ISA design) | `isa.py`, `assembler.py`, `disassembler.py` | `instruction_formats` |
| B | 1a, 2c (pipeline simulation) | `pipeline.py`, `machine.py` | `pipeline_4stage`, `pipeline_6stage` |
| C | 1b (design), 2d (CPU simulator) | `microcode.py`, `cpu.py` | `control_unit` |
| D | 1c, 2c (register view, CLI), 2d (debugger) | `alu.py`, `cli.py` | `alu` |

`bench.py` is shared: B presents the processor comparison table, C explains horizontal vs vertical, D the ALU table.
Interfaces between the parts: `docs/diagrams/ownership.png`.

## Assignment checklist (Phase 1)

| Requirement | Where |
|---|---|
| 1a 4-stage & 6-stage design, compared | `pipeline.py`, DESIGN.md §1, diagrams |
| 1b microprogrammed control unit (design) | `microcode.py`, DESIGN.md §2, `control_unit` diagram |
| 1c ALU | `alu.py`, DESIGN.md §3, `alu` diagram |
| 2a two-pass assembler → hex/binary | `assembler.py` |
| 2b disassembler | `disassembler.py` |
| 2c CLI showing the register file and pipeline | `cli.py` (`regs`, `pipe`, `diag`) |
| 2d step-by-step CPU simulator + debugger | `cpu.py`, `cli.py` |

**Later:** 2e `preprocessor.py`, 2f stack checks, 2g `stackview.py`, 3 `microcpu.py`, 5 full evaluation.

## Debugger commands

| Command | Does |
|---|---|
| `step [n]` / `s` | n clock cycles (pipeline) or n instructions (single-cycle) |
| `next` / `n` | finish one more instruction |
| `continue` / `c` | run to hlt, breakpoint, watch change or exception |
| `break <label>` / `b` | stop when that instruction executes |
| `watch <reg\|addr>` | `continue` stops when it changes (`unwatch` removes) |
| `set r3 10` / `set mem <addr> <v>` | change a register or memory word |
| `regs` / `r` | registers and flags |
| `mem <addr> [n]` / `x` | memory words |
| `pipe` / `p` | contents of every stage |
| `diag [n]` | pipeline diagram |
| `dis` | disassembly with current position |
| `stats` | cycles, CPI, stalls, flushes, ALU work |
| `reset`, `quit` | |

Options: `--mode single|pipe4|pipe6`, `--adder ripple|cla`, `--mul shiftadd|booth`, `--div restoring|nonrestoring`, `--run`.

## 5-minute demo script

1. **A:** `python assembler.py examples/fact.s -l fact.lst` → show the listing, then `python disassembler.py examples/fact.hex`.
2. **B:** `python cli.py examples/hazard.s --mode pipe6` → `step 10`, `diag`. Same with `--mode pipe4`: no stall, one fewer flush.
3. **C:** `python cli.py examples/fact.s --mode single` → `step 5`. Then `python microcode.py` → explain one instruction in horizontal vs vertical.
4. **D:** `python cli.py examples/sum.s --mode pipe4` → `watch r1`, `c`, `regs`, `mem array 4`. Then `muldiv.s --run` with `--mul shiftadd` vs `--mul booth`.
5. Each member shows their table from `python bench.py`.

## Examples

| File | Shows | Result |
|---|---|---|
| `sum.s` | loop, load-use hazards | r1 = 55 |
| `fact.s` | recursion, call/ret, stack by hand | r1 = 720 |
| `hazard.s` | every hazard type | r5 = 40 |
| `muldiv.s` | ALU ops, negatives, u/h modifiers | see file |
| `bubble.s` | sorting in memory | r9 = −15, r10 = 99 |
| `divzero.s` | DivideByZero | exception |

## Docs

- `docs/ISA.md` — instruction set, encodings, what may differ from the professor's RISC201
- `docs/DESIGN.md` — design choices + evaluation results
- `docs/VIVA.md` — per-member pitch and likely questions
- `docs/diagrams/` — datapaths, control unit, ALU, instruction formats, ownership (SVG + PNG)
