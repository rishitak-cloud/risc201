# Design and evaluation

All numbers below come from `python bench.py` (full output in `results.txt`).

---

## 1. Processors (Member B pipelines, Member C single-cycle) — `pipeline.py`, `cpu.py`

Diagrams: `diagrams/pipeline_4stage.png`, `diagrams/pipeline_6stage.png`

### Where the stages come from

The classic 5-stage pipeline is **IF OF EX MA RW**. We derived both designs from it:

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

### Beyond the book: pipeline features (Member B)

All are options (`--predict`, `--store-fwd`, `--mul-latency`, `--div-latency`, `--unified-mem`), off by default, so the textbook timing above stays the baseline. **Every combination was checked: 960 program × configuration runs, all ending in exactly the single-cycle CPU's registers and memory.**

| 6-stage, all 10 programs | Cycles | Change | Flushed | Branches predicted |
|---|---|---|---|---|
| baseline: predict not-taken | 1889 | — | 486 | 27% |
| + store forwarding | 1881 | −0.4% | 486 | 27% |
| + 1-bit predictor + BTB | 1634 | −13.5% | 231 | 65% |
| + 2-bit predictor + BTB + RAS | 1586 | **−16.0%** | 183 | 73% |
| multi-cycle units (mul 4, div 20) | 2552 | +35.1% | 486 | 27% |
| unified memory (one port) | 1977 | +4.7% | 444 | 27% |

#### 1. Dynamic branch prediction (Stallings 14.4; Patterson & Hennessy 4.8)

- **BTB** (Branch Target Buffer): remembers, per branch PC, where it went and what kind of branch it is. Without a BTB hit, IF doesn't even know the word is a branch, so it predicts not-taken.
- **BHT** (Branch History Table) of counters for conditional branches. 1-bit remembers the last outcome; 2-bit (strongly/weakly not-taken/taken) needs two wrong guesses to change its mind, so a loop's branch mispredicts once per loop, not twice.
- **RAS** (Return Address Stack): `call` pushes its return address, `ret` pops it. Calls and returns are LIFO, so `ret` is predicted almost perfectly.
- **Repair:** wrong-path fetches can push/pop the RAS; each instruction remembers the RAS before it, and a misprediction restores it.
- A flush now happens only on a **misprediction**, not on every taken branch.

#### 2. Store-data forwarding RW → MA (the W → M forwarding path)

A `st` needs its base register in EX (for the address) but its **data** only in MA. Reading the data in MA removes the `ld r1` → `st r1` stall in the 6-stage. Small on our programs (8 of 80 load-use stalls) because most loads feed ALU instructions, not stores.

#### 3. Precise exceptions — a bug fix, always on

The old pipeline raised a fault the moment EX or MA found it. Two real bugs, both reproduced:

| Situation | Old pipeline | Now |
|---|---|---|
| older `ld` in MA, younger `div r3, r4, r0` in EX | div-by-zero raised; **the ld never wrote r1** | ld commits r1 = 42, then the exception |
| older `sub sp, sp, 8192` overflows the stack (found in RW), younger `cmp` already past EX | **flag E = 1** set by the younger cmp | E = 0 |

**Fix:** a fault is attached to its instruction and raised only in **RW**, after all older instructions have committed. Flags (E, GT, V) are also committed in RW, and forwarded from MA to a branch in EX. So when an exception is reported, every older instruction is complete and no younger one has changed anything.

#### 4. Multi-cycle functional units

`mul`/`fmul` take N cycles in EX, `div`/`mod`/`fdiv`/`fsqrt`/`froot` take M. While busy, IF..EX hold and a bubble goes into MA. With mul 4 / div 20 cycles, gcd.s (division-heavy) and matmul.s (multiply-heavy) slow down most — a structural hazard on EX.

#### 5. Unified memory (Von Neumann) structural hazard

One port for instructions and data: IF cannot fetch in a cycle where a `ld`/`st` uses memory. +4.7% cycles overall. **Interesting case:** sum.s loses nothing — every load there already causes a load-use stall, and IF doesn't fetch during that stall, so the port conflict hides inside a bubble that already exists.

#### 6. Control signals and the iron law

- Decode produces control signals (`RegWrite`, `MemRead`, `MemWrite`, `MemToReg`, `Branch`, `SetFlags`, `ALUOp`), and MA and RW act on these bits rather than on the instruction's name. `pipe` shows them per stage.
- `stats` reports the iron law directly: CPI, clock period, time in ns and MIPS. The stage delays live in one place (`pipeline.py`), shared with `bench.py`.

#### Not implemented, and why

- **Branch resolution in OF**: would save one flush cycle per misprediction, but needs a forwarding path into OF and new stalls for a branch right after a `cmp` or `ld`. With a 73%-accurate predictor, the remaining saving is small.
- **Register read in OF with a split-phase register file**: our EX forwarding (MA, then RW, then register file) already gives the same values; doing the read in OF changes where the code reads, not what the processor computes.

---

## 2. Microprogrammed control unit (Member C) — `microcode.py`, `microcpu.py`

Diagram: `diagrams/control_unit.png`

A non-pipelined processor whose control comes from **control memory** instead of hardwired logic. Each clock cycle: read the microinstruction at **microPC**, turn on its control signals, compute the next microPC. The only place an opcode is looked at is the DISPATCH table.

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

### Beyond the book: three features we added (Member C)

#### 1. A third control style: mixed (field-encoded) microcode — `--mode mixed`

Horizontal and vertical are the two extremes. Real machines usually sit in between: group signals that are **never on in the same word** into a field, and encode each field. The microprogram is the horizontal one (same parallelism, same micro-cycles), but the word is much narrower.

| | Horizontal | Vertical | **Mixed** |
|---|---|---|---|
| Control memory | 26 × 26 = 676 bits | 36 × 14 = 504 bits | **26 × 15 = 390 bits** |
| Micro-cycles (all 10 programs) | 6231 | 8234 | **6231** |
| Clock (decoder delay) | 270 ps | 300 ps | 294 ps |
| Total time | 1682 ns | 2470 ns | 1832 ns |

`bench.py` section 2b checks which designs are on the trade-off frontier: **vertical is dominated** — mixed has a smaller ROM *and* is faster. Horizontal and mixed are both on the frontier: horizontal is fastest, mixed is smallest. The test suite proves that no horizontal word turns on two signals of the same field, so the packing loses nothing.

#### 2. Microcode profiler — `uprof`

Counts how often every control word, signal and opcode ran, and draws ASCII bars. It shows which micro-routines dominate (FETCH and DECODE run on every instruction), which control words a program never touches, and which signals never fire — e.g. `fp.s` uses only 14 of the 26 words.

#### 3. Lockstep verifier + fault injection — `verify.py`

The microprogrammed CPU gets its behaviour from **data** (the control memory), and data can have bugs. `verify.py` runs it side by side with the single-cycle CPU and compares registers, flags and memory **after every instruction**, reporting the first instruction that differs.

`--flip STYLE WORD BIT` damages one control-memory bit and shows the verifier catching it. `--mutate` flips **every** bit, one at a time:

| sum.s, horizontal ROM | Count |
|---|---|
| single-bit faults injected | 676 |
| caught (wrong state, crash, invalid microinstruction, micro-loop) | 211 |
| survived because this program never executes that word | 234 |
| survived in executed words (don't-care bits, e.g. next-address of a NEXT word) | 231 |

Of the bits in the words the program actually executes, 48% are detected. The survivors are explained, not hidden: most are don't-care fields.

A control word whose bits name no signal now raises `MicrocodeFault` instead of crashing Python.

---

## 3. ALU and FPU (Member D) — `alu.py`, `fpu.py`

Diagram: `diagrams/alu.png`

Every algorithm follows the course textbook's computer arithmetic chapter; the section each one comes from is named in the code comments, so it can be checked against the book.

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

### Beyond the book: two features we added

#### 1. Radix-4 Booth multiplier (`--mul booth4`, book exercise 15)

**The idea.** Radix-2 Booth (book §8.2.3) looks at 2 bits of the multiplier per step and does 32 steps. Radix-4 Booth looks at **3 bits** per step — the two current bits plus the last bit of the previous pair — and shifts UV right by **2**, so it needs only **16 steps**.

**What each step adds.** The 3 bits (b₂ᵢ₊₁, b₂ᵢ, b₂ᵢ₋₁) say which multiple of the multiplicand N to add to U:

| Bits | Add | Why |
|---|---|---|
| 000, 111 | nothing | inside a run of 0s or 1s |
| 001, 010 | +N | a single 1 |
| 011 | +2N | a run of 1s ends here |
| 100 | −2N | a run of 1s starts here |
| 101, 110 | −N | a 1 next to a run |

**2N costs nothing extra:** it is N shifted left by one, which is just wiring. So every step is still at most **one** add/subtract.

**Why it works.** Each pair of bits is worth 4ᵏ, so the multiplier is rewritten in base 4 with digits from {−2, −1, 0, +1, +2}. The table is exactly radix-2 Booth applied to two bits at once: the two radix-2 actions for those bits, combined.

**Worked example: 3 × 5 (4-bit, book style).** Multiplicand N = 3, multiplier M = 5 = 0101. U is 6 bits (two more than the book's U, because ±2N needs one more bit of room); V holds the multiplier.

| Step | Bits looked at | Action | U, V after the shift |
|---|---|---|---|
| start | | | 000000 0101 |
| 1 | b₁b₀b₋₁ = 010 | U += N | 000000 1101 |
| 2 | b₃b₂b₁ = 010 | U += N | 000000 1111 |

Product = 0000 1111 = 15, with **2** add/subs. Radix-2 Booth needs **4** on the same numbers, because 0101 alternates — that's radix-2 Booth's worst case (every bit pair is a transition). Radix-4 doesn't care.

**Negative multiplier: 3 × (−2), M = 1110.** Step 1 sees 100 → U −= 2N; step 2 sees 111 → nothing. Product 1111 1010 = −6 with **1** subtraction. Like radix-2 Booth, the sign is handled for free: the last triple reads the sign bit.

**Results on our data:**

| Add/subs per multiply | Iterative | Booth (radix-2) | **Radix-4 Booth** |
|---|---|---|---|
| Random 32-bit | 16.0 | 16.0 | **12.0** |
| Small positive | 3.9 | 4.7 | **3.5** |
| Small negative | 28.1 | 4.6 | **3.5** |
| matmul.s (whole program) | 669 | 315 | **240** |
| Worst case | 32 | 32 (0101…) | **16** |

It is never worse than either of the book's multipliers, and its worst case is half.

**Cost:** U needs 34 bits instead of 33 (±2N is twice as big as ±N), and the adder needs a small 5-way multiplexer to pick 0, ±N or ±2N.

#### 2. n-th root: `froot rd, rs1, n` (book exercise 33, generalised)

The book's exercise is the square root. We generalised it to **any n from 1 to 255**, still correctly rounded; `fsqrt` is just the case n = 2.

| Step | Square root | n-th root |
|---|---|---|
| Make the exponent divisible | even | a multiple of n (shift P left by t mod n) |
| Newton–Raphson on f(x) = xⁿ − a | x ← (x + a/x) / 2 | x ← ((n−1)x + a/xⁿ⁻¹) / n |
| Exact check → sticky bit | root² = input? | rootⁿ = input? |

The exact check is integer arithmetic, so the sticky bit is exact for every n — that is what makes the result correctly rounded, not just close.

- **Seed:** like real hardware, a first guess from an 8-bit lookup table (the top 8 bits of the input), rounded up so Newton approaches from above and falls every step.
- **Steps:** about 3 for every n — the 8 good bits of the seed become 16, then 32, more than the 27 needed.
- **Signs:** odd roots of negatives exist (∛−27 = −3); even roots of negatives are NaN + invalid. ±0 follows IEEE 754-2008 `rootn`: −0 for odd n, +0 for even n — except `fsqrt(−0) = −0`, IEEE's special rule for square root.
- **Verified:** for n = 2, 3, 4, 5, 7, 10, 31, 255, every result was checked with **exact fractions**: the input must lie strictly between the n-th powers of the two midpoints around the answer. 0 failures. `fsqrt` stays bit-identical to hardware.

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

Follows §8.4–8.6 of the textbook. Nine new instructions: `fadd fsub fmul fdiv fcmp itof ftoi fsqrt froot`. They use the normal registers (bit patterns), go through the existing ALU routines in the microcode (ROM size unchanged), and through EX in the pipelines.

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

**2g — visualizer (`stackview.py`, the `stack` command).** Draws memory from the stack base down to sp, with a rule at each frame boundary (the sp recorded at every `call`) and a usage bar. In a terminal the sp row is green, saved return addresses yellow and problems red. A word whose value equals *caller + 4* of an active call is marked as a return address, and overflow, underflow or a misaligned sp get a clear message. On `fact.s` at the base case it shows six nested frames.
---

## 5. Known simplifications (good viva answers)

- Branch prediction is always "not taken". A predictor would cut the flushes.
- `st` after `ld` of the same register stalls in the 6-stage by default; `--store-fwd` adds the RW → MA path that removes the stall.
- The microprogrammed CPU is not pipelined: each instruction runs to completion before the next one starts.
- Stage delays and gate delays are assumed numbers, stated at the top of `bench.py`.

---

## 6. Evaluation summary (item 5)

Ten programs × six processor models × four ALU configurations. `python bench.py` prints every table; `--csv out/` saves them.

| Question | Answer from the data |
|---|---|
| Which processor is fastest in real time? | **6-stage, on all 10 programs**, despite the highest CPI among the hardwired designs |
| Why not the single-cycle? | CPI = 1, but its 1000 ps clock must fit the whole instruction |
| When does the 4-stage beat the 6-stage on cycles? | Always — no load-use stalls, one fewer flush per taken branch. It still loses on time because its clock is 470 ps vs 270 ps |
| Horizontal, vertical or mixed? | Horizontal is fastest; mixed has the smallest ROM (390 bits) at nearly horizontal speed; vertical is dominated by mixed |
| Which adder? | Carry select (17) ≈ lookahead (18) ≫ ripple (64) at 32 bits |
| Which multiplier? | Radix-4 Booth: **matmul 240**, vs 315 for Booth and 669 for iterative. Never worse than either |
| Which divider? | Non-restoring: gcd 97 vs 186 add/subs |
| Which FP divider? | Restoring is always exact; Newton–Raphson and Goldschmidt are 6× fewer steps and only 1 ulp off in 0.01–0.02% of cases |
| What happens on overflow? | Wrap + V flag by default; precise `ArithmeticOverflow` trap with `--trap-overflow` |
| Best pipeline improvement? | 2-bit branch predictor + BTB + RAS: −16% cycles, mispredictions 162 → 61 |
| Is everything correct? | All 240 runs (24 per program) end in identical registers, flags and memory; `verify.py` also checks the microcoded CPU after every single instruction |

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
