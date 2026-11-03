# mac8

An 8x8 unsigned multiply-accumulate (20-bit accumulator) in VHDL-2008: the
framework's example design. Small enough to run the whole flow in minutes;
its multiplier array is what the miner and `make ext-rank` work on.

```
design.toml               top, language, sources, testbench, agent, flow knobs
src/rtl/                  the RTL (vhdl)
tb/test_mac8.py           cocotb testbench (RTL and every netlist)
constraints/mac8.sdc      timing constraints
librelane/config.json     LibreLane template (+ per-mode overlays)
pdk_extension/            hand-designed cells for this design (git-ignored)
```

```bash
make sim       DESIGN=mac8                 # RTL testbench
make config    DESIGN=mac8                 # every setting, resolved
make flow      DESIGN=mac8                 # all nine steps
make flow      DESIGN=mac8 STEP=2..5       # some of them
make status    DESIGN=mac8                 # is the run coherent?
make artifacts DESIGN=mac8                 # what the runs produced
```

Everything the flow produces lands in `runs/mac8/<tag>/`.
