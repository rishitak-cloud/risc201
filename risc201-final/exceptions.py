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
class ArithmeticOverflow(RiscError): pass
class MicrocodeFault(RiscError): pass
