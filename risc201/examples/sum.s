@ sum.s - add up an array of 10 numbers. Answer: r1 = 55
@ Shows: ld, loop with cmp + bgt, a load-use hazard every iteration.

        mov  r1, 0          @ r1 = running sum
        mov  r2, array      @ r2 = address of the array (label as immediate)
        mov  r3, 10         @ r3 = how many numbers are left
loop:   ld   r4, 0[r2]      @ r4 = *r2
        add  r1, r1, r4     @ uses r4 right after the load -> load-use hazard
        add  r2, r2, 4      @ next word
        sub  r3, r3, 1
        cmp  r3, 0
        bgt  loop           @ taken 9 times -> flushes
        hlt

array:  .word 1, 2, 3, 4, 5, 6, 7, 8, 9, 10
