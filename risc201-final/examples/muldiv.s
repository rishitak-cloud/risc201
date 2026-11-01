@ muldiv.s - exercises the ALU algorithms, including negative numbers.
@ Answers: r3 = -42, r4 = -3, r5 = -1, r6 = 0x7F800000, r7 = -2 (asr), r8 = 0xFFFF1234
        mov   r1, -7
        mov   r2, 6
        mul   r3, r1, r2        @ -42
        mov   r1, -20
        div   r4, r1, r2        @ -20 / 6 = -3   (rounds towards zero)
        mod   r5, r1, 19        @ -20 mod 19 = -1 (sign of the dividend)
        movh  r6, 0x7F80        @ upper half immediate
        mov   r7, -8
        asr   r7, r7, 2         @ arithmetic shift keeps the sign: -2
        movu  r8, 0x1234        @ zero-extended low half
        orh   r8, r8, 0xFFFF    @ r8 = 0xFFFF1234
        mov   r9, 0x0F0F
        and   r9, r9, 0x00FF    @ 0x000F
        not   r10, r9           @ 0xFFFFFFF0
        lsl   r11, r9, 4        @ 0xF0
        lsr   r12, r10, 28      @ 0xF
        hlt
