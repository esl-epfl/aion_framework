# mac8_vhdl

An 8x8 unsigned multiply-accumulate (20-bit accumulator) in VHDL-2008: the
framework's example design. Small enough to run the whole flow in minutes;
its multiplier array is what the miner and `make ext-rank` work on. The same
MAC in SystemVerilog is [`mac8_sv`](../mac8_sv/).

```
design.toml               top, language, sources, testbench, agent, flow knobs
src/rtl/                  the RTL (vhdl): mac8_pkg.vhd, mac8.vhd
tb/test_mac8.py           cocotb testbench (RTL and every netlist)
constraints/mac8.sdc      timing constraints (25 ns)
librelane/config.json     LibreLane template (+ per-mode overlays), 160x160 µm die
pdk_extension/            hand-designed cells for this design (git-ignored)
```

```bash
make sim       DESIGN=mac8_vhdl                 # RTL testbench (GHDL)
make config    DESIGN=mac8_vhdl                 # every setting, resolved
make flow      DESIGN=mac8_vhdl                 # all nine steps
make flow      DESIGN=mac8_vhdl STEP=2..5       # some of them
make status    DESIGN=mac8_vhdl                 # is the run coherent?
make artifacts DESIGN=mac8_vhdl                 # what the runs produced
```

Everything the flow produces lands in `runs/mac8_vhdl/<tag>/`.
