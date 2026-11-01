# RISC201 — Processor Design (CSC-201)

**Complete: every item of the assignment PDF (1a–1c, 2a–2g, 3, 4, 5), plus integer overflow handling and IEEE 754 floating point.**

The ISA follows **SimpleRisc** (Sarangi, Ch. 3); the ALU algorithms follow Sarangi Ch. 8. Plain Python 3, no libraries needed.

## Quick start

```bash
python tests/test_all.py                          # 24 tests, all should pass
python bench.py                                   # full evaluation (item 5)
python cli.py examples/fact.s --mode single       # single-cycle CPU
python cli.py examples/fact.s                     # 6-stage pipeline (default)
python cli.py examples/hazard.s --mode pipe4      # 4-stage pipeline
python cli.py examples/sum.s --mode horizontal    # microprogrammed, horizontal
python cli.py examples/sum.s --mode vertical      # microprogrammed, vertical
python cli.py examples/smash.s --run              # stack corruption caught
python microcode.py                               # both control memories
python alu.py                                     # ALU self-check + delay table
python fpu.py                                     # FPU demo: rounding, overflow, NaN, denormals
python cli.py examples/fp.s --mode single         # floating point; type c, then fregs
python cli.py examples/ovf.s --run --trap-overflow  # overflow trap
python assembler.py examples/fact.s -l fact.lst   # -> fact.hex + listing
python disassembler.py examples/fact.hex
```

## Who owns what

| Member | PDF items | Files | Diagram |
|---|---|---|---|
| A | 2a, 2b, 2e, 2g (+ ISA design) | `isa.py`, `assembler.py`, `disassembler.py`, `preprocessor.py`, `stackview.py` | `instruction_formats` |
| B | 1a, 2c (pipeline sim), 2f | `pipeline.py`, `machine.py`, `exceptions.py` | `pipeline_4stage`, `pipeline_6stage` |
| C | 1b, 2d (CPU simulator), 3 | `microcode.py`, `microcpu.py`, `cpu.py` | `control_unit` |
| D | 1c, 2c (CLI), 2d (debugger), 4, overflow, FP | `alu.py`, `fpu.py`, `cli.py` | `alu` |
| all | 5 | `bench.py`, `examples/` — each member presents their own section | `ownership` |

## Assignment checklist

| PDF item | Where |
|---|---|
| 1a 4-stage & 6-stage design, compared | `pipeline.py`, DESIGN §1, two datapath diagrams |
| 1b microprogrammed control unit | `microcode.py`, DESIGN §2, `control_unit` diagram |
| 1c ALU with efficient arithmetic/logic | `alu.py`, DESIGN §3, `alu` diagram |
| 2a two-pass assembler → binary/hex | `assembler.py` (`--bin` for binary) |
| 2b disassembler | `disassembler.py` |
| 2c CLI: register file, pipeline | `cli.py` — `regs`, `pipe`, `diag` |
| 2d step-by-step simulator + CLI debugger | `cpu.py`, `cli.py` — `step`, `next`, `break`, `watch`, `set`, `mem` |
| 2e stack macro preprocessor (full descending) | `preprocessor.py` — `push` / `pop` |
| 2f stack bounds + runtime exceptions | `machine.py`, `exceptions.py` |
| 2g ASCII stack frame renderer | `stackview.py` — `stack` |
| 3 execute from control memory, H and V, show microPC, exceptions | `microcpu.py` — `--mode horizontal\|vertical`, `step`, `next`, `upc`, `ucode` |
| 4 ALU class drives all arithmetic, shift, logic | `alu.py`, used by all five models |
| 5 evaluation: stages, programs, H vs V, ALU algorithms | `bench.py`, `docs/results.txt`, DESIGN §6 |
| *Extra (asked at mid-eval)*: integer overflow | `alu.py` — V flag, `--trap-overflow`, DESIGN §3 |
| *Extra (asked at mid-eval)*: IEEE 754 floating point | `fpu.py`, 7 new instructions, DESIGN §3, ISA.md |

## The five processor models

| Mode | Model | One `step` = |
|---|---|---|
| `single` | single-cycle CPU | one instruction |
| `pipe4` | IF · OF · EX+MA · RW | one clock cycle |
| `pipe6` | IF · ID · OF · EX · MA · RW | one clock cycle |
| `horizontal` | microprogrammed, horizontal microcode | one micro-cycle |
| `vertical` | microprogrammed, vertical microcode | one micro-cycle |

## Debugger commands

| Command | Does |
|---|---|
| `step [n]` / `s` | n steps (see table above) |
| `next` / `n` | finish one more instruction (micro modes print every micro-step) |
| `continue` / `c` | run to hlt, breakpoint, watch change or exception |
| `break <label>` / `b` | stop when that instruction executes |
| `watch <reg\|addr>` | `continue` stops when it changes (`unwatch` removes) |
| `set r3 10` / `set mem <addr> <v>` | change a register or memory word |
| `regs` / `r` | registers and flags (E, GT, V) |
| `fregs` | registers read as floats, plus IEEE status flags |
| `mem <addr> [n]` / `x` | memory words |
| `pipe` / `p` | every pipeline stage, or the microprogram state |
| `diag [n]` | pipeline timing diagram |
| `upc` | microPC, internal registers, last micro-steps |
| `ucode` | control memory listing |
| `stack` | ASCII stack with frame boundaries |
| `dis` | disassembly with current position |
| `stats` | cycles, CPI, stalls, flushes, ALU work |
| `reset`, `quit` | |

Options: `--mode single|pipe4|pipe6|horizontal|vertical`, `--adder ripple|cselect|cla`, `--mul iterative|booth`, `--div restoring|nonrestoring`, `--trap-overflow`, `--round nearest|zero|up|down`, `--fdiv restoring|newton|goldschmidt`, `--no-guard`, `--run`.

## Demo script (final evaluation)

1. **A:** `python assembler.py examples/fact.s -l fact.lst` → `push {r2, ra}` became three instructions. Then `python disassembler.py examples/fact.hex`. Then `fact.s` → `break done`, `c`, `stack` (six frames).
2. **B:** `hazard.s --mode pipe6` → `step 10`, `diag`; then `--mode pipe4`. Then `smash.s --run` (caught) and `--no-guard` (jumps to 0xBEEF).
3. **C:** `sum.s --mode vertical` → `ucode`, `next`, `next`, `upc`; then `--mode horizontal`. Show `bench.py` section 3.
4. **D:** `matmul.s --run --mul iterative` vs `--mul booth`. `ovf.s --run`, then with `--trap-overflow`. `fp.s` → `c`, `fregs`. Show `python fpu.py`.
5. **Everyone:** `python bench.py` section 7 — all 150 runs agree.

## Examples

| File | Shows | Result |
|---|---|---|
| `sum.s` | loop, load-use hazards | r1 = 55 |
| `fact.s` | recursion, call/ret, push/pop, 6 stack frames | r1 = 720 |
| `hazard.s` | every hazard type | r5 = 40 |
| `muldiv.s` | every ALU op, negatives, u/h modifiers | see file |
| `bubble.s` | sorting in memory | r9 = −15, r10 = 99 |
| `fib.s` | ALU-only dependence chains | r1 = 6765 |
| `gcd.s` | division-heavy | r1 = 21 |
| `matmul.s` | multiply-heavy, negatives, memory | r10 = −14, r11 = −54 |
| `fp.s` | every float instruction, rounding error, overflow to ∞ | see `fregs` |
| `ovf.s` | integer overflow: wrap vs trap | r2 = −2³¹, V = 1 |
| `overflow.s` | runaway recursion | StackOverflow |
| `smash.s` | overwritten return address | ProtectionFault |
| `divzero.s` | divide by zero | DivideByZero |

## Docs

- `docs/ISA.md` — instruction set and encodings
- `docs/DESIGN.md` — design decisions and every evaluation result
- `docs/VIVA.md` — per-member pitch and likely questions
- `docs/results.txt` — raw `bench.py` output
- `docs/diagrams/` — datapaths, control unit, ALU, instruction formats, ownership
