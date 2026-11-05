import isa
from exceptions import MemoryFault, IllegalInstruction, RiscError

STAGES = {4: ['IF', 'OF', 'EX', 'RW'],
          6: ['IF', 'ID', 'OF', 'EX', 'MA', 'RW']}

# Logic delay of each piece of work (picoseconds, ASSUMED numbers, stated
# openly). The 4-stage merges ID+OF and EX+MA, so those stages are slower.
STAGE_DELAY = {'IF': 200, 'ID': 100, 'OF': 150, 'EX': 250, 'MA': 200, 'RW': 100}
LATCH = 20                                    # pipeline register overhead


def clock_period(depth):
    """Iron law: the slowest stage sets the clock (plus the latch)."""
    d = STAGE_DELAY
    if depth == 6:
        return max(d.values()) + LATCH
    return max(d['IF'], d['ID'] + d['OF'], d['EX'] + d['MA'], d['RW']) + LATCH


BRANCHES = ('beq', 'bgt', 'b', 'call', 'ret')
MUL_OPS = ('mul', 'fmul')
DIV_OPS = ('div', 'mod', 'fdiv', 'fsqrt', 'froot')


class _Scratch:
    """Holds the V flag an instruction produces until it commits in RW."""
    def __init__(self):
        self.V = None


class BranchPredictor:
    """BTB + BHT (1-bit or 2-bit counters) + Return Address Stack.

    BTB  entry per branch PC (direct mapped, tagged): target and type
         ('cond', 'uncond', 'call', 'ret'). Without a BTB hit, IF does not
         even know the word is a branch, so it predicts "not taken".
    BHT  one counter per entry, for conditional branches:
           1-bit: remember the last outcome
           2-bit: 0 strongly not taken, 1 weakly not taken,
                  2 weakly taken,      3 strongly taken
         A 2-bit counter needs TWO wrong guesses in a row to change its
         mind, so a loop branch mispredicts once per loop, not twice.
    RAS  call pushes its return address, ret pops it: calls and returns
         are LIFO, so this predicts ret almost perfectly.
    """

    def __init__(self, kind='2bit', entries=64, ras_size=8):
        assert kind in ('1bit', '2bit')
        self.kind = kind
        self.entries = entries
        self.ras_size = ras_size
        self.btb = {}
        self.bht = [1 if kind == '2bit' else 0] * entries
        self.ras = []

    def _index(self, pc):
        return (pc >> 2) % self.entries

    def predict(self, pc):
        """(taken, target) for the word at pc. Also does the speculative
        RAS push / pop for a predicted call / ret."""
        entry = self.btb.get(self._index(pc))
        if not entry or entry[0] != pc:
            return False, None
        _, target, kind = entry
        if kind == 'cond':
            taken = self.bht[self._index(pc)] >= (2 if self.kind == '2bit' else 1)
            return taken, (target if taken else None)
        if kind == 'ret':
            if not self.ras:
                return False, None
            return True, self.ras.pop()
        if kind == 'call':
            self._push(pc + 4)
        return True, target

    def _push(self, address):
        self.ras.append(address)
        if len(self.ras) > self.ras_size:
            self.ras.pop(0)                      # oldest entry falls off

    def update(self, pc, name, taken, target):
        """Called in EX with the real outcome."""
        i = self._index(pc)
        kind = {'beq': 'cond', 'bgt': 'cond', 'b': 'uncond', 'call': 'call', 'ret': 'ret'}[name]
        if kind == 'cond':
            if self.kind == '2bit':
                self.bht[i] = min(3, self.bht[i] + 1) if taken else max(0, self.bht[i] - 1)
            else:
                self.bht[i] = 1 if taken else 0
        if taken:
            self.btb[i] = (pc, target, kind)

    def repair(self, ras_before, name, pc):
        """After a misprediction: throw away what wrong-path fetches did to
        the RAS, then apply this instruction's own push / pop."""
        self.ras = list(ras_before)
        if name == 'call':
            self._push(pc + 4)
        elif name == 'ret' and self.ras:
            self.ras.pop()


class Pipeline:
    def __init__(self, machine, stages=6, predictor='none', store_forwarding=False,
                 mul_latency=1, div_latency=1, unified_memory=False):
        if stages not in STAGES:
            raise ValueError("stages must be 4 or 6")
        assert predictor in ('none', '1bit', '2bit')
        self.m = machine
        self.depth = stages
        self.names = STAGES[stages]
        self.ex_index = self.names.index('EX')
        self.has_ma = 'MA' in self.names
        self.mem_stage = 'MA' if self.has_ma else 'EX'
        self.slot = {n: None for n in self.names}   # instruction in each stage

        self.predictor = BranchPredictor(predictor) if predictor != 'none' else None
        self.predictor_kind = predictor
        self.store_forwarding = store_forwarding and self.has_ma
        self.latency = {op: mul_latency for op in MUL_OPS}
        self.latency.update({op: div_latency for op in DIV_OPS})
        self.unified_memory = unified_memory

        self.pc = 0
        self.cycle = 0
        self.retired = 0
        self.stalls = 0             # load-use stalls
        self.ex_stalls = 0          # multi-cycle EX
        self.struct_stalls = 0      # unified memory port
        self.flushed = 0
        self.branches = 0
        self.mispredicted = 0
        self.halted = False

        self.fetching = True        # turned off once hlt reaches EX
        self.stall_next = False     # load-use hazard found
        self.hold_ex = False        # multi-cycle instruction still busy in EX
        self.flush_next = False     # misprediction / hlt found
        self.redirect = None        # new PC after a misprediction

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
               'fault': None, 'srcs': [], 'ex_srcs': [], 'dest': None, 'result': None,
               'pred': (False, None), 'ras_before': (), 'ctrl': {}}
        self.seq += 1
        if 0 <= pc < self.m.code_end:
            ins['word'] = self.m.mem.peek(pc)
        else:
            # Do not fail yet: this may be a wrong-path fetch that a branch
            # will flush. It only faults if it reaches RW.
            ins['fault'] = MemoryFault(f"instruction fetch from {pc:#x}, outside the code", pc)
        next_pc = pc + 4
        if self.predictor:
            ins['ras_before'] = tuple(self.predictor.ras)
            taken, target = self.predictor.predict(pc)
            ins['pred'] = (taken, target)
            if taken:
                next_pc = target
                self.events.append(f"IF: predict {pc:#06x} taken -> {target:#06x}")
        self.pc = next_pc
        self.texts[ins['seq']] = (isa.format_instr(isa.decode(ins['word']), pc, self.m.labels)
                                  if ins['word'] is not None else '(bad fetch)')
        return ins

    # ------------------------------------------------------------------
    # ID / OF : decode, and generate the control signals
    # ------------------------------------------------------------------
    def decode(self, ins):
        if ins['d'] is not None or ins['fault']:
            return
        d = isa.decode(ins['word'])
        ins['d'] = d
        if d['name'] is None:
            ins['fault'] = IllegalInstruction(f"opcode {d['opcode']:#07b} does not exist "
                                              f"(or reserved modifier)", ins['pc'])
            return
        name = d['name']
        ins['srcs'] = isa.source_regs(d)
        ins['dest'] = isa.dest_reg(d)
        # registers needed in EX. With store forwarding a st needs only its
        # base register in EX; its data register is read in MA.
        if name == 'st' and self.store_forwarding:
            ins['ex_srcs'] = [d['rs1']]
        else:
            ins['ex_srcs'] = list(ins['srcs'])
        # control signals (Patterson & Hennessy fig. 4.22 style): from here
        # on, MA and RW act on these bits, not on the instruction's name
        ins['ctrl'] = {
            'RegWrite': ins['dest'] is not None,
            'MemRead': name == 'ld',
            'MemWrite': name == 'st',
            'MemToReg': name == 'ld',
            'Branch': name in BRANCHES,
            'SetFlags': name in isa.COMPARE,
            'ALUOp': name,
        }

    # ------------------------------------------------------------------
    # Forwarding unit
    # ------------------------------------------------------------------
    def read_operand(self, r):
        """Newest value of register r for the instruction in EX."""
        for stage in ('MA', 'RW'):
            older = self.slot.get(stage)
            if older and older['dest'] == r and not older['fault']:
                assert not (stage == 'MA' and older['ctrl'].get('MemRead')), \
                    "load-use hazard was not stalled"
                self.events.append(f"forward {isa.reg_name(r)} from {stage}")
                return older['result']
        return self.m.regs.read(r)

    def read_flags(self):
        """Newest E, GT for a branch in EX: flags are committed in RW, so
        forward them from an older cmp still in MA (RW has already committed
        this cycle, because RW works first)."""
        older = self.slot.get('MA')
        if older and older['ctrl'].get('SetFlags') and not older['fault']:
            self.events.append("forward flags from MA")
            return older['flags']
        return self.m.flags.E, self.m.flags.GT

    # ------------------------------------------------------------------
    # EX
    # ------------------------------------------------------------------
    def execute(self, ins):
        if ins.get('executed'):                       # a multi-cycle unit is still busy
            return
        ins['executed'] = True
        ins['ex_left'] = 1
        if ins['fault']:
            return                                    # raised later, in RW
        d, pc = ins['d'], ins['pc']
        if pc in self.breakpoints:          # checked in EX: never a wrong-path fetch
            self.hit_breakpoint = pc
        name = d['name']
        alu = self.m.alu
        self.m.pc_now = pc
        taken, target = False, None
        scratch = _Scratch()
        alu.flags = scratch                           # V is committed in RW, not now
        try:
            if name in isa.THREE_ADDR:
                a = self.read_operand(d['rs1'])
                b = d['imm'] if d['I'] else self.read_operand(d['rs2'])
                ins['result'] = alu.compute(name, a, b, pc)
            elif name in isa.TWO_ADDR:
                b = d['imm'] if d['I'] else self.read_operand(d['rs2'])
                ins['result'] = alu.compute(name, 0, b, pc)
            elif name in isa.COMPARE:                  # cmp, fcmp
                a = self.read_operand(d['rs1'])
                b = d['imm'] if d['I'] else self.read_operand(d['rs2'])
                ins['flags'] = alu.compare(a, b, name)
            elif name in ('ld', 'st'):
                ins['addr'] = alu.add(self.read_operand(d['rs1']), d['imm'])
                if name == 'st' and not self.store_forwarding:
                    ins['store_value'] = self.read_operand(d['rd'])
            elif name in ('beq', 'bgt', 'b', 'call'):
                E, GT = self.read_flags()
                taken = (name in ('b', 'call') or (name == 'beq' and E) or
                         (name == 'bgt' and GT))
                target = pc + 4 * d['offset']
                if name == 'call':
                    ins['result'] = pc + 4             # goes to ra in RW
            elif name == 'ret':
                target = self.read_operand(isa.RA)
                self.m.check_return(target, pc)
                taken = True
            elif name == 'hlt':
                self.fetching = False
                self.flush_next = True
                self.events.append("hlt in EX: stop fetching, drain the pipeline")
            # nop: nothing
            if not self.has_ma:                        # 4-stage: MA lives in EX
                self.memory_access(ins)
        except RiscError as e:
            ins['fault'] = e                           # precise: raise it in RW
            self.events.append(f"{name} faults in EX; reported when it reaches RW")
            return
        finally:
            alu.flags = self.m.flags
        ins['V'] = scratch.V
        ins['ex_left'] = self.latency.get(name, 1)

        if name in BRANCHES:
            self.resolve_branch(ins, name, taken, target)

    def resolve_branch(self, ins, name, taken, target):
        pc = ins['pc']
        self.branches += 1
        predicted_taken, predicted_target = ins['pred']
        wrong = taken != predicted_taken or (taken and target != predicted_target)
        if self.predictor:
            self.predictor.update(pc, name, taken, target)
        if not wrong:
            if taken:
                self.events.append(f"{name} taken -> {target:#06x}, correctly predicted: no flush")
            return
        self.mispredicted += 1
        self.redirect = target if taken else pc + 4
        self.flush_next = True
        if self.predictor:
            self.predictor.repair(ins['ras_before'], name, pc)
            self.events.append(f"{name} mispredicted -> {self.redirect:#06x}, "
                               f"flush younger instructions")
        else:
            self.events.append(f"{name} taken -> {target:#06x}, flush younger instructions")

    # ------------------------------------------------------------------
    # MA
    # ------------------------------------------------------------------
    def memory_access(self, ins):
        ctrl = ins['ctrl']
        if ins['fault'] or not (ctrl.get('MemRead') or ctrl.get('MemWrite')):
            return
        self.m.pc_now = ins['pc']
        if ctrl['MemRead']:
            ins['result'] = self.m.mem.load(ins['addr'])
        else:
            if self.store_forwarding:                  # data register read now (RW -> MA)
                rd = ins['d']['rd']
                older = self.slot.get('RW')
                if older and older['dest'] == rd and not older['fault']:
                    self.events.append(f"forward {isa.reg_name(rd)} from RW to MA (store data)")
                ins['store_value'] = self.m.regs.read(rd)   # RW has already written it
            self.m.mem.store(ins['addr'], ins['store_value'])

    def memory_stage(self, ins):
        """MA as a stage: a memory fault is recorded, raised in RW."""
        try:
            self.memory_access(ins)
        except RiscError as e:
            ins['fault'] = e
            self.events.append("memory fault in MA; reported when it reaches RW")

    # ------------------------------------------------------------------
    # RW: commit
    # ------------------------------------------------------------------
    def write_back(self, ins):
        if ins['fault']:
            # every older instruction has committed; nothing younger has
            # touched registers, flags or memory -> a PRECISE exception
            raise ins['fault']
        ctrl, name = ins['ctrl'], ins['d']['name']
        self.m.pc_now = ins['pc']
        if ctrl['RegWrite']:
            self.m.regs.write(ins['dest'], ins['result'])
        if ctrl['SetFlags']:
            self.m.flags.E, self.m.flags.GT = ins['flags']
        if ins.get('V') is not None:
            self.m.flags.V = ins['V']
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

        if self.hold_ex:
            for i in range(len(names) - 1, ex + 1, -1):  # only stages AFTER EX move
                s[names[i]] = s[names[i - 1]]
            s[names[ex + 1]] = None                     # bubble behind the busy unit
            self.ex_stalls += 1
            self.hold_ex = False
            self.events.append(f"EX busy ({s['EX']['d']['name']}): IF..EX held")
        elif self.stall_next:
            for i in range(len(names) - 1, ex, -1):     # EX and later move on
                s[names[i]] = s[names[i - 1]]
            s['EX'] = None                              # bubble
            self.stalls += 1
            self.stall_next = False
            self.events.append("stall: bubble inserted into EX")
        else:
            for i in range(len(names) - 1, 0, -1):
                s[names[i]] = s[names[i - 1]]
            user = s[self.mem_stage]
            if (self.unified_memory and self.fetching and user and not user['fault']
                    and (user['ctrl'].get('MemRead') or user['ctrl'].get('MemWrite'))):
                s['IF'] = None                          # the one memory port is busy
                self.struct_stalls += 1
                self.events.append(f"unified memory: {self.mem_stage} uses the port, IF waits")
            else:
                s['IF'] = self.fetch() if self.fetching else None

        # 2. stage work, oldest first ----------------------------------
        if s['RW']:
            self.write_back(s['RW'])
        if self.has_ma and s['MA']:
            self.memory_stage(s['MA'])
        if s['EX']:
            self.execute(s['EX'])
        for n in names[1:ex]:                           # ID / OF
            if s[n]:
                self.decode(s[n])

        # 3. hazards for the next edge ---------------------------------
        busy = s['EX']
        if busy and busy.get('ex_left', 1) > 1:
            busy['ex_left'] -= 1
            self.hold_ex = True
        elif self.has_ma and not self.flush_next:
            producer, consumer = s['EX'], s['OF']
            if (producer and consumer and not producer['fault']
                    and producer['ctrl'].get('MemRead')
                    and producer['dest'] in consumer['ex_srcs']):
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
        period = clock_period(self.depth)
        time_ns = self.cycle * period / 1000
        st = {
            'cycles': self.cycle,
            'instructions': self.retired,
            'CPI': round(self.cycle / self.retired, 3) if self.retired else 0,
            'stalls': self.stalls,
            'flushed': self.flushed,
            'branches': self.branches,
            'mispredicted': self.mispredicted,
            'clock_ps': period,
            'time_ns': round(time_ns, 1),
            'MIPS': round(self.retired / time_ns * 1000, 1) if time_ns else 0,
        }
        if self.branches:
            st['prediction_accuracy'] = f"{100 * (1 - self.mispredicted / self.branches):.1f}%"
        if self.ex_stalls:
            st['ex_stalls'] = self.ex_stalls
        if self.struct_stalls:
            st['struct_stalls'] = self.struct_stalls
        return st

    def show(self):
        extras = [f"predictor={self.predictor_kind}"] if self.predictor else []
        if self.store_forwarding:
            extras.append("store-forwarding")
        lines = [f"cycle {self.cycle}   ({self.depth}-stage{', ' + ', '.join(extras) if extras else ''})"
                 f"   next fetch pc={self.pc:#06x}"]
        for n in self.names:
            ins = self.slot[n]
            text = f"{ins['pc']:#06x}  {self.texts[ins['seq']]}" if ins else '-- bubble --'
            if ins and ins['ctrl']:
                c = ins['ctrl']
                bits = ' '.join(k for k in ('RegWrite', 'MemRead', 'MemWrite', 'Branch',
                                            'SetFlags') if c[k])
                text = f"{text:34s} [{bits}]" if bits else text
            if ins and ins['fault']:
                text += "   (faulted - reported in RW)"
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
