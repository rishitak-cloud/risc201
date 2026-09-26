"""
exceptions.py - runtime exceptions of the RISC201 machine

When the simulated program does something illegal, the processor raises
one of these. The CLI catches it, stops, and shows where it happened.

    RiscError                   base class, carries the PC
      IllegalInstruction        opcode that does not exist
      MemoryFault               address outside memory
      AlignmentFault            word access to an address not divisible by 4
      ProtectionFault           write into the code region
      DivideByZero

(The stack-specific exceptions belong to item 2f and come later.)
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
class DivideByZero(RiscError): pass
