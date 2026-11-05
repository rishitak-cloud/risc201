# Viva prep — one section per member

Each member should be able to (1) explain their part in ~1 minute, (2) run its demo, (3) walk through the named functions line by line.

---

## Member A — ISA + assembler + disassembler + stack macros + stack view (2a, 2b, 2e, 2g)

**Files:** `isa.py`, `assembler.py`, `disassembler.py`, `preprocessor.py`, `stackview.py`
**Diagram:** `instruction_formats`
**Code to know cold:** `isa.encode`, `isa.decode`, `assembler.assemble` (both passes), `assembler.encode_line`, `disassembler.disassemble`, `preprocessor.expand_macros` (incl. `li` / `lif`), `stackview.render`

**1-minute pitch:** RISC201 follows SimpleRisc: 21 instructions + `hlt`, 32-bit fixed length, 3 formats. The assembler is two-pass: pass 1 gives every line an address and builds the symbol table, pass 2 encodes, so forward references work. The disassembler reverses it and rebuilds labels from branch targets; our tests prove .s → hex → .s → hex gives identical words.

**Likely questions**

| Question | Answer |
|---|---|
| Why two passes? | A branch can point to a label defined later. Pass 1 learns all addresses first. |
| Why is the branch offset in words, not bytes? | Instructions are 4-byte aligned, so the low 2 bits are always 0. Storing words gives 4× the range from 27 bits. |
| What does the `u`/`h` modifier do? | Chooses how 16 bits become 32: sign-extend, zero-extend, or put in the upper half. |
| How do you load 0x12345678? | `movh r1, 0x1234` then `oru r1, r1, 0x5678`. |
| Why does `st` use the rd field as a source? | To keep one format for ld/st. Hazard logic must treat st's rd as a read (see `isa.source_regs`). |
| How does the disassembler know what's data? | The hex file records `code_end`; words after it print as `.word`. |
| Show where the I bit is set. | `isa.encode`: `word |= 1 << 26` when an immediate is given. |
| Why expand macros before pass 1, not after? | Pass 1 assigns addresses. `push {r2, ra}` becomes 3 instructions, so the addresses would all be wrong if it ran later. |
| Why does pop take the same list as push? | So a function can write `push {r2, ra}` … `pop {r2, ra}` and the offsets always line up. The expansion reverses the order internally. |
| Why full descending? | The stack grows down from the top of memory towards the heap, and sp points at live data, so `0[sp]` is always a valid word. |
| How does the stack view find frames? | `Machine.frames` records the sp at every `call`; the renderer draws a rule at each of those addresses. |
| Why draw high addresses at the top? | The stack descends, so sp at the bottom of the picture matches how the stack is usually drawn in the textbook. |
| How do you load a 32-bit constant? | `li r1, 0x12345678` expands to `movh r1, 0x1234` + `oru r1, r1, 0x5678` — immediates are only 16 bits. |
| How do you load a float constant? | `lif r1, 2.5` does the same with 2.5's IEEE bits (0x40200000); `.float 2.5` puts it in memory. |
| Why do li / lif always give two instructions? | So every line's size is known in pass 1 and addresses never shift. |

---

## Member B — pipelines + stack protection (1a, 2f)

**Files:** `pipeline.py`, `machine.py` (Machine, RegisterFile, Memory + the stack guard), `exceptions.py`
**Diagrams:** `pipeline_4stage`, `pipeline_6stage`
**Code to know cold:** `Pipeline.step` (3 phases), `read_operand` (forwarding), `execute` (branch decision), the load-use check at the end of `step`, `Machine.__init__`, `RegisterFile.write`

**1-minute pitch:** Both pipelines come from Sarangi's 5 stages. The 4-stage merges EX and MA; the 6-stage splits OF into decode and register read. Each cycle: clock edge moves instructions, each stage works oldest-first, then we detect hazards for the next edge. Forwarding removes all ALU stalls; only the 6-stage has load-use stalls; taken branches flush 2 or 3 instructions.

**Demo:** `python cli.py examples/hazard.s --mode pipe6`, then `step 8`, `diag`. Repeat with `--mode pipe4` and compare. Then `python cli.py examples/smash.s --run` and `examples/overflow.s --run` for the guard, and `--no-guard` to show what happens without it.

**Likely questions**

| Question | Answer |
|---|---|
| Why does the 4-stage never stall on a load? | Memory access happens inside EX, so the value is in the RW latch when the next instruction reaches EX. |
| Why does the 6-stage need exactly 1 stall? | The ld has its data at the end of MA; the next instruction wants it at the start of EX, one cycle too early. |
| Why flush 3 in the 6-stage? | The branch decides in EX; IF, ID and OF already hold wrong-path instructions. |
| Is the 6-stage worse then? | More cycles, but a shorter clock period (270 vs 470 ps with our delays). bubble.s: 163 ns vs 237 ns. |
| What if an instruction past the end is fetched? | It is marked faulty but only raises if it reaches EX; a branch flush discards it silently. |
| How would you reduce flushes? | Branch prediction, or resolving branches earlier (in OF). |
| Why do stages run oldest-first? | RW writes the register file before EX reads, which is what the forwarding path provides in hardware. |
| What does the stack guard check? | sp below the limit → StackOverflow; sp above the base → StackUnderflow; access below sp → StackAccessViolation; write into code → ProtectionFault; `ret` outside the code → ProtectionFault. |
| Why is reading below sp an error? | In a full-descending stack only sp..base is allocated; below sp is not yours yet. |
| How is a corrupted return address caught? | `Machine.check_return` verifies the target is inside the code region before `ret` takes it. `smash.s` demonstrates it. |
| Where do exceptions get the PC? | `machine.pc_now` is set by the processor before every memory or register access. |

---

## Member C — control unit + microcode execution + single-cycle CPU (1b, 2d, 3)

**Files:** `microcode.py` (the ROMs), `microcpu.py` (runs them), `cpu.py` (single-cycle)
**Diagram:** `control_unit`
**Code to know cold:** `MicroCPU.micro_step` (fetch word → decode → next microPC → perform signals), `next_upc`, `perform`, `ControlMemory.encode` / `decode`, `check_rom`, `SingleCycleCPU.step`

**1-minute pitch:** This CPU has no hardwired control. Each cycle it reads one microinstruction from control memory at microPC, turns on its control signals and computes the next microPC. Opcodes are only used by DISPATCH to jump to a routine. Horizontal words have one bit per signal (wide, fast); vertical words hold one encoded micro-op (narrow, slower, needs a decoder).

**Demo:** `python cli.py examples/sum.s --mode vertical` → `ucode`, `next`, `next` (microPC trace per instruction), `upc`. Repeat with `--mode horizontal` and compare the step counts. Then show section 3 of `python bench.py` (micro-cycles per instruction).

**Likely questions**

| Question | Answer |
|---|---|
| Horizontal vs vertical in one line? | Horizontal = more bits per word, fewer steps. Vertical = fewer bits, more steps, needs a decoder. |
| Your numbers? | Horizontal 26×26 = 676 bits, CPI ≈ 5. Vertical 36×14 = 504 bits, CPI ≈ 6.6, and ~1.5× slower in time. |
| Which instructions show the biggest gap? | `st` (6 vs 9) and ALU ops (5 vs 7): they move several values that horizontal can move in parallel. Branches cost the same in both — one micro-op each, nothing to parallelise. |
| How is the next microPC chosen? | `next_upc` looks at the sequencing field: NEXT = +1, JUMP = target, DISPATCH = the opcode's routine, IF_E / IF_GT = target if the flag is set, FETCH = 0. It uses values from the START of the cycle, like hardware. |
| Why compute the next microPC before performing the signals? | So a word that changes the flags can't affect its own branch decision — same as all signals latching on one clock edge. |
| How does the vertical decoder work? | The 5-bit field is a number; `ControlMemory.decode` turns it into one signal name. Horizontal just tests 18 bits. |
| Is the microprogrammed CPU pipelined? | No, same as Sarangi's. Each instruction runs start to finish over several micro-cycles. |
| What is DISPATCH? | Next microPC = start of the routine for this opcode (Sarangi's `mswitch`). |
| Why can't A_RS1 and ALU_OP share a word? | They happen at the same clock edge; the ALU would read the old A. `check_rom` rejects it. |
| Why can RA_LINK and PC_BRANCH share a word? | RA_LINK reads the old PC, PC_BRANCH writes the new one — same edge, no conflict. |
| How do mov/not reuse the ALU routine? | MOVNOT loads B then JUMPs to ALU_GO. |
| What raises an exception? | Unknown opcode at DISPATCH → `IllegalInstruction`; microPC outside the ROM → `MicrocodeFault`; memory and stack faults pass through from `machine.py`. |
| What is the single-cycle CPU for? | One whole instruction per step, no overlap: the simplest correct model. The pipelines and microcode must end in the same state as it. |
| Why is its CPI exactly 1? | Every instruction takes one (long) cycle; the clock period must fit the slowest instruction (ld: fetch + decode + ALU + memory + write back). |

---

## Member D — ALU, overflow, floating point + CLI debugger (1c, 2c, 2d, 4)

**Files:** `alu.py`, `fpu.py`, `cli.py`
**Diagram:** `alu`
**Code to know cold:** `ALU.compute`, `ALU._cla` (the (G,P) tree), `ALU._cselect`, `ALU._booth`, `ALU._nonrestoring`, `_shift_uv_right` / `_shift_uv_left`, `add_overflows` / `sub_overflows` / `mul_full`, `FPU.add`, `FPU.mul`, `FPU.round_and_pack`, `Debugger.do_continue`, `Debugger.guarded`, `Debugger.do_watch`

**1-minute pitch:** Every arithmetic, logic and shift operation in all four processor models goes through one ALU class. The adder, multiplier and divider algorithms can be swapped, and the ALU counts their work so we can compare them. The CLI debugger drives any processor model through the same small set of methods (`step`, `show`, `stats`), and adds breakpoints, watches, memory and register inspection and editing.

**Demo:** `python cli.py examples/sum.s --mode pipe4`, then `watch r1`, `c`, `regs`, `mem array 4`. Then `ovf.s --run` (V=1) and `ovf.s --run --trap-overflow` (exception). Then `fp.s`, `c`, `fregs` (0.1 × 10 ≠ 1.0, 1e38 × 10 = inf). Finally `python fpu.py`.

**Likely questions**

| Question | Answer |
|---|---|
| Why is CLA faster? | A tree of (G,P) blocks: `G = Gu + Pu·Gl`, `P = Pu·Pl` (book eq. 8.9). O(log n) levels instead of n carries. 18 vs 64 gate levels. |
| What is the carry select adder? | Each block is added twice in parallel, for carry-in 0 and 1; a mux picks the right one when the real carry arrives. O(√n), so k = √32 ≈ 6. |
| Which of the three is best? | At n = 32, carry select (17) and lookahead (18) tie, because √32 ≈ log₂32. Lookahead wins as n grows: at n = 1024 it's 38 vs 95. |
| What are U and V? | The book's multiplier/divider datapath: a 33-bit U joined to a 32-bit V, shifted as one register. Multiply shifts right, divide shifts left. |
| Why is U 33 bits? | So adding or subtracting N (or D) can never overflow — book Important Points 9 and 12. |
| Why does the iterative multiplier subtract on the last step? | The MSB of a 2's complement multiplier has weight −2³¹, so that bit must be subtracted, not added. |
| When does Booth help? | Runs of 1s, i.e. negative multipliers: 4.7 vs 28 add/subs. On random data they tie; on small positives Booth is slightly worse (4.6 vs 4.0). |
| Restoring vs non-restoring? | Non-restoring never adds back after a negative result; the next step adds instead. ~33 vs ~61 operations. |
| How is subtraction done? | A + (~B) + 1 through the same adder (`ALU.sub`). |
| How does cmp set GT? | Uses the subtractor; E = result is zero, GT = signed A > B. |
| How does one CLI work for pipelines and microcode? | Both classes have the same methods (`step`, `show`, `stats`, `halted`), so the CLI does not care which one it has. |
| How does a watch work? | Before running, save the watched values; after every cycle compare; stop on a change. |
| Why is the breakpoint checked in EX, not IF? | IF may fetch wrong-path instructions that get flushed; EX only sees instructions that really execute. |
| What happens on an exception? | `guarded()` catches it, prints the PC and source line, and blocks further stepping until `reset`. |
| **How do you handle overflow?** | Detect it like hardware: for add, same-sign inputs and a different-sign result; for sub, different-sign inputs and the result's sign differs from A; for mul, the full 64-bit UV product doesn't fit 32 bits; for div, only −2³¹/−1. Then either wrap and set V (default, like C / RISC-V / ARM) or trap with `ArithmeticOverflow` (`--trap-overflow`, like MIPS `add`). |
| Why not just check the carry out? | Carry out is UNSIGNED overflow. 0xFFFFFFFF + 1 carries out but is −1 + 1 = 0 — correct for signed numbers. Signed overflow is about the sign bit. |
| Is the trap precise? | Yes. It fires in EX; older instructions have already written back, younger ones haven't changed anything. The PC points at the guilty instruction. |
| How does mul detect overflow for free? | The book's multiplier already produces 64 bits in U and V. If the high half isn't just the sign-extension of V, the answer didn't fit. |
| **How do you do IEEE 754?** | `fpu.py`, following book §8.4–8.6. Unpack sign, exponent, significand; align exponents; add or multiply significands; normalise; round with guard/round/sticky bits; renormalise; check overflow/underflow; pack. |
| Why guard, round, sticky? | Bits shifted out during alignment still affect rounding. Round = first lost bit, sticky = OR of all the rest. Guard keeps one more so a 1-bit left normalisation doesn't lose the round bit (§8.4.4). |
| What is round to nearest even? | Round to the closer float; on an exact tie, pick the one with LSB 0 (table 8.6). Avoids a systematic upward bias. |
| What about 0/0, 1/0, overflow? | IEEE default: no trap. 0/0 → NaN + invalid; 1/0 → ∞ + divzero; too large → ∞ + overflow; too small → denormal or 0 + underflow. `fregs` shows the flags. |
| What is a denormal? | Exponent field 0: value 0.M × 2⁻¹²⁶. Fills the gap between 0 and the smallest normal, so x − y = 0 only if x = y. |
| Why is 0.1 × 10 ≠ 1.0? | 0.1 has no exact binary form; each add rounds. fp.s shows 1.00000012. |
| How do you know the FPU is right? | 160,000 random operations, including denormals, NaN and ∞, match numpy's hardware float32 bit for bit; directed rounding checked against exact fractions. |
| Do floats reuse the integer hardware? | Yes: significand add uses the ALU adder; significand multiply uses the Booth/iterative multiplier. The microcode needed no new routines — `fadd` dispatches to the same ALU routine as `add`. |
| Restoring vs Newton–Raphson vs Goldschmidt? | Restoring: 29 steps, always exact. NR and Goldschmidt: 5 steps, 1 ulp off in ~0.01% of cases. Error squares each NR step (book table 8.8). |
| Why the `cmd` module? | Standard Python: each `do_xxx` method becomes a command, and `help` is built from the docstrings. |

---

## Questions anyone may get

- **Draw the 6-stage pipeline diagram for `ld r1,0[r2]; add r3,r1,r1`.** Use `diag` on `hazard.s` to practise.
- **CPI formula?** cycles ÷ instructions. **Time?** instructions × CPI × clock period.
- **Why did all four models give identical results?** They share the ISA, ALU and memory; only timing/control differ. `bench.py` part 4 checks this.
