# ================================================================
#  sha256 -- cocotb testbench
#
#  A port of upstream's src/tb/tb_sha256.v (secworks/sha256): the same
#  test cases, through the same 32-bit register interface.
#
#    name_version       NAME0/NAME1/VERSION read "sha2-256" "1.80"
#    sha224_*, sha256_* the NIST single-block ("abc") and double-block
#                       ("abcdbcde...") examples, both digests checked
#    issue_9_blocks     upstream's nine-block regression
#
#  Not in upstream: random_messages, a few padded messages of random length
#  in both modes, checked against hashlib.
#
#  Runs unchanged on the RTL and on every netlist the flow writes, so it
#  drives and samples top-level ports only, and never on a clock edge:
#
#    INPUT_PS after the    drive cs/we/address/write_data: one bus access
#    rising edge           per cycle, captured at the next rising edge
#    SAMPLE_PS before the  sample read_data (combinational from address and
#    rising edge           the registers) for the access driven this cycle
#
#  That is the SDC's budget (inputs 20% into the cycle, outputs 20% before
#  its end): a netlist with SDF delays has settled if it meets timing.
# ================================================================

import hashlib
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge, Timer

PERIOD_NS = 20
INPUT_PS = 4000                        # 20% into the cycle
SAMPLE_PS = 2000                       # 2 ns before the next edge
MAX_POLLS = 200                        # a block takes 66 cycles

# ---- the address map (sha256.v) --------------------------------------------
ADDR_NAME0, ADDR_NAME1, ADDR_VERSION = 0x00, 0x01, 0x02
ADDR_CTRL, ADDR_STATUS = 0x08, 0x09
CTRL_INIT, CTRL_NEXT, CTRL_MODE = 0x1, 0x2, 0x4
STATUS_READY, STATUS_VALID = 0x1, 0x2
ADDR_BLOCK0 = 0x10                     # .. 0x1f, most significant word first
ADDR_DIGEST0 = 0x20                    # .. 0x27, most significant word first

SHA224, SHA256 = 0, 1


class Sha256Bus:
    """One register access per clock cycle, as upstream's tasks do."""

    def __init__(self, dut):
        self.dut = dut
        self.clk = dut.clk

    def _drive(self, cs: int, we: int, address: int = 0, data: int = 0) -> None:
        self.dut.cs.value = cs
        self.dut.we.value = we
        self.dut.address.value = address
        self.dut.write_data.value = data

    async def _cycle(self) -> None:
        await RisingEdge(self.clk)
        await Timer(INPUT_PS, unit="ps")

    async def reset(self) -> None:
        self._drive(0, 0)
        self.dut.reset_n.value = 0
        await ClockCycles(self.clk, 2)
        await Timer(INPUT_PS, unit="ps")
        self.dut.reset_n.value = 1

    async def idle(self, cycles: int = 1) -> None:
        for _ in range(cycles):
            await self._cycle()
            self._drive(0, 0)

    async def write(self, address: int, word: int) -> None:
        await self._cycle()
        self._drive(1, 1, address, word)

    async def read(self, address: int) -> int:
        await self._cycle()
        self._drive(1, 0, address)
        await Timer(PERIOD_NS * 1000 - INPUT_PS - SAMPLE_PS, unit="ps")
        value = self.dut.read_data.value
        assert value.is_resolvable, f"read_data[{address:#04x}] = {value}"
        assert int(self.dut.error.value) == 0, f"error set reading {address:#04x}"
        return int(value)

    async def write_block(self, block: int) -> None:
        for k in range(16):
            await self.write(ADDR_BLOCK0 + k, (block >> (32 * (15 - k))) & 0xFFFF_FFFF)

    async def read_digest(self) -> int:
        digest = 0
        for k in range(8):
            digest = (digest << 32) | await self.read(ADDR_DIGEST0 + k)
        return digest

    async def wait_ready(self) -> None:
        """Poll STATUS until ready or valid, as upstream's wait_ready."""
        for _ in range(MAX_POLLS):
            if await self.read(ADDR_STATUS) & (STATUS_READY | STATUS_VALID):
                return
        raise AssertionError(f"core not ready after {MAX_POLLS} polls")

    async def hash_block(self, block: int, mode: int, first: bool) -> int:
        """One block: write it, start it (init or next), wait, read the digest.

        STATUS is registered twice over (core FSM, then sha256.v's ready_reg):
        the busy flag shows up three edges after the CTRL write is captured,
        so two idle cycles before the first poll, or it sees the stale ready.
        """
        await self.write_block(block)
        ctrl = (CTRL_INIT if first else CTRL_NEXT) | (CTRL_MODE if mode == SHA256 else 0)
        await self.write(ADDR_CTRL, ctrl)
        await self.idle(2)
        await self.wait_ready()
        digest = await self.read_digest()
        if mode == SHA224:                 # the last word is not part of it
            digest &= ~0xFFFF_FFFF
        return digest


async def start(dut) -> Sha256Bus:
    cocotb.start_soon(Clock(dut.clk, PERIOD_NS, unit="ns").start())
    bus = Sha256Bus(dut)
    await bus.reset()
    return bus


def _check(dut, name: str, got: int, expected: int) -> None:
    assert got == expected, f"{name}:\n  expected {expected:064x}\n  got      {got:064x}"
    dut._log.info(f"{name}: {got:064x}")


# ---- upstream's test vectors (NIST SHA224.pdf / SHA256.pdf) ----------------
ABC = 0x61626380000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000018
TWO_0 = 0x6162636462636465636465666465666765666768666768696768696A68696A6B696A6B6C6A6B6C6D6B6C6D6E6C6D6E6F6D6E6F706E6F70718000000000000000
TWO_1 = 0x000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000001C0

EXPECTED = {
    (SHA224, "abc"): 0x23097D223405D8228642A477BDA255B32AADBCE4BDA0B3F7E36C9DA700000000,
    (SHA224, "two_0"): 0x8250e65dbcf62f8466659c3333e5e91a10c8b7b0953927691f1419c200000000,
    (SHA224, "two_1"): 0x75388b16512776cc5dba5da1fd890150b0c6455cb4f58b195252252500000000,
    (SHA256, "abc"): 0xBA7816BF8F01CFEA414140DE5DAE2223B00361A396177A9CB410FF61F20015AD,
    (SHA256, "two_0"): 0x85E655D6417A17953363376A624CDE5C76E09589CAC5F811CC4B32C1F20E533A,
    (SHA256, "two_1"): 0x248D6A61D20638B8E5C026930C3E6039A33CE45964FF2167F6ECEDD419DB06C1,
}

ISSUE_BLOCKS = [
    0x6b900001_496e2074_68652061_72656120_6f662049_6f542028_496e7465_726e6574_206f6620_5468696e_6773292c_206d6f72_6520616e_64206d6f_7265626f_6f6d2c20,
    0x69742068_61732062_65656e20_6120756e_69766572_73616c20_636f6e73_656e7375_73207468_61742064_61746120_69732074_69732061_206e6577_20746563_686e6f6c,
    0x6f677920_74686174_20696e74_65677261_74657320_64656365_6e747261_6c697a61_74696f6e_2c496e20_74686520_61726561_206f6620_496f5420_28496e74_65726e65,
    0x74206f66_20546869_6e677329_2c206d6f_72652061_6e64206d_6f726562_6f6f6d2c_20697420_68617320_6265656e_20612075_6e697665_7273616c_20636f6e_73656e73,
    0x75732074_68617420_64617461_20697320_74697320_61206e65_77207465_63686e6f_6c6f6779_20746861_7420696e_74656772_61746573_20646563_656e7472_616c697a,
    0x6174696f_6e2c496e_20746865_20617265_61206f66_20496f54_2028496e_7465726e_6574206f_66205468_696e6773_292c206d_6f726520_616e6420_6d6f7265_626f6f6d,
    0x2c206974_20686173_20626565_6e206120_756e6976_65727361_6c20636f_6e73656e_73757320_74686174_20646174_61206973_20746973_2061206e_65772074_6563686e,
    0x6f6c6f67_79207468_61742069_6e746567_72617465_73206465_63656e74_72616c69_7a617469_6f6e2c49_6e207468_65206172_6561206f_6620496f_54202849_6e746572,
    0x6e657420_6f662054_68696e67_73292c20_6d6f7265_20616e64_206d6f72_65800000_00000000_00000000_00000000_00000000_00000000_00000000_00000000_000010e8,
]
ISSUE_DIGEST = 0x7758a30bbdfc9cd92b284b05e9be9ca3d269d3d149e7e82ab4a9ed5e81fbcf9d


# ---- the tests -------------------------------------------------------------
@cocotb.test()
async def name_version(dut):
    """NAME0, NAME1 and VERSION (upstream's check_name_version)."""
    bus = await start(dut)
    name = b"".join([(await bus.read(a)).to_bytes(4, "big")
                     for a in (ADDR_NAME0, ADDR_NAME1)])
    version = (await bus.read(ADDR_VERSION)).to_bytes(4, "big")
    dut._log.info(f"DUT name: {name.decode(errors='replace')}, "
                  f"version: {version.decode(errors='replace')}")
    assert name == b"sha2-256", f"name {name!r}"
    assert version == b"1.80", f"version {version!r}"


async def _single_block(dut, mode: int) -> None:
    bus = await start(dut)
    _check(dut, "abc", await bus.hash_block(ABC, mode, first=True), EXPECTED[mode, "abc"])


async def _double_block(dut, mode: int) -> None:
    bus = await start(dut)
    _check(dut, "first block", await bus.hash_block(TWO_0, mode, first=True),
           EXPECTED[mode, "two_0"])
    _check(dut, "final block", await bus.hash_block(TWO_1, mode, first=False),
           EXPECTED[mode, "two_1"])


@cocotb.test()
async def sha224_single_block(dut):
    """SHA-224 of "abc"."""
    await _single_block(dut, SHA224)


@cocotb.test()
async def sha224_double_block(dut):
    """SHA-224 of the NIST two-block message, both digests."""
    await _double_block(dut, SHA224)


@cocotb.test()
async def sha256_single_block(dut):
    """SHA-256 of "abc"."""
    await _single_block(dut, SHA256)


@cocotb.test()
async def sha256_double_block(dut):
    """SHA-256 of the NIST two-block message, both digests."""
    await _double_block(dut, SHA256)


@cocotb.test()
async def issue_9_blocks(dut):
    """Upstream's nine-block regression (issue_test), SHA-256."""
    bus = await start(dut)
    for k, block in enumerate(ISSUE_BLOCKS):
        digest = await bus.hash_block(block, SHA256, first=(k == 0))
    _check(dut, "nine blocks", digest, ISSUE_DIGEST)


# ---- not in upstream: against hashlib --------------------------------------
def pad(message: bytes) -> list:
    """SHA-2 padding: the message as 512-bit blocks, most significant first."""
    data = message + b"\x80" + b"\0" * (-(len(message) + 9) % 64) \
        + (8 * len(message)).to_bytes(8, "big")
    return [int.from_bytes(data[k:k + 64], "big") for k in range(0, len(data), 64)]


@cocotb.test()
async def random_messages(dut):
    """Random messages of 0..150 bytes (1..3 blocks), both modes, vs hashlib."""
    bus = await start(dut)
    rng = random.Random(1)
    lengths = [0, 55, 56, 64] + [rng.randrange(151) for _ in range(4)]
    for k, length in enumerate(lengths):
        mode = SHA256 if k % 2 == 0 else SHA224
        message = rng.randbytes(length)
        for j, block in enumerate(pad(message)):
            digest = await bus.hash_block(block, mode, first=(j == 0))
        if mode == SHA256:
            expected = int.from_bytes(hashlib.sha256(message).digest(), "big")
        else:
            expected = int.from_bytes(hashlib.sha224(message).digest(), "big") << 32
        _check(dut, f"sha{'256' if mode == SHA256 else '224'}({length} bytes)",
               digest, expected)
