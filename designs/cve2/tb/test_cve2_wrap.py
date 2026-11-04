# ================================================================
#  cve2_wrap -- cocotb testbench: hello world on the CVE2 core
#
#  The core has no memories (src/rtl/cve2_wrap.sv): this testbench is the
#  RAM on both OBI buses, and a few memory-mapped registers the program
#  writes to.  The program is assembled here, so no RISC-V toolchain is
#  needed.  The point is not to verify CVE2 (upstream does that), only to
#  notice when a flow step breaks it.
#
#  Runs unchanged on the RTL and on every netlist the flow writes, so it
#  drives and samples top-level ports only, and never on a clock edge:
#
#    falling edge          drive rvalid/rdata for what was granted at the
#                          last rising edge
#    SAMPLE_PS before the  sample the requests: gnt is tied high, so each
#    rising edge           one is granted at that edge
#
#  A netlist with SDF delays has settled by then if it meets timing.
# ================================================================

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, Event, FallingEdge, Timer, with_timeout

PERIOD_NS = 25
SAMPLE_PS = 2000                       # sample requests 2 ns before the edge
MAX_CYCLES = 3000                      # the program needs a few hundred

# ---- memory map ----------------------------------------------------------
BOOT = 0x0000_0000                     # cve2_wrap BootAddr: the first fetch
MSG = 0x0000_0400                      # the string
SCRATCH = 0x0000_0600                  # a word the program stores and reloads
MMIO = 0x1000_0000
PUTCHAR, EXIT, RESULT = MMIO + 0x0, MMIO + 0x4, MMIO + 0x8

MESSAGE = "Hello, world!\n"
EXPECTED_RESULTS = [55, 55 * 55, 55 * 55 // 5, 55 * 55]


# ---- a few RV32IM instructions -------------------------------------------
REG = {name: i for i, name in enumerate(
    "zero ra sp gp tp t0 t1 t2 s0 s1 a0 a1 a2 a3 a4 a5 "
    "a6 a7 s2 s3 s4 s5 s6 s7 s8 s9 s10 s11 t3 t4 t5 t6".split())}


def _r(f7, f3, rd, rs1, rs2):
    return (f7 << 25) | (REG[rs2] << 20) | (REG[rs1] << 15) | (f3 << 12) | (REG[rd] << 7) | 0x33


def _i(op, f3, rd, rs1, imm):
    assert -2048 <= imm < 2048, imm
    return ((imm & 0xFFF) << 20) | (REG[rs1] << 15) | (f3 << 12) | (REG[rd] << 7) | op


def _s(f3, rs2, rs1, imm):
    assert -2048 <= imm < 2048, imm
    imm &= 0xFFF
    return ((imm >> 5) << 25) | (REG[rs2] << 20) | (REG[rs1] << 15) | (f3 << 12) \
        | ((imm & 0x1F) << 7) | 0x23


def _b(f3, rs1, rs2, off):
    assert -4096 <= off < 4096 and off % 2 == 0, off
    off &= 0x1FFF
    return (((off >> 12) & 1) << 31) | (((off >> 5) & 0x3F) << 25) | (REG[rs2] << 20) \
        | (REG[rs1] << 15) | (f3 << 12) | (((off >> 1) & 0xF) << 8) \
        | (((off >> 11) & 1) << 7) | 0x63


def _j(rd, off):
    assert -(1 << 20) <= off < (1 << 20) and off % 2 == 0, off
    off &= 0x1FFFFF
    return (((off >> 20) & 1) << 31) | (((off >> 1) & 0x3FF) << 21) \
        | (((off >> 11) & 1) << 20) | (((off >> 12) & 0xFF) << 12) | (REG[rd] << 7) | 0x6F


class Asm:
    """Two-pass: labels may be used before they are defined."""

    def __init__(self, base: int):
        self.base, self.items, self.labels = base, [], {}

    def label(self, name):
        self.labels[name] = self.base + 4 * len(self.items)

    def _emit(self, fn):
        self.items.append(fn)

    def _rel(self, name, pc):
        return self.labels[name] - pc

    def lui(self, rd, imm20):   self._emit(lambda pc: (imm20 << 12) | (REG[rd] << 7) | 0x37)
    def addi(self, rd, rs, imm): self._emit(lambda pc: _i(0x13, 0, rd, rs, imm))
    def lbu(self, rd, imm, rs):  self._emit(lambda pc: _i(0x03, 4, rd, rs, imm))
    def lw(self, rd, imm, rs):   self._emit(lambda pc: _i(0x03, 2, rd, rs, imm))
    def sb(self, rs2, imm, rs1): self._emit(lambda pc: _s(0, rs2, rs1, imm))
    def sw(self, rs2, imm, rs1): self._emit(lambda pc: _s(2, rs2, rs1, imm))
    def add(self, rd, a, b):     self._emit(lambda pc: _r(0, 0, rd, a, b))
    def mul(self, rd, a, b):     self._emit(lambda pc: _r(1, 0, rd, a, b))
    def divu(self, rd, a, b):    self._emit(lambda pc: _r(1, 5, rd, a, b))
    def beq(self, a, b, l):      self._emit(lambda pc: _b(0, a, b, self._rel(l, pc)))
    def bne(self, a, b, l):      self._emit(lambda pc: _b(1, a, b, self._rel(l, pc)))
    def j(self, l):              self._emit(lambda pc: _j("zero", self._rel(l, pc)))

    def words(self) -> dict:
        return {self.base + 4 * k: fn(self.base + 4 * k) & 0xFFFF_FFFF
                for k, fn in enumerate(self.items)}


def program() -> dict:
    """Word address -> word: the code at BOOT, the string at MSG."""
    a = Asm(BOOT)
    a.lui("t0", MMIO >> 12)
    a.addi("a0", "zero", MSG)
    a.label("print")                      # puts(MSG)
    a.lbu("t1", 0, "a0")
    a.beq("t1", "zero", "printed")
    a.sb("t1", PUTCHAR - MMIO, "t0")
    a.addi("a0", "a0", 1)
    a.j("print")
    a.label("printed")
    a.addi("t2", "zero", 10)              # t3 = 10 + 9 + ... + 1
    a.addi("t3", "zero", 0)
    a.label("sum")
    a.add("t3", "t3", "t2")
    a.addi("t2", "t2", -1)
    a.bne("t2", "zero", "sum")
    a.sw("t3", RESULT - MMIO, "t0")       # 55
    a.mul("t4", "t3", "t3")
    a.sw("t4", RESULT - MMIO, "t0")       # 3025
    a.addi("t5", "zero", 5)
    a.divu("t6", "t4", "t5")
    a.sw("t6", RESULT - MMIO, "t0")       # 605
    a.sw("t4", SCRATCH, "zero")           # through the RAM and back
    a.lw("a1", SCRATCH, "zero")
    a.sw("a1", RESULT - MMIO, "t0")       # 3025
    a.sw("zero", EXIT - MMIO, "t0")       # exit(0)
    a.label("halt")
    a.j("halt")

    image = a.words()
    assert max(image) < MSG, "the code ran into the string"
    data = MESSAGE.encode() + b"\0"
    data += b"\0" * (-len(data) % 4)
    for k in range(0, len(data), 4):
        image[MSG + k] = int.from_bytes(data[k:k + 4], "little")
    return image


# ---- the memory ------------------------------------------------------------
def _bit(dut, name: str) -> int:
    value = getattr(dut, name).value
    assert value.is_resolvable, f"{name} is {value} after reset"
    return int(value)


class System:
    """RAM on both buses, plus PUTCHAR / EXIT / RESULT."""

    def __init__(self, dut, image: dict):
        self.dut, self.mem = dut, dict(image)
        self.uart, self.results, self.exit_code = "", [], None
        self.done = Event()
        self.fetches = self.loads = self.stores = 0

    def read(self, addr: int) -> int:
        return self.mem.get(addr & ~3, 0)

    def write(self, addr: int, be: int, wdata: int) -> None:
        addr &= ~3
        lanes = [k for k in range(4) if be >> k & 1]
        if addr == PUTCHAR:
            self.uart += chr((wdata >> (8 * lanes[0])) & 0xFF)
            self.dut._log.info("putchar %r", self.uart[-1])
        elif addr == EXIT:
            self.exit_code = wdata
            self.done.set()
        elif addr == RESULT:
            self.results.append(wdata)
            self.dut._log.info("result %d", wdata)
        else:
            word = self.read(addr)
            for k in lanes:
                mask = 0xFF << (8 * k)
                word = (word & ~mask) | (wdata & mask)
            self.mem[addr] = word

    async def run(self) -> None:
        dut = self.dut
        instr, data = None, None             # granted at the last rising edge
        while True:
            await FallingEdge(dut.clk_i)
            dut.instr_rvalid_i.value = int(instr is not None)
            dut.instr_rdata_i.value = self.read(instr) if instr is not None else 0
            if data is not None and data[0] == "r":
                dut.data_rvalid_i.value = 1
                dut.data_rdata_i.value = self.read(data[1])
            else:
                dut.data_rvalid_i.value = int(data is not None)   # write ack
                dut.data_rdata_i.value = 0

            await Timer(PERIOD_NS * 500 - SAMPLE_PS, unit="ps")
            instr, data = None, None
            if not dut.rst_ni.value:
                continue
            if _bit(dut, "instr_req_o"):
                instr = _bit(dut, "instr_addr_o")
                self.fetches += 1
            if _bit(dut, "data_req_o"):
                addr = _bit(dut, "data_addr_o")
                if _bit(dut, "data_we_o"):
                    self.write(addr, _bit(dut, "data_be_o"), _bit(dut, "data_wdata_o"))
                    self.stores += 1
                    data = ("w", addr)
                else:
                    self.loads += 1
                    data = ("r", addr)


async def start(dut) -> System:
    dut.rst_ni.value = 0
    dut.fetch_enable_i.value = 1
    for bus in ("instr", "data"):
        getattr(dut, f"{bus}_gnt_i").value = 1          # always ready
        getattr(dut, f"{bus}_rvalid_i").value = 0
        getattr(dut, f"{bus}_rdata_i").value = 0
        getattr(dut, f"{bus}_err_i").value = 0
    cocotb.start_soon(Clock(dut.clk_i, PERIOD_NS, unit="ns").start())
    system = System(dut, program())
    cocotb.start_soon(system.run())
    await ClockCycles(dut.clk_i, 5)
    await FallingEdge(dut.clk_i)
    dut.rst_ni.value = 1
    return system


@cocotb.test()
async def hello_world(dut):
    """Boot, print a string over MMIO, a loop, mul, divu, a RAM round trip, exit(0)."""
    system = await start(dut)
    await with_timeout(system.done.wait(), MAX_CYCLES * PERIOD_NS, "ns")
    dut._log.info("exit %d: %d fetches, %d loads, %d stores",
                  system.exit_code, system.fetches, system.loads, system.stores)
    assert system.uart == MESSAGE, f"printed {system.uart!r}, expected {MESSAGE!r}"
    assert system.results == EXPECTED_RESULTS, \
        f"results {system.results}, expected {EXPECTED_RESULTS}"
    assert system.exit_code == 0, f"exit code {system.exit_code}"
