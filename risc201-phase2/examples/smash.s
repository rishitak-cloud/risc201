@ smash.s - stack corruption. The function writes past its local
@ variable and overwrites its own saved return address. With the guard
@ ON, `ret` is caught with a ProtectionFault instead of jumping into data.

        call  victim
        hlt
victim: push  {ra}             @ saved ra is at 0[sp]
        sub   sp, sp, 4        @ one local word at 0[sp]; saved ra now at 4[sp]
        movu  r1, 0xBEEF
        st    r1, 0[sp]        @ fine: the local variable
        st    r1, 4[sp]        @ BUG: one word too far - overwrites saved ra
        add   sp, sp, 4
        pop   {ra}
        ret                    @ ret to 0xBEEF -> ProtectionFault
