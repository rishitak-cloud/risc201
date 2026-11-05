# RISC201 ISA

RISC201 follows the **SimpleRisc** instruction set. All encodings live in `isa.py`.

## Registers

| Name | Use |
|---|---|
| r0 – r13 | general purpose (r0 is a normal register, not hardwired to zero) |
| r14 = sp | stack pointer, starts at 0x10000 |
| r15 = ra | return address, written by `call` |
| flags | two bits, **E** and **GT**, written only by `cmp` |

32-bit registers, byte-addressed memory, `ld`/`st` move one aligned 32-bit word.

## Instruction formats (bit 31 on the left)

```
Register   | opcode 5 | I=0 | rd 4 | rs1 4 | rs2 4 |    unused 14     |
Immediate  | opcode 5 | I=1 | rd 4 | rs1 4 | mod 2 |      imm 16      |
Branch     | opcode 5 |               offset 27 (words)                 |
```

Branch target = PC of the branch + offset × 4.

### Immediate modifiers

| Suffix | mod | 32-bit value | Example |
|---|---|---|---|
| none | 00 | sign-extended | `add r1, r2, -5` |
| `u` | 01 | zero-extended | `movu r1, 0xFFFF` → 0x0000FFFF |
| `h` | 10 | placed in upper 16 bits | `movh r1, 0x1234` → 0x12340000 |

Loading any 32-bit constant: `movh r1, HI` then `oru r1, r1, LO`.

## Instructions

| Opcode | Instr | Syntax | Meaning |
|---|---|---|---|
| 00000 | add | `add rd, rs1, rs2/imm` | rd = rs1 + op2 |
| 00001 | sub | `sub rd, rs1, rs2/imm` | rd = rs1 − op2 |
| 00010 | mul | `mul rd, rs1, rs2/imm` | rd = low 32 bits of rs1 × op2 |
| 00011 | div | `div rd, rs1, rs2/imm` | rd = rs1 / op2 (rounds towards 0) |
| 00100 | mod | `mod rd, rs1, rs2/imm` | rd = remainder (sign of rs1) |
| 00101 | cmp | `cmp rs1, rs2/imm` | E = (rs1 == op2), GT = (rs1 > op2), signed |
| 00110 | and | `and rd, rs1, rs2/imm` | bitwise AND |
| 00111 | or | `or rd, rs1, rs2/imm` | bitwise OR |
| 01000 | not | `not rd, rs2/imm` | rd = ~op2 |
| 01001 | mov | `mov rd, rs2/imm` | rd = op2 |
| 01010 | lsl | `lsl rd, rs1, rs2/imm` | logical shift left |
| 01011 | lsr | `lsr rd, rs1, rs2/imm` | logical shift right |
| 01100 | asr | `asr rd, rs1, rs2/imm` | arithmetic shift right (keeps sign) |
| 01101 | nop | `nop` | nothing |
| 01110 | ld | `ld rd, imm[rs1]` | rd = MEM[rs1 + imm] |
| 01111 | st | `st rd, imm[rs1]` | MEM[rs1 + imm] = rd |
| 10000 | beq | `beq label` | branch if E = 1 |
| 10001 | bgt | `bgt label` | branch if GT = 1 |
| 10010 | b | `b label` | always branch |
| 10011 | call | `call label` | ra = PC + 4, branch |
| 10100 | ret | `ret` | PC = ra |
| 10101 | fadd | `fadd rd, rs1, rs2` | IEEE 754 float add (**extension**) |
| 10110 | fsub | `fsub rd, rs1, rs2` | float subtract (**extension**) |
| 10111 | fmul | `fmul rd, rs1, rs2` | float multiply (**extension**) |
| 11000 | fdiv | `fdiv rd, rs1, rs2` | float divide (**extension**) |
| 11001 | fcmp | `fcmp rs1, rs2` | float compare → E, GT (**extension**) |
| 11010 | itof | `itof rd, rs2` | integer → float (**extension**) |
| 11011 | ftoi | `ftoi rd, rs2` | float → integer, towards zero, saturating (**extension**) |
| 11100 | fsqrt | `fsqrt rd, rs2` | square root, correctly rounded (**extension**) |
| 11101 | froot | `froot rd, rs1, n` | n-th root, n = 1..255 from an immediate or register, correctly rounded (**extension**) |
| 11111 | hlt | `hlt` | **our extension**: stop the simulator |

## Assembler extras

| Feature | Example |
|---|---|
| labels, forward references | `b end` … `end: hlt` |
| label as immediate | `mov r2, array` |
| data | `.word 1, 2, 0x10` / `.space 16` |
| stack macros (preprocessor) | `push {r2, ra}` / `pop {r2, ra}` |
| any 32-bit constant (macro → `movh` + `oru`) | `li r1, 0x12345678` |
| any float constant (macro → `movh` + `oru`) | `lif r1, 2.5` |
| float data | `.float 3.14, -0.5` |
| comments | `@`, `;` or `//` |

## Where our RISC201 might differ from the professor's

Check these against any handout before the evaluation. Each one is a one-line change in `isa.py`.

- **Opcode numbers**: we used the order of the textbook's table.
- **`hlt`**: not in SimpleRisc. We added it at opcode 11111.
- **Stage split**: 4-stage and 6-stage are our choice of merging / splitting the classic 5 stages (see DESIGN.md).
- **Shift by ≥ 32**: we give 0 (or all sign bits for `asr`).

## Our extensions beyond SimpleRisc

### Overflow flag V

SimpleRisc has only E and GT. We add **V** (signed overflow), set by `add`, `sub`, `mul`, `div` and cleared when they don't overflow. `cmp` and `ld`/`st` address arithmetic never touch it.

| Policy | How | Like |
|---|---|---|
| wrap (default) | keep the low 32 bits, set V | C, RISC-V, ARM's V flag |
| trap (`--trap-overflow`) | raise `ArithmeticOverflow` at the exact PC | MIPS `add` |

### Floating point

IEEE 754 single precision on the ordinary registers (no separate float register file — the same idea as RISC-V's Zfinx). A register just holds a bit pattern; `fadd` reads it as a float, `add` reads it as an integer.

The `h` modifier gives a 16-bit float constant for free: `fmulh r1, r1, 0x4000` multiplies by 2.0 (0x40000000).

IEEE exceptions follow the standard's default: produce a special value and set a status flag, no trap.

| Status flag | Raised by | Result |
|---|---|---|
| invalid | 0/0, ∞−∞, 0×∞, √(negative), even root of a negative, root with n outside 1..255, compare with NaN, ftoi out of range | NaN (ftoi saturates) |
| divzero | x / 0 | ±∞ |
| overflow | result too large | ±∞ (or the largest float, by rounding mode) |
| underflow | tiny and inexact | denormal or 0 |
| inexact | rounding discarded bits | rounded value |

Rounding modes (book table 8.6): `--round nearest|zero|up|down`.
