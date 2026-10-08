@ mul_compare.s - test that shows different mul_addsub counts
@ between shift-add and Booth's algorithm.
@
@ Multiplying by 15 (0b1111):
@   shift-add  ->  4 add steps  (one per 1-bit)
@   booth      ->  2 add/sub    (sub at bit0, add at bit4)
@
@ Multiplying by 31 (0b11111):
@   shift-add  ->  5 add steps
@   booth      ->  2 add/sub
@
@ Expected: r3 = 3*15 = 45, r4 = 3*31 = 93

        mov   r1, 3
        mov   r2, 15
        mul   r3, r1, r2        @ 3 * 15 = 45
        mov   r2, 31
        mul   r4, r1, r2        @ 3 * 31 = 93
        hlt
