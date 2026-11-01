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

The single-cycle CPU (`cpu.py`) has CPI = 1 but the longest clock (1000 ps: one cycle must fit fetch + decode + register read + ALU + memory + write back), so it is slowest in real time — bubble.s takes 375 ns.

**The 6-stage always needs more cycles** (load-use stalls + 1 extra flush per taken branch).

**But its clock is faster.** With assumed stage delays (IF 200, ID 100, OF 150, EX 250, MA 200, RW 100 ps, latch 20 ps):

| | Slowest stage | Clock period | bubble.s time |
|---|---|---|---|
| 4-stage | EX+MA = 450 ps | 470 ps | 236.9 ns |
| 6-stage | EX = 250 ps | 270 ps | **163.3 ns** |

So: time = instructions × CPI × clock period. The 6-stage loses on CPI and wins on period.

---

## 2. Microprogrammed control unit (Member C) — `microcode.py`, `microcpu.py`

Diagram: `diagrams/control_unit.png`

A non-pipelined processor whose control comes from **control memory** instead of hardwired logic (Sarangi Ch. 8). Each clock cycle: read the microinstruction at **microPC**, turn on its control signals, compute the next microPC. The only place an opcode is looked at is the DISPATCH table.

- 18 control signals (`A_RS1`, `ALU_OP`, `MEM_READ`, `PC_INC`, …)
- 6 sequencing modes: NEXT, JUMP, DISPATCH (jump by opcode), IF_E, IF_GT, FETCH
- word layout: `| signals | sequencing | next address |`
- `microcode.py` holds both ROMs (the design); `microcpu.py` **executes** programs from them (item 3)

### Design comparison

| | Horizontal | Vertical |
|---|---|---|
| Signal field | 1 bit per signal (18 bits) | encoded micro-op (5 bits) |
| Signals per word | many | one |
| Needs a decoder | no | yes |
| Control memory | 26 words × 26 bits = **676 bits** | 36 words × 14 bits = **504 bits** |

### Measured: micro-cycles per instruction

| Instruction | Horizontal | Vertical |
|---|---|---|
| ALU op (reg or imm) | 5 | 7 |
| mov / not | 5 | 6 |
| cmp | 4 | 6 |
| ld | 7 | 9 |
| st | 6 | 9 |
| beq / bgt, b, ret, nop | 3–4 | 3–4 |
| call | 3 | 4 |

Branches cost the same in both: they need one micro-op each, so there is nothing for horizontal to do in parallel. The gap is in data-moving instructions.

### Measured: whole programs

| Program | Horizontal CPI | Vertical CPI | Horizontal time | Vertical time |
|---|---|---|---|---|
| sum | 4.97 | 6.58 | 85.9 ns | 126.3 ns |
| fact | 5.08 | 6.77 | 108.3 ns | 160.5 ns |
| bubble | 4.98 | 6.57 | 504.6 ns | 738.6 ns |
| matmul | 4.98 | 6.66 | 662.6 ns | 985.5 ns |

Vertical is ~1.3× the micro-cycles and ~1.5× the time (the decoder also lengthens its clock), in exchange for a 25% smaller control memory.

`check_rom()` rejects illegal microcode, e.g. `A_RS1` and `ALU_OP` in the same word (the ALU would need the new A in the same cycle). Runtime exceptions: unknown opcode at DISPATCH → `IllegalInstruction`; microPC outside the ROM → `MicrocodeFault`.

---

## 3. ALU and FPU (Member D) — `alu.py`, `fpu.py`

Diagram: `diagrams/alu.png`

Every algorithm follows Sarangi Chapter 8; section numbers are in the file header so each one can be checked against the book.

### Adders

| Algorithm | Book | Complexity | Our delay (n=32) |
|---|---|---|---|
| Ripple carry | 8.1.3 | O(n) | 64 gate levels |
| Carry select | 8.1.4 | O(√n) | 17 |
| Carry lookahead | 8.1.5 | O(log n) | 18 |

- **Carry select**: blocks of k = √32 ≈ 6 bits. Each block is added twice in parallel (carry-in 0 and carry-in 1); a multiplexer picks the right one when the real carry arrives, so the carry crosses one mux per block instead of 6 full adders.
- **Carry lookahead**: a tree of (G, P) blocks. Stage I combines children bottom-up with the book's rule (eq. 8.9) `G(1,n) = G(m+1,n) + P(m+1,n)·G(1,m)`, `P(1,n) = P(m+1,n)·P(1,m)`. Stage II pushes carries back down with `Cout = G + P·Cin`. 2-bit ripple adders at the leaves make the sum.

Honest reading of the numbers: at n = 32, carry select (17) and lookahead (18) are level, because √32 = 5.7 and log₂32 = 5 are close. Lookahead wins as n grows — at n = 1024 it would be 38 against carry select's 95.

### Multipliers

Both share the book's datapath: a 33-bit **U** joined to a 32-bit **V**, shifted right as one register. The multiplier sits in V, the multiplicand in N. The extra bit of U prevents overflow when N is added (book Important Point 9).

| Algorithm | Book | Rule |
|---|---|---|
| Iterative | 8.2.2 | If LSB(V) = 1, U += N — but U −= N on the last step, which handles a negative multiplier |
| Booth | 8.2.3 | Bit pair (current, previous): 1,0 → U −= N; 0,1 → U += N; 0,0 and 1,1 → nothing |

Booth replaces j−i+1 additions in a run of 1s with one add and one subtract (eq. 8.10).

| Operands | Iterative | Booth |
|---|---|---|
| Random 32-bit | 16.0 add/sub | 15.9 |
| Small positive | **4.0** | 4.6 |
| Small negative | 28.0 | **4.7** |

Booth wins big on negative numbers (long runs of 1s in the high bits) and loses slightly on small positives. Same asymptotic complexity, as the book says.

### Dividers

Same U/V pair, but shifted **left**, operating on magnitudes with signs fixed afterwards (8.3.1).

| Algorithm | Book | Per step |
|---|---|---|
| Restoring | 8.3.2 | `U −= D`; if negative, add D back — up to **two** add/subs |
| Non-restoring | 8.3.3 | `U ≥ 0 ? U −= D : U += D` — exactly **one**, plus one final correction |

61.4 vs 32.5 add/subs per divide: non-restoring does about half the work.

---

### Overflow

Signed (2's complement) overflow is detected for `add`, `sub`, `mul`, `div` — the same tests hardware uses:

| Op | Overflow when |
|---|---|
| add | A and B have the same sign, and the result's sign differs |
| sub | A and B have different signs, and the result's sign differs from A |
| mul | the full 64-bit product in UV (the book's multiplier already builds it) doesn't fit in 32 signed bits |
| div | only −2³¹ / −1 (the answer +2³¹ doesn't fit) |

Two policies:

| Policy | Behaviour | Real ISA |
|---|---|---|
| **wrap** (default) | keep the low 32 bits, set the **V** flag | C, RISC-V, ARM |
| **trap** (`--trap-overflow`) | raise `ArithmeticOverflow` at the exact PC | MIPS `add` |

The trap is **precise** in every model: in the pipelines it fires in EX, after all older instructions have finished and before any younger one has changed state. `examples/ovf.s` demonstrates both.

### Floating point (IEEE 754 single precision) — `fpu.py`

Follows Sarangi §8.4–8.6. Seven new instructions: `fadd fsub fmul fdiv fcmp itof ftoi`. They use the normal registers (bit patterns), go through the existing ALU routines in the microcode (ROM size unchanged), and through EX in the pipelines.

| Step | How (book) |
|---|---|
| Unpack | S, E, M; hidden 1 for normals, 0.M × 2⁻¹²⁶ for denormals (eq. 8.22–8.23) |
| Add/sub | swap so E_B ≤ E_A, shift P_B right by E_A − E_B, 2's complement if signs differ, add, fix sign (fig. 8.17) |
| Multiply | S = S_A ⊕ S_B, E = E_A + E_B − 127, W = P_A × P_B (fig. 8.18) |
| Divide | E = E_A − E_B + 127; P_A / P_B by restoring division, Newton–Raphson or Goldschmidt (§8.6) |
| Round | guard, round, sticky bits; four IEEE modes (table 8.6) |
| Normalise | before rounding, and again if rounding carries out (§8.4.3) |
| Specials | ±0, ±∞, NaN, denormals; IEEE status flags instead of traps |

**Reuse of integer hardware:** significand addition goes through the ALU's adder; significand multiplication through its Booth / iterative multiplier (24 × 24 bits fits its 64-bit UV product).

**Verified against real IEEE hardware:** 160,000 random operations (including denormals, NaN, ±∞) give bit-identical results to numpy's float32, and all three directed rounding modes are checked against exact rational arithmetic.

| Division algorithm | Correctly rounded | Worst error | Steps per divide |
|---|---|---|---|
| restoring | 100% | 0 ulp | 29 |
| Newton–Raphson | 99.99% | 1 ulp | 5 |
| Goldschmidt | 99.98% | 1 ulp | 5 |

That is the book's point in §8.6: the iterative methods are much faster because floating point is approximate anyway.

---

## 4. Stack: macros (A), protection (B), visualizer (A) — `preprocessor.py`, `machine.py`, `stackview.py`

**2e — macros (`preprocessor.py`, runs before pass 1).** Full-descending stack: it grows towards lower addresses and sp points **at** the last word pushed.

```
push {r2, ra}   ->   sub sp, sp, 8      pop {r2, ra}   ->   ld  r2, 4[sp]
                     st  r2, 4[sp]                         ld  ra, 0[sp]
                     st  ra, 0[sp]                         add sp, sp, 8
```

pop takes the same register list as push, so the order always matches. Every expanded line keeps its original source line number, so assembler errors still point at the user's file.

**2f — protection (`machine.py`, `exceptions.py`).** Checked on every sp write and every memory access:

| Rule | Exception | Demo |
|---|---|---|
| sp below stack limit (0xF000) | StackOverflow | `overflow.s` |
| sp above stack base, or reading above it | StackUnderflow | `pop {r1}` on an empty stack |
| ld/st in stack region below sp | StackAccessViolation | `ld r1, -4[sp]` |
| write into the code region | ProtectionFault | `st r1, 0[r0]` |
| `ret` to a non-code address | ProtectionFault | `smash.s` |
| divide by zero | DivideByZero | `divzero.s` |

Run with `--no-guard` to show the alternative: `overflow.s` runs until the stack crashes into the code, and `smash.s` jumps to 0xBEEF and executes garbage. That contrast is the point of the feature.

**2g — visualizer (`stackview.py`, the `stack` command).** Draws memory from the stack base down to sp, with a horizontal rule at each frame boundary (the sp recorded at every `call`), plus a usage bar. On `fact.s` at the base case it shows six nested frames, each holding a saved n and a saved return address.

---

## 5. Known simplifications (good viva answers)

- Branch prediction is always "not taken". A predictor would cut the flushes.
- `st` after `ld` of the same register stalls in the 6-stage. Sarangi forwards RW → MA to avoid this; we could add that.
- The microprogrammed CPU is not pipelined, as in Sarangi's design.
- Stage delays and gate delays are assumed numbers, stated at the top of `bench.py`.

---

## 6. Evaluation summary (item 5)

Ten programs × five processor models × three ALU configurations. `python bench.py` prints every table; `--csv out/` saves them.

| Question | Answer from the data |
|---|---|
| Which processor is fastest in real time? | **6-stage, on all 10 programs**, despite the highest CPI among the hardwired designs |
| Why not the single-cycle? | CPI = 1, but its 1000 ps clock must fit the whole instruction |
| When does the 4-stage beat the 6-stage on cycles? | Always — no load-use stalls, one fewer flush per taken branch. It still loses on time because its clock is 470 ps vs 270 ps |
| Horizontal or vertical? | Horizontal is ~1.5× faster; vertical's ROM is 25% smaller |
| Which adder? | Carry select (17) ≈ lookahead (18) ≫ ripple (64) at 32 bits |
| Which multiplier? | Booth on real code: **matmul 315 vs 669** add/subs. Iterative is slightly better on small positive multipliers |
| Which divider? | Non-restoring: gcd 97 vs 186 add/subs |
| Which FP divider? | Restoring is always exact; Newton–Raphson and Goldschmidt are 6× fewer steps and only 1 ulp off in 0.01–0.02% of cases |
| What happens on overflow? | Wrap + V flag by default; precise `ArithmeticOverflow` trap with `--trap-overflow` |
| Is everything correct? | All 150 runs (15 per program) end in identical registers, flags and memory |

Programs and what each one stresses:

| Program | Stresses |
|---|---|
| `sum`, `bubble` | loads followed by use → load-use stalls |
| `fact` | call/ret, stack, recursion |
| `hazard` | one of every hazard |
| `fib` | ALU-only dependence chains → forwarding, no stalls |
| `gcd` | division (`mod`) |
| `matmul` | multiplication with negative operands + memory |
| `muldiv` | every ALU operation, signed edge cases |
| `fp` | every float instruction, rounding error, overflow to ∞ |
| `ovf` | integer overflow: wrap vs trap |
