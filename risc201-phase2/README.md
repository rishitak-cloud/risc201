# RISC201 — Processor Design (CSC-201) — Phase 1 + 2

**Evaluation scope: PDF items 1 to 2g.** Running from control memory (item 3) and the full evaluation (item 5) come in the final phase. The control unit **design** (1b) is complete.

The ISA follows **SimpleRisc** (Sarangi, Ch. 3). Plain Python 3, no libraries needed.

## Quick start

```bash
python tests/test_all.py                          # 14 tests, all should pass
python bench.py                                   # evaluation tables
python cli.py examples/fact.s --mode single       # single-cycle CPU
python cli.py examples/fact.s                     # 6-stage pipeline (default)
python cli.py examples/hazard.s --mode pipe4      # 4-stage pipeline
python cli.py examples/smash.s --run              # stack corruption caught
python microcode.py                               # both control memories
python assembler.py examples/fact.s -l fact.lst   # -> fact.hex + listing
python disassembler.py examples/fact.hex
```

## Who owns what

| Member | PDF items | Files | Diagram |
|---|---|---|---|
| A | 2a, 2b, 2e (+ ISA design) | `isa.py`, `assembler.py`, `disassembler.py`, `preprocessor.py` | `instruction_formats` |
| B | 1a, 2c (pipeline sim), 2f | `pipeline.py`, `machine.py`, `exceptions.py` | `pipeline_4stage`, `pipeline_6stage` |
| C | 1b (design), 2d (CPU simulator) | `microcode.py`, `cpu.py` | `control_unit` |
| D | 1c, 2c (register view, CLI), 2d (debugger), 2g | `alu.py`, `cli.py`, `stackview.py` | `alu` |

`bench.py` is shared: B presents the processor comparison, C explains horizontal vs vertical, D the ALU table.

## Assignment checklist

| Requirement | Where | Status |
|---|---|---|
| 1a 4-stage & 6-stage design, compared | `pipeline.py`, DESIGN §1, diagrams | done |
| 1b microprogrammed control unit (design) | `microcode.py`, DESIGN §2, diagram | done |
| 1c ALU | `alu.py`, DESIGN §3, diagram | done |
| 2a two-pass assembler → hex/binary | `assembler.py` | done |
| 2b disassembler | `disassembler.py` | done |
| 2c CLI showing register file and pipeline | `cli.py` (`regs`, `pipe`, `diag`) | done |
| 2d step-by-step CPU simulator + debugger | `cpu.py`, `cli.py` | done |
| 2e stack macro preprocessor (full descending) | `preprocessor.py` | done |
| 2f stack bounds + runtime exceptions | `machine.py`, `exceptions.py` | done |
| 2g ASCII stack visualizer | `stackview.py` (`stack` command) | done |
| 3 execute from control memory, show microPC | — | next phase |
| 5 full evaluation incl. H vs V timing | `bench.py` (partial) | next phase |

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
| `stack` | ASCII stack with frame boundaries |
| `pipe` / `p` | contents of every stage |
| `diag [n]` | pipeline diagram |
| `dis` | disassembly with current position |
| `stats` | cycles, CPI, stalls, flushes, ALU work |
| `reset`, `quit` | |

Options: `--mode single|pipe4|pipe6`, `--adder ripple|cla`, `--mul shiftadd|booth`, `--div restoring|nonrestoring`, `--no-guard`, `--run`.

## Demo script

1. **A:** `python assembler.py examples/fact.s -l fact.lst` → show how `push {r2, ra}` became three instructions in the listing. Then `python disassembler.py examples/fact.hex`.
2. **B:** `python cli.py examples/hazard.s --mode pipe6` → `step 10`, `diag`; same with `--mode pipe4`. Then `python cli.py examples/smash.s --run` (caught), and `--no-guard` (jumps to 0xBEEF).
3. **C:** `python cli.py examples/fact.s --mode single` → `step 5`. Then `python microcode.py` → one instruction in horizontal vs vertical.
4. **D:** `python cli.py examples/fact.s` → `break done`, `c`, `stack` (six nested frames). Then `muldiv.s --run` with `--mul shiftadd` vs `--mul booth`.
5. Each member shows their table from `python bench.py`.

## Examples

| File | Shows | Result |
|---|---|---|
| `sum.s` | loop, load-use hazards | r1 = 55 |
| `fact.s` | recursion, call/ret, push/pop macros, 6 stack frames | r1 = 720 |
| `hazard.s` | every hazard type | r5 = 40 |
| `muldiv.s` | ALU ops, negatives, u/h modifiers | see file |
| `bubble.s` | sorting in memory | r9 = −15, r10 = 99 |
| `overflow.s` | runaway recursion | StackOverflow |
| `smash.s` | overwritten return address | ProtectionFault |
| `divzero.s` | divide by zero | DivideByZero |

## Docs

- `docs/ISA.md` — instruction set, encodings, what may differ from the professor's RISC201
- `docs/DESIGN.md` — design choices + evaluation results
- `docs/VIVA.md` — per-member pitch and likely questions
- `docs/diagrams/` — datapaths, control unit, ALU, instruction formats, ownership (SVG + PNG)
