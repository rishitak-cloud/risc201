import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import isa
from assembler import assemble, to_hex, AsmError
from disassembler import disassemble, read_words
from preprocessor import expand_macros


def one_word(line):
    """Assemble a single line (with an 'end' label after it for branches)."""
    return assemble(f"    {line}\nend: hlt\n").words[0]


def round_trip(source):
    """assemble -> hex text -> disassemble -> assemble again. Returns both word lists."""
    first = assemble(source)
    words, code_end = read_words(to_hex(first))
    listing = '\n'.join(disassemble(words, code_end))
    second = assemble(listing)
    return first.words, second.words


# ---------------------------------------------------------------------------
# 1. One test per instruction: assemble it, decode it, check the fields
# ---------------------------------------------------------------------------
# name -> (source line, expected decoded fields)
SAMPLES = {
    'add': ('add r1, r2, r3', dict(I=0, rd=1, rs1=2, rs2=3)),
    'sub': ('sub r4, r5, r6', dict(I=0, rd=4, rs1=5, rs2=6)),
    'mul': ('mul r1, r2, 7',  dict(I=1, rd=1, rs1=2, imm16=7)),
    'div': ('div r1, r2, r3', dict(I=0, rd=1, rs1=2, rs2=3)),
    'mod': ('mod r1, r2, r3', dict(I=0, rd=1, rs1=2, rs2=3)),
    'cmp': ('cmp r1, r2',     dict(I=0, rs1=1, rs2=2)),
    'and': ('and r1, r2, r3', dict(I=0, rd=1, rs1=2, rs2=3)),
    'or':  ('or r1, r2, r3',  dict(I=0, rd=1, rs1=2, rs2=3)),
    'not': ('not r1, r2',     dict(I=0, rd=1, rs2=2)),
    'mov': ('mov r1, -5',     dict(I=1, rd=1, imm16=0xFFFB)),
    'lsl': ('lsl r1, r2, 3',  dict(I=1, rd=1, rs1=2, imm16=3)),
    'lsr': ('lsr r1, r2, 3',  dict(I=1, rd=1, rs1=2, imm16=3)),
    'asr': ('asr r1, r2, r3', dict(I=0, rd=1, rs1=2, rs2=3)),
    'nop': ('nop', {}),
    'ld':  ('ld r1, 8[sp]',   dict(I=1, rd=1, rs1=isa.SP, imm16=8)),
    'st':  ('st r1, -4[r2]',  dict(I=1, rd=1, rs1=2, imm16=0xFFFC)),
    'beq': ('beq end', dict(offset=1)),
    'bgt': ('bgt end', dict(offset=1)),
    'b':   ('b end',   dict(offset=1)),
    'call': ('call end', dict(offset=1)),
    'ret': ('ret', {}),
    'fadd': ('fadd r1, r2, r3', dict(I=0, rd=1, rs1=2, rs2=3)),
    'fsub': ('fsub r1, r2, r3', dict(I=0, rd=1, rs1=2, rs2=3)),
    'fmul': ('fmul r1, r2, r3', dict(I=0, rd=1, rs1=2, rs2=3)),
    'fdiv': ('fdiv r1, r2, r3', dict(I=0, rd=1, rs1=2, rs2=3)),
    'fcmp': ('fcmp r1, r2',     dict(I=0, rs1=1, rs2=2)),
    'itof': ('itof r1, r2',     dict(I=0, rd=1, rs2=2)),
    'ftoi': ('ftoi r1, r2',     dict(I=0, rd=1, rs2=2)),
    'fsqrt': ('fsqrt r1, r2',   dict(I=0, rd=1, rs2=2)),
    'froot': ('froot r1, r2, 3', dict(I=1, rd=1, rs1=2, imm16=3)),
    'hlt': ('hlt', {}),
}


class OneTestPerInstruction(unittest.TestCase):
    def test_every_opcode_has_a_sample(self):
        # if you add an instruction to isa.OPCODES, this reminds you to test it
        self.assertEqual(set(SAMPLES), set(isa.OPCODES))

    def test_each_instruction(self):
        for name, (line, fields) in SAMPLES.items():
            with self.subTest(instruction=name):
                d = isa.decode(one_word(line))
                self.assertEqual(d['name'], name)
                for key, expected in fields.items():
                    self.assertEqual(d[key], expected, f"{name}: field {key}")

    def test_opcodes_are_unique_and_fit_5_bits(self):
        codes = list(isa.OPCODES.values())
        self.assertEqual(len(codes), len(set(codes)))
        self.assertTrue(all(0 <= c < 32 for c in codes))


class ImmediateModifiers(unittest.TestCase):
    def test_default_is_sign_extended(self):
        self.assertEqual(isa.decode(one_word('add r1, r2, -1'))['imm'], 0xFFFFFFFF)

    def test_u_is_zero_extended(self):
        d = isa.decode(one_word('addu r1, r2, 0xFFFF'))
        self.assertEqual((d['mod'], d['imm']), (isa.MOD_U, 0xFFFF))

    def test_h_goes_to_upper_half(self):
        d = isa.decode(one_word('movh r1, 0x1234'))
        self.assertEqual((d['mod'], d['imm']), (isa.MOD_H, 0x12340000))

    def test_float_h_form(self):
        # fmulh r1, r1, 0x4000 multiplies by 2.0 (0x40000000)
        self.assertEqual(isa.decode(one_word('fmulh r1, r1, 0x4000'))['imm'], 0x40000000)


class Branches(unittest.TestCase):
    def test_forward_and_backward_offsets(self):
        prog = assemble("top: nop\n b end\n b top\nend: hlt\n")
        self.assertEqual(isa.decode(prog.words[1])['offset'], 2)    # forward
        self.assertEqual(isa.decode(prog.words[2])['offset'], -2)   # backward

    def test_label_as_immediate(self):
        prog = assemble("mov r1, data\nhlt\ndata: .word 5\n")
        self.assertEqual(isa.decode(prog.words[0])['imm16'], prog.symbols['data'])


# ---------------------------------------------------------------------------
# 2. Macros
# ---------------------------------------------------------------------------
class Macros(unittest.TestCase):
    def texts(self, source):
        return [t.strip() for _, t in expand_macros(source)]

    def test_push_list(self):
        self.assertEqual(self.texts("push {r2, ra}"),
                         ["sub sp, sp, 8", "st r2, 4[sp]", "st ra, 0[sp]"])

    def test_pop_list_matches_push(self):
        self.assertEqual(self.texts("pop {r2, ra}"),
                         ["ld r2, 4[sp]", "ld ra, 0[sp]", "add sp, sp, 8"])

    def test_push_single_register(self):
        self.assertEqual(self.texts("push r1"), ["sub sp, sp, 4", "st r1, 0[sp]"])

    def test_label_survives_macro(self):
        prog = assemble("f: push {ra}\n b f\n")
        self.assertEqual(prog.symbols['f'], 0)

    def test_li_matches_manual_expansion(self):
        self.assertEqual(assemble("li r1, 0x12345678\n").words,
                         assemble("movh r1, 0x1234\noru r1, r1, 0x5678\n").words)

    def test_li_negative(self):
        self.assertEqual(assemble("li r1, -1\n").words,
                         assemble("movh r1, 0xffff\noru r1, r1, 0xffff\n").words)

    def test_lif_uses_ieee754_bits(self):
        # 2.5 -> 0x40200000
        self.assertEqual(assemble("lif r1, 2.5\n").words,
                         assemble("movh r1, 0x4020\noru r1, r1, 0x0\n").words)

    def test_macros_always_two_or_more_words(self):
        self.assertEqual(len(assemble("li r1, 5\n").words), 2)
        self.assertEqual(len(assemble("lif r1, 1.0\n").words), 2)


# ---------------------------------------------------------------------------
# 3. Error handling
# ---------------------------------------------------------------------------
class Errors(unittest.TestCase):
    def assertAsmError(self, source, line_no=None):
        with self.assertRaises(AsmError) as ctx:
            assemble(source)
        if line_no is not None:
            self.assertEqual(ctx.exception.line_no, line_no)

    def test_unknown_instruction(self):
        self.assertAsmError("nop\nfoo r1, r2\n", 2)

    def test_bad_register(self):
        self.assertAsmError("add r1, r2, r16\n", 1)

    def test_immediate_too_big(self):
        self.assertAsmError("add r1, r2, 40000\n", 1)

    def test_unsigned_immediate_too_big(self):
        self.assertAsmError("addu r1, r2, 0x10000\n", 1)

    def test_wrong_operand_count(self):
        self.assertAsmError("add r1, r2\n", 1)

    def test_undefined_label(self):
        self.assertAsmError("b nowhere\n", 1)

    def test_duplicate_label(self):
        self.assertAsmError("a: nop\na: nop\n", 2)

    def test_reserved_label(self):
        self.assertAsmError("sp: nop\n", 1)

    def test_suffix_with_register(self):
        self.assertAsmError("addu r1, r2, r3\n", 1)

    def test_space_must_be_multiple_of_4(self):
        self.assertAsmError(".space 6\n", 1)


# ---------------------------------------------------------------------------
# 4. Round trip:  .s -> .hex -> .s -> .hex  must give the same words
# ---------------------------------------------------------------------------
FACTORIAL = """
        mov  r1, 5
        call fact
        hlt
fact:   push {r2, ra}
        cmp  r1, 1
        bgt  recurse
        mov  r1, 1
        b    done
recurse: mov r2, r1
        sub  r1, r1, 1
        call fact
        mul  r1, r1, r2
done:   pop  {r2, ra}
        ret
table:  .word 1, 2, 0x10
        .float 3.5, -0.25
        .space 8
"""

FLOAT_PROGRAM = """
        lif   r1, 2.5
        lif   r2, 4.0
        fadd  r3, r1, r2
        fmulh r3, r3, 0x4000
        fcmp  r3, r2
        itof  r4, r1
        ftoi  r5, r3
        hlt
"""


class RoundTrip(unittest.TestCase):
    def test_factorial(self):
        a, b = round_trip(FACTORIAL)
        self.assertEqual(a, b)

    def test_float_program(self):
        a, b = round_trip(FLOAT_PROGRAM)
        self.assertEqual(a, b)

    def test_every_instruction(self):
        source = '\n'.join(line for line, _ in SAMPLES.values()) + "\nend: hlt\n"
        a, b = round_trip(source)
        self.assertEqual(a, b)

    def test_random_programs(self):
        # Property test: build random (valid) instructions, print them with
        # isa.format_instr, assemble the text, expect the same word back.
        rng = random.Random(201)
        straight = sorted(n for n in isa.OPCODES if n not in isa.BRANCH)
        for _ in range(3000):
            name = rng.choice(straight)
            word = random_word(rng, name)
            text = isa.format_instr(isa.decode(word))
            with self.subTest(text=text):
                self.assertEqual(assemble(text + "\n").words[0], word)


def random_word(rng, name):
    """A random instruction in canonical form (unused fields are zero)."""
    reg = lambda: rng.randrange(16)
    if name in isa.NO_OPERAND:
        return isa.encode(name)
    if name in isa.MEMORY:
        return isa.encode(name, rd=reg(), rs1=reg(), imm=rng.randrange(-32768, 32768))
    rd, rs1, rs2 = reg(), reg(), reg()
    if name in isa.TWO_ADDR:
        rs1 = 0
    if name in isa.COMPARE:
        rd = 0
    if rng.random() < 0.5:
        return isa.encode(name, rd=rd, rs1=rs1, rs2=rs2)
    mod = rng.choice([isa.MOD_DEFAULT, isa.MOD_U, isa.MOD_H])
    imm = rng.randrange(-32768, 32768) if mod == isa.MOD_DEFAULT else rng.randrange(65536)
    return isa.encode(name, rd=rd, rs1=rs1, imm=imm, mod=mod)


# ---------------------------------------------------------------------------
# 5. Fuzzing: random 32-bit words must never crash the decoder / disassembler
# ---------------------------------------------------------------------------
class Fuzz(unittest.TestCase):
    def test_random_words_never_crash(self):
        rng = random.Random(7)
        words = [rng.getrandbits(32) for _ in range(20000)]
        # also force every valid opcode, so the interesting paths are hit often
        for name, code in isa.OPCODES.items():
            words += [(code << 27) | rng.getrandbits(27) for _ in range(500)]
        for w in words:
            d = isa.decode(w)
            isa.format_instr(d)
            isa.format_instr(d, pc=0x100)
            isa.source_regs(d)
            isa.dest_reg(d)

    def test_disassemble_whole_image(self):
        rng = random.Random(11)
        words = [rng.getrandbits(32) for _ in range(2000)]
        lines = disassemble(words, code_end=len(words) * 4)
        self.assertEqual(sum(1 for l in lines if '@' in l), len(words))

    def test_reserved_modifier_is_illegal(self):
        # mod bits 11 are reserved: decoded as "no instruction", shown as .word
        word = isa.encode('add', rd=1, rs1=2, imm=5, mod=isa.MOD_DEFAULT) | (0b11 << 16)
        self.assertIsNone(isa.decode(word)['name'])
        self.assertTrue(isa.format_instr(isa.decode(word)).startswith('.word'))

    def test_empty_and_tiny_images(self):
        self.assertEqual(disassemble([]), [])
        self.assertEqual(len(disassemble([isa.encode('hlt')])), 1)


if __name__ == '__main__':
    unittest.main()
