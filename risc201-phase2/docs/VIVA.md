# Viva prep — one section per member

Each member should be able to (1) explain their part in ~1 minute, (2) run its demo, (3) walk through the named functions line by line.

---

## Member A — ISA + assembler + disassembler + macros (2a, 2b, 2e)

**Files:** `isa.py`, `assembler.py`, `disassembler.py`, `preprocessor.py`
**Diagram:** `instruction_formats`
**Code to know cold:** `isa.encode`, `isa.decode`, `assembler.assemble` (both passes), `assembler.encode_line`, `disassembler.disassemble`, `preprocessor.expand_macros`

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

## Member C — control unit design + single-cycle CPU (1b, 2d simulator)

**Files:** `microcode.py`, `cpu.py` (`microcpu.py` = item 3, later)
**Diagram:** `control_unit`
**Code to know cold:** `SingleCycleCPU.step` (fetch, decode, execute, memory, write back), `ControlMemory.encode` / `decode`, `check_rom`, the `HORIZONTAL` / `VERTICAL` tables

**1-minute pitch:** This CPU has no hardwired control. Each cycle it reads one microinstruction from control memory at microPC, turns on its control signals and computes the next microPC. Opcodes are only used by DISPATCH to jump to a routine. Horizontal words have one bit per signal (wide, fast); vertical words hold one encoded micro-op (narrow, slower, needs a decoder).

**Demo:** `python cli.py examples/fact.s --mode single`, then `step 5` (each line shows what changed). Then `python microcode.py` for both control memories.

**Likely questions**

| Question | Answer |
|---|---|
| Horizontal vs vertical in one line? | Horizontal = more bits per word, fewer steps. Vertical = fewer bits, more steps, needs a decoder. |
| Your numbers? | Horizontal 26×26 = 676 bits, CPI ≈ 5. Vertical 36×14 = 504 bits, CPI ≈ 6.6. |
| What is DISPATCH? | Next microPC = start of the routine for this opcode (Sarangi's `mswitch`). |
| Why can't A_RS1 and ALU_OP share a word? | They happen at the same clock edge; the ALU would read the old A. `check_rom` rejects it. |
| Why can RA_LINK and PC_BRANCH share a word? | RA_LINK reads the old PC, PC_BRANCH writes the new one — same edge, no conflict. |
| How do mov/not reuse the ALU routine? | MOVNOT loads B then JUMPs to ALU_GO. |
| What raises an exception? | Unknown opcode at DISPATCH, microPC outside the ROM. |
| What is the single-cycle CPU for? | One whole instruction per step, no overlap: the simplest correct model. The pipelines and microcode must end in the same state as it. |
| Why is its CPI exactly 1? | Every instruction takes one (long) cycle; the clock period must fit the slowest instruction (ld: fetch + decode + ALU + memory + write back). |

---

## Member D — ALU + CLI debugger + stack visualizer (1c, 2c, 2d, 2g)

**Files:** `alu.py`, `cli.py`, `stackview.py`
**Diagram:** `alu`
**Code to know cold:** `ALU.compute`, `ALU._cla`, `ALU._booth`, `ALU._nonrestoring`, `Debugger.do_continue`, `Debugger.guarded`, `Debugger.do_watch`

**1-minute pitch:** Every arithmetic, logic and shift operation in all four processor models goes through one ALU class. The adder, multiplier and divider algorithms can be swapped, and the ALU counts their work so we can compare them. The CLI debugger drives any processor model through the same small set of methods (`step`, `show`, `stats`), and adds breakpoints, watches, memory and register inspection and editing.

**Demo:** `python cli.py examples/sum.s --mode pipe4`, then `watch r1`, `c`, `c`, `regs`, `mem array 4`, `stats`. Then `python cli.py examples/muldiv.s --run --mul shiftadd` vs `--mul booth`, and compare `alu.mul_addsub` in the output.

**Likely questions**

| Question | Answer |
|---|---|
| Why is CLA faster? | Each block's carries come straight from g, p and the carry-in, not bit by bit. 18 vs 64 gate delays. |
| When does Booth help? | Runs of 1s (e.g. negative multipliers): 4.7 vs 28 add/subs. On random data they are equal. |
| Restoring vs non-restoring? | Non-restoring never adds back after a negative result; the next step adds instead. ~33 vs ~61 operations. |
| How is subtraction done? | A + (~B) + 1 through the same adder (`ALU.sub`). |
| How does cmp set GT? | Uses the subtractor; E = result is zero, GT = signed A > B. |
| How does one CLI work for pipelines and microcode? | Both classes have the same methods (`step`, `show`, `stats`, `halted`), so the CLI does not care which one it has. |
| How does a watch work? | Before running, save the watched values; after every cycle compare; stop on a change. |
| Why is the breakpoint checked in EX, not IF? | IF may fetch wrong-path instructions that get flushed; EX only sees instructions that really execute. |
| What happens on an exception? | `guarded()` catches it, prints the PC and source line, and blocks further stepping until `reset`. |
| How does the stack view find frames? | `Machine.frames` records the sp at every `call`; the renderer draws a rule at each of those addresses. |
| Why draw high addresses at the top? | The stack descends, so sp at the bottom of the picture matches how the stack is usually drawn in the textbook. |
| Why the `cmd` module? | Standard Python: each `do_xxx` method becomes a command, and `help` is built from the docstrings. |

---

## Questions anyone may get

- **Draw the 6-stage pipeline diagram for `ld r1,0[r2]; add r3,r1,r1`.** Use `diag` on `hazard.s` to practise.
- **CPI formula?** cycles ÷ instructions. **Time?** instructions × CPI × clock period.
- **Why did all four models give identical results?** They share the ISA, ALU and memory; only timing/control differ. `bench.py` part 4 checks this.
