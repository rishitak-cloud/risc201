@ gcd.s - Euclid's algorithm with mod. Answer: r1 = 21 (gcd(1071, 462))
@ Shows: a division-heavy program -> compares restoring vs non-restoring.

        mov  r1, 1071
        mov  r2, 462
loop:   cmp  r2, 0
        beq  done
        mod  r3, r1, r2     @ r3 = r1 mod r2
        mov  r1, r2
        mov  r2, r3
        b    loop
done:   hlt
