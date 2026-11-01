@ fact.s - recursive factorial with a real stack. Answer: r1 = 720 (6!)
@ Shows: call / ret, push / pop macros, stack frames, load-use after ld.

        mov   r2, 6         @ n = 6
        call  fact          @ r1 = fact(n)
        hlt

@ fact(n): argument in r2, result in r1
fact:   push  {r2, ra}      @ save n and the return address
        cmp   r2, 1
        bgt   recurse       @ n > 1 -> recursive case
        mov   r1, 1         @ base case: fact(1) = 1
        b     done
recurse:
        sub   r2, r2, 1
        call  fact          @ r1 = fact(n - 1)
        ld    r2, 4[sp]     @ get our own n back from the stack
        mul   r1, r1, r2    @ r1 = fact(n - 1) * n
done:   pop   {r2, ra}
        ret
