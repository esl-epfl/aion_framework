// ================================================================
//  SPDX-FileCopyrightText:    2026 Filippo Quadri
//  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
//  Description:               cve2_clock_gate -- replaces upstream's
//
//  Upstream's rtl/cve2_clock_gate.sv is a latch + AND "for simulation only,
//  must not be used for ASIC synthesis", so it is not vendored.  This one
//  keeps the same module and ports:
//
//    synthesis   the IHP integrated clock gate, sg13g2_slgcp_1 (SCE = test
//                enable).  yosys-slang defines SYNTHESIS by itself.
//    simulation  the same behavioural latch gate upstream uses.
// ================================================================

module cve2_clock_gate (
    input  logic clk_i,
    input  logic en_i,
    input  logic scan_cg_en_i,
    output logic clk_o
);

`ifdef SYNTHESIS
  sg13g2_slgcp_1 u_icg (
      .CLK (clk_i),
      .GATE(en_i),
      .SCE (scan_cg_en_i),
      .GCLK(clk_o)
  );
`else
  logic clk_en;

  always_latch begin
    if (clk_i == 1'b0) clk_en <= en_i | scan_cg_en_i;
  end

  assign clk_o = clk_i & clk_en;
`endif

endmodule
