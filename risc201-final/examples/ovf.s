@ ovf.s - signed integer overflow.
@ Default (wrap):         r2 = -2147483648 and the V flag is 1 (see `regs`)
@ With --trap-overflow:   ArithmeticOverflow is raised at the add
        li   r1, 0x7FFFFFFF     @ the largest positive 32-bit integer
        add  r2, r1, 1          @ +2^31 does not fit: wraps to -2^31
        hlt
