@ fp.s - IEEE 754 floating point (our extension). Use `fregs` to read r1..r7 as floats.
@ Answers:  r1 = 19.635     area of a circle, radius 2.5
@           r3 = 1.0000001  0.1 added ten times - NOT exactly 1.0
@           r4 = 0          so fcmp finds r3 != 1.0
@           r6 = 5          ftoi(16 / 3.0) = ftoi(5.333) = 5
@           r7 = inf        1e38 x 10 overflows -> FPU overflow flag
@ Shows: lif (load float constant), fmul, fadd, fdiv, fcmp, itof, ftoi,
@ rounding error, overflow to infinity.

        lif  r1, 3.1415927
        lif  r2, 2.5
        fmul r1, r1, r2
        fmul r1, r1, r2         @ pi x r x r

        lif  r2, 0.1
        mov  r3, 0              @ integer 0 has the same bits as +0.0
        mov  r5, 10
add01:  fadd r3, r3, r2         @ 0.1 cannot be stored exactly in binary
        sub  r5, r5, 1
        cmp  r5, 0
        bgt  add01
        lif  r2, 1.0
        mov  r4, 1
        fcmp r3, r2
        beq  same
        mov  r4, 0              @ not equal: the rounding errors added up
same:   mov  r5, 3              @ average of 3, 5, 8 ...
        add  r5, r5, 5
        add  r5, r5, 8          @ 16
        itof r5, r5             @ 16.0
        lif  r2, 3.0
        fdiv r5, r5, r2         @ 5.333
        ftoi r6, r5             @ 5   (rounds towards zero)
        lif  r7, 1e38
        lif  r2, 10.0
        fmul r7, r7, r2         @ too big for a float -> +inf
        hlt
