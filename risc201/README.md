# RISC201 — Processor Design (CSC-201)

Assembler, disassembler, 4-stage and 6-stage pipelines, horizontal and vertical microprogrammed control, ALU with swappable algorithms, stack macros, stack protection and an interactive debugger.

The ISA follows **SimpleRisc** (Sarangi, Ch. 3). Plain Python 3, no libraries needed.

## Quick start

```bash
python tests/test_all.py                              # 13 tests, all should pass
python bench.py                                       # full evaluation tables
python cli.py examples/fact.s                         # debugger, 6-stage pipeline
python cli.py examples/sum.s --mode horizontal        # microprogrammed CPU
python assembler.py examples/fact.s -l fact.lst       # -> fact.hex + listing
python disassembler.py examples/fact.hex
```

## Who owns what (Phase 1 = items 1 to 2d)

| Member | Items | Files | Diagram |
|---|---|---|---|
| A | 2a, 2b (+ ISA design) | `isa.py`, `assembler.py`, `disassembler.py` | `instruction_formats` |
| B | 1a | `pipeline.py`, `machine.py` | `pipeline_4stage`, `pipeline_6stage` |
| C | 1b | `microcode.py`, `microcpu.py` | `control_unit` |
| D | 1c, 2c, 2d | `alu.py`, `cli.py` | `alu` |

`bench.py` is shared: B presents the 4 vs 6-stage table, C the horizontal vs vertical table, D the ALU table.
Interfaces between the parts are in `docs/diagrams/ownership.png`.

Phase 2 (already built, not presented yet): `preprocessor.py`, `stackview.py`, stack checks in `machine.py` / `exceptions.py`.

## Assignment checklist

| Requirement | Where |
|---|---|
| 1a 4-stage & 6-stage design, compared | `pipeline.py`, DESIGN.md §1, diagrams |
| 1b microprogrammed control unit | `microcode.py`, DESIGN.md §2, `control_unit` diagram |
| 1c ALU | `alu.py`, DESIGN.md §3, `alu` diagram |
| 2a two-pass assembler → hex/binary | `assembler.py` |
| 2b disassembler | `disassembler.py` |
| 2c/2d CLI simulator + step debugger | `cli.py` |
| 2e push/pop macro preprocessor (full descending) | `preprocessor.py` |
| 2f stack bounds checks + runtime exceptions | `machine.py`, `exceptions.py` |
| 2g ASCII stack visualizer | `stackview.py` (`stack` command) |
| 3 execute from control memory, show microPC, both H and V | `microcpu.py`, `ucode` / `step` commands |
| 4 ALU class drives all arithmetic/shift/logic | `alu.py` (used by every model) |
| 5 evaluation: 4 vs 6 stage, H vs V, ALU algorithms | `bench.py`, `docs/results.txt` |

## Debugger commands

| Command | Does |
|---|---|
| `step [n]` / `s` | n clock cycles (pipeline) or n micro-steps |
| `next` / `n` | finish one more instruction |
| `continue` / `c` | run to hlt, breakpoint or exception |
| `break <label>` / `b` | stop when that instruction executes |
| `watch <reg\|addr>` | `continue` stops when it changes (`unwatch` removes) |
| `set r3 10` / `set mem <addr> <v>` | change a register or memory word |
| `regs` / `r` | registers and flags |
| `mem <addr> [n]` / `x` | memory words |
| `pipe` / `p` | contents of every stage (or micro-state) |
| `diag [n]` | pipeline diagram |
| `ucode` | control memory listing |
| `stack` | ASCII stack with frames |
| `dis` | disassembly with current position |
| `stats` | cycles, CPI, stalls, flushes, ALU work |
| `reset`, `quit` | |

Options: `--mode pipe4|pipe6|horizontal|vertical`, `--adder ripple|cla`, `--mul shiftadd|booth`, `--div restoring|nonrestoring`, `--no-guard`, `--run`.

## 5-minute demo script

1. **A:** `python assembler.py examples/fact.s -l fact.lst` → show listing, then `python disassembler.py examples/fact.hex`.
2. **B:** `python cli.py examples/hazard.s --mode pipe6` → `step 10`, `diag`. Same with `--mode pipe4`: no stall, one fewer flush.
3. **C:** `python cli.py examples/sum.s --mode vertical` → `ucode`, `next`, `next` (microPC trace). Mention horizontal's numbers.
4. **D:** `python cli.py examples/sum.s --mode pipe4` → `watch r1`, `c`, `regs`, `mem array 4`. Then `muldiv.s --run` with `--mul shiftadd` vs `--mul booth`.
5. Each member shows their table from `python bench.py`.

## Examples

| File | Shows | Result |
|---|---|---|
| `sum.s` | loop, load-use hazards | r1 = 55 |
| `fact.s` | recursion, call/ret, push/pop | r1 = 720 |
| `hazard.s` | every hazard type | r5 = 40 |
| `muldiv.s` | ALU ops, negatives, u/h modifiers | see file |
| `bubble.s` | sorting in memory | r9 = −15, r10 = 99 |
| `overflow.s` | StackOverflow | exception |
| `smash.s` | overwritten return address | ProtectionFault |
| `divzero.s` | DivideByZero | exception |

## Docs

- `docs/ISA.md` — instruction set, encodings, what may differ from the professor's RISC201
- `docs/DESIGN.md` — design choices + evaluation results
- `docs/VIVA.md` — per-member pitch and likely questions
- `docs/diagrams/` — datapaths, control unit, ALU, instruction formats, ownership (SVG + PNG)
