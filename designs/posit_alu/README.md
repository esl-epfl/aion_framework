# posit_alu

The AION Tiny Tapeout chip's Posit<32,2>/<16,2> ALU (`tt_um_aion`: register
interface + add/mult/compare/bitwise core), copied from `aion_chip`
(src/rtl and src/tb at aion_chip commit 8c07ccc).

```
design.toml                  top, sources, testbench, flow knobs
src/rtl/                     the VHDL, in compile order in design.toml
tb/aion_test.py              cocotb: ops through the 8-bit register interface
tb/posit.py                  reference model (Stillwater Universal, via ctypes)
tb/universal_posit.cpp       its C++ shim, built into tb/libuniversal_posit.so
constraints/tt_um_aion.sdc   timing constraints (40 ns)
librelane/config.json        LibreLane template (+ per-mode overlays)
pdk_extension/               hand-designed cells for this design (git-ignored)
```

Top level is `tt_um_aion`, so the cocotb testbench `tb/aion_test.py` drives
it through the 8-bit register interface. `tb/posit.py` checks results against
Stillwater Universal through `tb/libuniversal_posit.so`. The testbench differs
from aion_chip's in one place: inputs change `INPUT_DELAY_NS` (8 ns) after the
rising edge, as the SDC's input delay promises, instead of at the edge. At the
edge is a hold race once the netlist has a clock tree, and the post-PnR SDF
simulation would capture the new value a cycle early.

```bash
make sim  DESIGN=posit_alu                 # RTL testbench (GHDL)
make sim  DESIGN=posit_alu STAGE=synth     # the same, on 1_synth's netlist
make flow DESIGN=posit_alu                 # all nine steps
```

Die: the 4x2 Tiny Tapeout tile size (854.4 x 313.74 um), but no TT DEF
template, PDN or pin constraints -- this is a flow comparison, not a tapeout.
