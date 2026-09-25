"""
exceptions.py - runtime exceptions of the RISC201 machine (Phase 2 - owner not assigned yet)

When the simulated program does something illegal, the simulator raises
one of these. The CLI catches it, stops, and shows where it happened.

    RiscError                   base class, carries the PC
      IllegalInstruction        opcode that does not exist
      MemoryFault               address outside memory
      AlignmentFault            word access to an address not divisible by 4
      ProtectionFault           writing into the code region / bad return address
      StackOverflow             sp went below the stack limit
      StackUnderflow            sp went above the stack base (popped too much)
      StackAccessViolation      touching stack memory below sp (unallocated)
      DivideByZero
      MicrocodeFault            microPC left the control memory
"""


class RiscError(Exception):
    def __init__(self, message, pc=None):
        self.pc = pc
        where = f" at pc={pc:#06x}" if pc is not None else ""
        super().__init__(f"{type(self).__name__}: {message}{where}")


class IllegalInstruction(RiscError): pass
class MemoryFault(RiscError): pass
class AlignmentFault(RiscError): pass
class ProtectionFault(RiscError): pass
class StackOverflow(RiscError): pass
class StackUnderflow(RiscError): pass
class StackAccessViolation(RiscError): pass
class DivideByZero(RiscError): pass
class MicrocodeFault(RiscError): pass
