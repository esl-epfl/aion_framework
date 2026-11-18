# mac8_sv

The 8x8 multiply-accumulate of [`mac8_vhdl`](../mac8_vhdl/), in
SystemVerilog. The package is a compilation unit of its own
(`src/include/mac8_pkg.sv`), listed first in `[sources] rtl`.

```
design.toml               top, language, sources, testbench, agent, flow knobs
src/include/mac8_pkg.sv   the package
src/rtl/                  the RTL (systemverilog)
tb/test_mac8.py           cocotb testbench (RTL and every netlist)
constraints/mac8.sdc      timing constraints (25 ns)
librelane/config.json     LibreLane template (+ per-mode overlays), 150x150 µm die
pdk_extension/            hand-designed cells for this design (git-ignored)
```

```bash
make sim       DESIGN=mac8_sv                 # RTL testbench (Icarus)
make config    DESIGN=mac8_sv                 # every setting, resolved
make flow      DESIGN=mac8_sv                 # all nine steps
make flow      DESIGN=mac8_sv STEP=2..5       # some of them
make status    DESIGN=mac8_sv                 # is the run coherent?
make artifacts DESIGN=mac8_sv                 # what the runs produced
```

Everything the flow produces lands in `runs/mac8_sv/<tag>/`.
