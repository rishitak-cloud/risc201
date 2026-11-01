@ fib.s - Fibonacci, iterative. Answer: r1 = 6765 (fib(20))
@ Shows: a tight ALU-only loop (no memory), long chains of dependent adds
@ -> exercises forwarding; one taken branch per iteration.

        mov  r1, 0          @ fib(i)
        mov  r2, 1          @ fib(i+1)
        mov  r4, 20         @ how many steps
loop:   add  r3, r1, r2     @ next = a + b
        mov  r1, r2         @ a = b       (needs r2: forwarded)
        mov  r2, r3         @ b = next    (needs r3: forwarded)
        sub  r4, r4, 1
        cmp  r4, 0
        bgt  loop
        hlt
