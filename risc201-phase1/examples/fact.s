@ fact.s - recursive factorial using the stack by hand. Answer: r1 = 720 (6!)
@ Shows: call / ret, saving registers on a full-descending stack
@ (sp grows down and points at the last word pushed), load-use after ld.

        mov   r2, 6         @ n = 6
        call  fact          @ r1 = fact(n)
        hlt

@ fact(n): argument in r2, result in r1
fact:   sub   sp, sp, 8     @ make room for 2 words
        st    r2, 4[sp]     @ save n
        st    ra, 0[sp]     @ save the return address
        cmp   r2, 1
        bgt   recurse       @ n > 1 -> recursive case
        mov   r1, 1         @ base case: fact(1) = 1
        b     done
recurse:
        sub   r2, r2, 1
        call  fact          @ r1 = fact(n - 1)
        ld    r2, 4[sp]     @ get our own n back
        mul   r1, r1, r2    @ r1 = fact(n - 1) * n
done:   ld    r2, 4[sp]     @ restore n
        ld    ra, 0[sp]     @ restore the return address
        add   sp, sp, 8     @ give the space back
        ret
