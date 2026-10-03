@ bubble.s - bubble sort 8 numbers in memory (ascending).
@ Shows: nested loops, lots of ld / st, data outside the code region.
@ The array lives at 0x1000 so it is writable (code is write protected).

        movu r1, 0x1000         @ r1 = array base
        mov  r2, init           @ copy the initial values into the array
        mov  r3, 0
copy:   ld   r4, 0[r2]
        st   r4, 0[r1]
        add  r1, r1, 4
        add  r2, r2, 4
        add  r3, r3, 1
        cmp  r3, 8
        beq  sort
        b    copy

sort:   mov  r5, 7              @ r5 = number of passes left
outer:  movu r1, 0x1000         @ r1 = pointer into the array
        mov  r6, r5             @ r6 = compares in this pass
inner:  ld   r7, 0[r1]
        ld   r8, 4[r1]
        cmp  r7, r8
        bgt  swap
        b    next
swap:   st   r8, 0[r1]
        st   r7, 4[r1]
next:   add  r1, r1, 4
        sub  r6, r6, 1
        cmp  r6, 0
        bgt  inner
        sub  r5, r5, 1
        cmp  r5, 0
        bgt  outer
        movu r1, 0x1000         @ load the sorted answer into r1..r4 for checking
        ld   r9, 0[r1]          @ smallest
        ld   r10, 28[r1]        @ largest
        hlt

init:   .word 42, -3, 17, 8, 99, 0, 23, -15
