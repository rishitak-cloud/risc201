"""
pipeline.py - the 4-stage and 6-stage pipelined processors (Member B)

Both are made from Sarangi's 5-stage pipeline (Chapter 9):
          IF  OF  EX  MA  RW

  4-stage: IF | OF | EX+MA | RW          EX and MA merged into one stage
  6-stage: IF | ID | OF | EX | MA | RW   OF split into decode + register read

What each stage does here
  IF  fetch the word at PC, PC = PC + 4 (always predict "not taken")
  ID  decode: split the word into fields (6-stage only)
  OF  decode (4-stage) / read registers
  EX  ALU, compare (writes flags), branch decision, ld/st address
  MA  ld reads memory, st writes memory (inside EX in the 4-stage)
  RW  write the result into the register file

Hazards (in-order pipeline, full forwarding)
  * Data: EX takes its operands through the forwarding unit: the newest
    value from the MA latch, else the RW latch, else the register file.
  * Load-use (6-stage only): a ld in EX gets its data at the END of MA,
    too late for the next instruction's EX -> stall 1 cycle.
    In the 4-stage, ld finishes inside EX, so it never stalls.
  * Control: branches are decided in EX. If taken, the instructions
    fetched after it (everything before EX) are flushed:
    2 in the 4-stage (IF, OF), 3 in the 6-stage (IF, ID, OF).

One call to step() = one clock cycle:
  1. clock edge: every instruction moves one stage forward
     (or is held for a stall, or thrown away for a flush)
  2. every stage does its work, oldest instruction first
  3. look for hazards that decide what happens at the next edge
"""
import isa
from exceptions import MemoryFault, IllegalInstruction

STAGES = {4: ['IF', 'OF', 'EX', 'RW'],
          6: ['IF', 'ID', 'OF', 'EX', 'MA', 'RW']}


class Pipeline:
    def __init__(self, machine, stages=6):
        if stages not in STAGES:
            raise ValueError("stages must be 4 or 6")
        self.m = machine
        self.depth = stages
        self.names = STAGES[stages]
        self.ex_index = self.names.index('EX')
        self.has_ma = 'MA' in self.names
        self.slot = {n: None for n in self.names}   # instruction in each stage

        self.pc = 0
        self.cycle = 0
        self.retired = 0
        self.stalls = 0
        self.flushed = 0
        self.halted = False

        self.fetching = True        # turned off once hlt reaches EX
        self.stall_next = False     # load-use hazard found
        self.flush_next = False     # branch taken / hlt found
        self.redirect = None        # new PC after a taken branch

        self.events = []            # messages about this cycle
        self.breakpoints = set()
        self.hit_breakpoint = None
        self.seq = 0                # numbering of fetched instructions
        self.timeline = {}          # seq -> {cycle: stage}
        self.texts = {}             # seq -> assembly text
        self.squashed = set()

    # ------------------------------------------------------------------
    # IF
    # ------------------------------------------------------------------
    def fetch(self):
        pc = self.pc
        ins = {'seq': self.seq, 'pc': pc, 'word': None, 'd': None,
               'fault': None, 'srcs': [], 'dest': None, 'result': None}
        self.seq += 1
        if 0 <= pc < self.m.code_end:
            ins['word'] = self.m.mem.peek(pc)
        else:
            # Do not fail yet: this may be a wrong-path fetch that a branch
            # will flush. It only faults if it reaches EX.
            ins['fault'] = MemoryFault(f"instruction fetch from {pc:#x}, outside the code", pc)
        self.pc += 4
        self.texts[ins['seq']] = (isa.format_instr(isa.decode(ins['word']), pc, self.m.labels)
                                  if ins['word'] is not None else '(bad fetch)')
        return ins

    # ------------------------------------------------------------------
    # ID / OF : decode
    # ------------------------------------------------------------------
    def decode(self, ins):
        if ins['d'] is not None or ins['fault']:
            return
        d = isa.decode(ins['word'])
        ins['d'] = d
        if d['name'] is None:
            ins['fault'] = IllegalInstruction(f"opcode {d['opcode']:#07b} does not exist", ins['pc'])
            return
        ins['srcs'] = isa.source_regs(d)
        ins['dest'] = isa.dest_reg(d)

    # ------------------------------------------------------------------
    # Forwarding unit
    # ------------------------------------------------------------------
    def read_operand(self, r):
        """Newest value of register r for the instruction in EX."""
        for stage in ('MA', 'RW'):
            older = self.slot.get(stage)
            if older and older['dest'] == r:
                assert not (stage == 'MA' and older['d']['name'] == 'ld'), \
                    "load-use hazard was not stalled"
                self.events.append(f"forward {isa.reg_name(r)} from {stage}")
                return older['result']
        return self.m.regs.read(r)

    # ------------------------------------------------------------------
    # EX
    # ------------------------------------------------------------------
    def execute(self, ins):
        if ins['fault']:
            raise ins['fault']
        d, pc = ins['d'], ins['pc']
        if pc in self.breakpoints:          # checked in EX: never a wrong-path fetch
            self.hit_breakpoint = pc
        name = d['name']
        alu, flags = self.m.alu, self.m.flags
        self.m.pc_now = pc
        taken, target = False, None

        if name in isa.THREE_ADDR:
            a = self.read_operand(d['rs1'])
            b = d['imm'] if d['I'] else self.read_operand(d['rs2'])
            ins['result'] = alu.compute(name, a, b, pc)
        elif name in isa.TWO_ADDR:
            b = d['imm'] if d['I'] else self.read_operand(d['rs2'])
            ins['result'] = alu.compute(name, 0, b, pc)
        elif name == 'cmp':
            a = self.read_operand(d['rs1'])
            b = d['imm'] if d['I'] else self.read_operand(d['rs2'])
            flags.E, flags.GT = alu.compare(a, b)
        elif name in ('ld', 'st'):
            ins['addr'] = alu.add(self.read_operand(d['rs1']), d['imm'])
            if name == 'st':
                ins['store_value'] = self.read_operand(d['rd'])
        elif name in ('beq', 'bgt', 'b', 'call'):
            taken = (name in ('b', 'call') or
                     (name == 'beq' and flags.E) or
                     (name == 'bgt' and flags.GT))
            target = pc + 4 * d['offset']
            if name == 'call':
                ins['result'] = pc + 4                 # goes to ra in RW
        elif name == 'ret':
            target = self.read_operand(isa.RA)
            self.m.check_return(target, pc)
            taken = True
        elif name == 'hlt':
            self.fetching = False
            self.flush_next = True
            self.events.append("hlt in EX: stop fetching, drain the pipeline")
        # nop: nothing

        if taken:
            self.redirect = target
            self.flush_next = True
            self.events.append(f"{name} taken -> {target:#06x}, flush younger instructions")

        if not self.has_ma:                            # 4-stage: MA lives in EX
            self.memory_access(ins)

    # ------------------------------------------------------------------
    # MA
    # ------------------------------------------------------------------
    def memory_access(self, ins):
        name = ins['d']['name']
        self.m.pc_now = ins['pc']
        if name == 'ld':
            ins['result'] = self.m.mem.load(ins['addr'])
        elif name == 'st':
            self.m.mem.store(ins['addr'], ins['store_value'])

    # ------------------------------------------------------------------
    # RW
    # ------------------------------------------------------------------
    def write_back(self, ins):
        name = ins['d']['name']
        self.m.pc_now = ins['pc']
        if ins['dest'] is not None:
            self.m.regs.write(ins['dest'], ins['result'])
        if name == 'call':
            self.m.note_call(ins['pc'], ins['pc'] + 4 * ins['d']['offset'])
        elif name == 'ret':
            self.m.note_return()
        elif name == 'hlt':
            self.halted = True
        self.retired += 1

    # ------------------------------------------------------------------
    # One clock cycle
    # ------------------------------------------------------------------
    def step(self):
        if self.halted:
            return
        self.cycle += 1
        self.events = []
        self.hit_breakpoint = None
        s, names, ex = self.slot, self.names, self.ex_index

        # 1. clock edge ------------------------------------------------
        if self.flush_next:
            for n in names[:ex]:                        # IF .. OF
                if s[n]:
                    self.squashed.add(s[n]['seq'])
                    if self.redirect is not None:       # count branch penalties only
                        self.flushed += 1
                    s[n] = None
            if self.redirect is not None:
                self.pc = self.redirect
            self.flush_next, self.redirect = False, None

        if self.stall_next:
            for i in range(len(names) - 1, ex, -1):     # EX and later move on
                s[names[i]] = s[names[i - 1]]
            s['EX'] = None                              # bubble
            self.stalls += 1
            self.stall_next = False
            self.events.append("stall: bubble inserted into EX")
        else:
            for i in range(len(names) - 1, 0, -1):
                s[names[i]] = s[names[i - 1]]
            s['IF'] = self.fetch() if self.fetching else None

        # 2. stage work, oldest first ----------------------------------
        if s['RW']:
            self.write_back(s['RW'])
        if self.has_ma and s['MA']:
            self.memory_access(s['MA'])
        if s['EX']:
            self.execute(s['EX'])
        for n in names[1:ex]:                           # ID / OF
            if s[n]:
                self.decode(s[n])

        # 3. hazards for the next edge ---------------------------------
        if self.has_ma and not self.flush_next:
            producer, consumer = s['EX'], s['OF']
            if (producer and consumer and producer['d']['name'] == 'ld'
                    and producer['dest'] in consumer['srcs']):
                self.stall_next = True
                self.events.append(f"load-use hazard on {isa.reg_name(producer['dest'])}: "
                                   f"stall next cycle")

        for n in names:
            if s[n]:
                self.timeline.setdefault(s[n]['seq'], {})[self.cycle] = n

    def run(self, max_cycles=200000):
        while not self.halted:
            if self.cycle >= max_cycles:
                raise RuntimeError(f"no hlt after {max_cycles} cycles (infinite loop?)")
            self.step()

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------
    def stats(self):
        return {
            'cycles': self.cycle,
            'instructions': self.retired,
            'CPI': round(self.cycle / self.retired, 3) if self.retired else 0,
            'stalls': self.stalls,
            'flushed': self.flushed,
        }

    def show(self):
        lines = [f"cycle {self.cycle}   ({self.depth}-stage)   next fetch pc={self.pc:#06x}"]
        for n in self.names:
            ins = self.slot[n]
            text = f"{ins['pc']:#06x}  {self.texts[ins['seq']]}" if ins else '-- bubble --'
            lines.append(f"  {n:3s} | {text}")
        for e in self.events:
            lines.append(f"  * {e}")
        return '\n'.join(lines)

    def diagram(self, last=12):
        """Classic pipeline diagram: one row per instruction, one column
        per cycle. A repeated stage means a stall, 'x' means flushed."""
        seqs = sorted(self.timeline)[-last:]
        if not seqs:
            return "(nothing executed yet)"
        first = min(min(self.timeline[q]) for q in seqs)
        last_c = max(max(self.timeline[q]) for q in seqs)
        cols = range(first, last_c + 1)
        head = f"{'instruction':26s}" + ''.join(f"{c:>4d}" for c in cols)
        out = [head, '-' * len(head)]
        for q in seqs:
            row = self.timeline[q]
            cells = ''.join(f"{row.get(c, ''):>4s}" for c in cols)
            mark = '  x' if q in self.squashed else ''
            out.append(f"{self.texts[q][:25]:26s}{cells}{mark}")
        return '\n'.join(out)
