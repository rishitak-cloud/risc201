@ matmul.s - 3x3 matrix multiply C = A x B, B has negative numbers.
@ Answers: r10 = C[0][0] = -14,  r11 = C[2][2] = -54
@ Shows: a multiply-heavy program with lots of ld / st -> compares
@ iterative vs Booth multipliers, and load-use stalls in the 6-stage.
@ C is written at 0x1000 (the code region is write protected).

        mov  r1, 0              @ i
iloop:  mov  r2, 0              @ j
jloop:  mov  r4, 0              @ sum
        mov  r3, 0              @ k
kloop:  mul  r5, r1, 12         @ address of A[i][k] = Am + 12i + 4k
        mul  r6, r3, 4
        add  r5, r5, r6
        ld   r8, Am[r5]
        mul  r5, r3, 12         @ address of B[k][j] = Bm + 12k + 4j
        mul  r6, r2, 4
        add  r5, r5, r6
        ld   r9, Bm[r5]
        mul  r8, r8, r9         @ A[i][k] * B[k][j]
        add  r4, r4, r8
        add  r3, r3, 1
        cmp  r3, 3
        beq  kdone
        b    kloop
kdone:  mul  r5, r1, 12         @ C[i][j] at 0x1000 + 12i + 4j
        mul  r6, r2, 4
        add  r5, r5, r6
        movu r7, 0x1000
        add  r5, r5, r7
        st   r4, 0[r5]
        add  r2, r2, 1
        cmp  r2, 3
        beq  jdone
        b    jloop
jdone:  add  r1, r1, 1
        cmp  r1, 3
        beq  idone
        b    iloop
idone:  movu r7, 0x1000
        ld   r10, 0[r7]         @ C[0][0]
        ld   r11, 32[r7]        @ C[2][2]
        hlt

Am:     .word 1, 2, 3, 4, 5, 6, 7, 8, 9
Bm:     .word -1, 2, -3, 4, -5, 6, -7, 8, -9
