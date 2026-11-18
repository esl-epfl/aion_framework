# posit_alu

The AION Tiny Tapeout chip's Posit<32,2>/<16,2> ALU (`tt_um_aion`: register
interface + add/mult/compare/bitwise core), copied from `aion_chip`
(src/rtl and src/tb at aion_chip commit 8c07ccc).

Top level is `tt_um_aion`, so the cocotb testbench `tb/aion_test.py` drives
it unmodified through the 8-bit register interface. `tb/posit.py` checks
results against Stillwater Universal through `tb/libuniversal_posit.so`
(built from `tb/universal_posit.cpp`).

Die: the 4x2 Tiny Tapeout tile size (854.4 x 313.74 um), but no TT DEF
template, PDN or pin constraints -- this is a flow comparison, not a tapeout.
