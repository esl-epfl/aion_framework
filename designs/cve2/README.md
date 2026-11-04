# cve2

The [CVE2 (CV32E20)](https://github.com/openhwfoundation/cve2) RISC-V core,
RV32IMC, in its upstream CI configuration. **No memories**: both OBI buses
are top-level ports, and the testbench acts as the RAM.

```
design.toml                  top, sources, testbench, flow knobs
src/cve2/                    upstream RTL, vendored unmodified (VENDOR.md: commit, file list)
src/rtl/cve2_wrap.sv         top level: cve2_top with flat ports, sideband tied off
src/rtl/cve2_clock_gate.sv   replaces upstream's sim-only gate: sg13g2_slgcp_1 in synthesis
tb/test_cve2_wrap.py         cocotb: hello world (RTL and every netlist)
constraints/cve2_wrap.sdc    timing constraints (25 ns)
librelane/config.json        LibreLane template (+ per-mode overlays)
```

The testbench assembles a small program (no RISC-V toolchain needed),
serves it from a Python RAM, and checks what the core writes to three MMIO
registers: `Hello, world!\n` on PUTCHAR, the results of a loop, `mul`,
`divu` and a RAM round trip on RESULT, and 0 on EXIT. It is a smoke test,
meant to show when a flow step breaks the core. It does not verify CVE2.

```bash
make sim  DESIGN=cve2                 # RTL testbench (Verilator)
make sim  DESIGN=cve2 STAGE=synth     # the same, on 1_synth's netlist
make flow DESIGN=cve2                 # all nine steps
```

`DIE_AREA` (800x800 µm) is an estimate made before synthesis. Resize it
once `1_synth` reports the cell area.
