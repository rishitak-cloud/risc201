@ overflow.s - recursion that never stops. With the stack guard ON the
@ simulator raises StackOverflow when sp crosses the stack limit.
@ Run with --no-guard to see what happens without protection.

        call  forever
        hlt
forever:
        push  {ra}
        call  forever
