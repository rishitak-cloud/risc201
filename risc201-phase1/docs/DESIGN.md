# Design and evaluation

All numbers below come from `python bench.py` (full output in `results.txt`).

---

## 1. Processors (Member B pipelines, Member C single-cycle) — `pipeline.py`, `cpu.py`

Diagrams: `diagrams/pipeline_4stage.png`, `diagrams/pipeline_6stage.png`

### Where the stages come from

Sarangi's 5-stage pipeline is **IF OF EX MA RW**. We derived both designs from it:

| Design | Stages | How |
|---|---|---|
| 4-stage | IF · OF · EX+MA · RW | merge EX and MA |
| 6-stage | IF · ID · OF · EX · MA · RW | split OF into decode (ID) and register read (OF) |

### Hazard handling

| Hazard | Rule | 4-stage | 6-stage |
|---|---|---|---|
| RAW, ALU → ALU | forwarding from MA / RW latch | no stall | no stall |
| Load-use | `ld` data ready at end of MA | **no stall** (MA is inside EX) | **1 stall** |
| Taken branch | decided in EX, flush everything before EX | **2 flushed** | **3 flushed** |

Forwarding is `Pipeline.read_operand()`: newest value from the MA latch, else RW latch, else register file.
The load-use check is at the end of `Pipeline.step()`: `ld` in EX and its destination used by the instruction in OF → stall.

### Results

| Program | single cycles | 4-stage | 6-stage | 4-stage CPI | 6-stage CPI |
|---|---|---|---|---|---|
| sum | 64 | 85 | 106 | 1.33 | 1.66 |
| fact | 79 | 118 | 143 | 1.49 | 1.81 |
| hazard | 9 | 14 | 19 | 1.56 | 2.11 |
| bubble | 375 | 504 | 605 | 1.34 | 1.61 |

The single-cycle CPU (Member C, `cpu.py`) has CPI = 1 but the longest clock (1000 ps: one cycle must fit fetch + decode + register read + ALU + memory + write back), so it is the slowest of the three in real time: bubble.s takes 375 ns.

**The 6-stage always needs more cycles** (load-use stalls + 1 extra flush per taken branch).

**But its clock is faster.** With assumed stage delays (IF 200, ID 100, OF 150, EX 250, MA 200, RW 100 ps, latch 20 ps):

| | Slowest stage | Clock period | bubble.s time |
|---|---|---|---|
| 4-stage | EX+MA = 450 ps | 470 ps | 236.9 ns |
| 6-stage | EX = 250 ps | 270 ps | **163.3 ns** |

So: time = instructions × CPI × clock period. The 6-stage loses on CPI and wins on period.

---

## 2. Microprogrammed control unit — design only (Member C) — `microcode.py`

Diagram: `diagrams/control_unit.png`

A non-pipelined processor whose control comes from **control memory** instead of hardwired logic (Sarangi Ch. 8). Each clock cycle: read the microinstruction at **microPC**, turn on its control signals, compute the next microPC.

- 18 control signals (register transfers like `A_RS1`, `ALU_OP`, `MEM_READ`, `PC_INC`)
- 6 sequencing modes: NEXT, JUMP, DISPATCH (jump by opcode), IF_E, IF_GT, FETCH
- microinstruction layout: `| signals | sequencing | next address |`

| | Horizontal | Vertical |
|---|---|---|
| Signal field | 1 bit per signal (18 bits) | encoded micro-op (5 bits) |
| Signals per word | many | one |
| Needs a decoder | no | yes |
| Control memory | 26 words × 26 bits = **676 bits** | 36 words × 14 bits = **504 bits** |
| Micro-cycles for `add` | **5** | 7 |

Horizontal is faster, vertical is smaller.

`check_rom()` rejects illegal microcode, e.g. `A_RS1` and `ALU_OP` in the same word (the ALU would need the new A in the same cycle).

Run `python microcode.py` to print both control memories. **Executing** from this ROM is item 3 and comes later.

---

## 3. ALU (Member D) — `alu.py`

Diagram: `diagrams/alu.png`

| Operation | Algorithms | Result |
|---|---|---|
| add | ripple-carry vs carry-lookahead (4-bit blocks) | 64 vs **18** gate delays |
| mul | shift-and-add vs Booth | random: ~equal (16.0 vs 15.9); small negative multiplier: 28.0 vs **4.7** add/sub |
| div | restoring vs non-restoring | 61.4 vs **32.5** add/sub |

Honest findings:
- **Booth only wins on runs of 1s** (e.g. negative numbers). On small positives it is slightly worse (4.6 vs 4.0).
- Non-restoring halves the work because it never "adds back".

Division works on 33-bit magnitudes with Python integers, so it does not go through the 32-bit adder; its cost is counted as add/sub steps.

---

## 4. Exceptions (Phase 1 set)

| Rule | Exception | Demo |
|---|---|---|
| address outside memory | MemoryFault | `ld r1, 0[r1]` with a huge r1 |
| address not a multiple of 4 | AlignmentFault | `ld r1, 1[r0]` |
| write into the code region | ProtectionFault | `st r1, 0[r0]` |
| `ret` to a non-code address | ProtectionFault | corrupted `ra` |
| divide by zero | DivideByZero | `divzero.s` |
| opcode that does not exist | IllegalInstruction | `.word 0xB0000000` |

Stack bound checking is item 2f and comes later.

---

## 5. Known simplifications (good viva answers)

- Branch prediction is always "not taken". A predictor would cut the flushes.
- `st` after `ld` of the same register stalls in the 6-stage. Sarangi forwards RW → MA to avoid this; we could add that.
- The microprogrammed control unit is designed but not yet executed (item 3).
- Stage delays and gate delays are assumed numbers, stated at the top of `bench.py`.
