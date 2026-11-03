# ================================================================
#  mac8 -- cocotb testbench
#
#  Runs unchanged on the RTL and on every netlist the flow writes, so it
#  drives and samples the top-level ports only, and samples on the falling
#  edge: a netlist with SDF delays changes its outputs after the rising one.
# ================================================================

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge

PERIOD_NS = 25
MASK = (1 << 20) - 1


async def start(dut) -> None:
    cocotb.start_soon(Clock(dut.clk, PERIOD_NS, unit="ns").start())
    dut.rst_n.value = 0
    dut.en.value = 0
    dut.clr.value = 0
    dut.a.value = 0
    dut.b.value = 0
    await ClockCycles(dut.clk, 2)
    dut.rst_n.value = 1
    await FallingEdge(dut.clk)


async def cycle(dut, en: int, clr: int, a: int, b: int) -> None:
    dut.en.value, dut.clr.value, dut.a.value, dut.b.value = en, clr, a, b
    await RisingEdge(dut.clk)
    await FallingEdge(dut.clk)


@cocotb.test()
async def reset_and_clear(dut):
    """Reset and clr both zero the accumulator; en=0 holds it."""
    await start(dut)
    assert int(dut.acc.value) == 0
    await cycle(dut, 1, 0, 200, 100)
    assert int(dut.acc.value) == 20000
    await cycle(dut, 0, 0, 7, 7)
    assert int(dut.acc.value) == 20000, "en=0 must hold"
    await cycle(dut, 1, 1, 7, 7)
    assert int(dut.acc.value) == 0, "clr wins over en"


@cocotb.test()
async def corners(dut):
    """Largest operands, and the wrap at 2^20."""
    await start(dut)
    expected = 0
    for _ in range(20):
        await cycle(dut, 1, 0, 255, 255)
        expected = (expected + 255 * 255) & MASK
        assert int(dut.acc.value) == expected


@cocotb.test()
async def random_stream(dut):
    """1000 random cycles against the model."""
    await start(dut)
    rng = random.Random(8)
    expected = 0
    for _ in range(1000):
        en, clr = int(rng.random() < 0.8), int(rng.random() < 0.03)
        a, b = rng.getrandbits(8), rng.getrandbits(8)
        await cycle(dut, en, clr, a, b)
        if clr:
            expected = 0
        elif en:
            expected = (expected + a * b) & MASK
        assert int(dut.acc.value) == expected, (
            f"acc={int(dut.acc.value)}, expected {expected}"
        )
