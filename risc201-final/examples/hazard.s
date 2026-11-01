@ hazard.s - small program built to show every hazard. Answer: r5 = 40
@ Run it with --mode pipe4 and --mode pipe6 and compare `diag`.

        mov  r1, data
        ld   r2, 0[r1]      @ r2 = 10
        add  r3, r2, r2     @ load-use: 6-stage stalls 1 cycle, 4-stage does not
        add  r4, r3, r3     @ ALU -> ALU: forwarded, no stall in either
        ld   r5, 4[r1]      @ r5 = 0
        add  r5, r5, r4     @ load-use again
        cmp  r5, 40
        beq  good           @ taken: 2 flushed (4-stage) / 3 flushed (6-stage)
        mov  r5, 0          @ never runs
good:   hlt

data:   .word 10, 0
