# mac8_sv

```
design.toml               top, language, sources, testbench, agent, flow knobs
src/rtl/                  the RTL (systemverilog)
tb/test_mac8.py           cocotb testbench (RTL and every netlist)
constraints/mac8.sdc      timing constraints
librelane/config.json     LibreLane template (+ per-mode overlays)
pdk_extension/            hand-designed cells for this design (git-ignored)
```

```bash
make sim       DESIGN=mac8_sv                 # RTL testbench
make config    DESIGN=mac8_sv                 # every setting, resolved
make flow      DESIGN=mac8_sv                 # all nine steps
make flow      DESIGN=mac8_sv STEP=2..5       # some of them
make status    DESIGN=mac8_sv                 # is the run coherent?
make artifacts DESIGN=mac8_sv                 # what the runs produced
```

Everything the flow produces lands in `runs/mac8_sv/<tag>/`.
