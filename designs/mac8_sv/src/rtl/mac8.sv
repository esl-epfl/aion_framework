// ================================================================
//  mac8 -- starting point: replace with your design.
//
//  A registered 8-bit datapath, so the flow has logic to mine out of the
//  box:  y <= a + b  when sel,  y <= (a & b) ^ y  otherwise.
//  Keep the ports the testbench drives in sync with tb/test_mac8.py.
// ================================================================


module mac8 (
    input  logic                              clk,
    input  logic                              rst_n,
    input  logic                              en,
    input  logic                              clr,
    input  logic [mac8_pkg::OPERAND_BITS-1:0] a,
    input  logic [mac8_pkg::OPERAND_BITS-1:0] b,
    output logic [    mac8_pkg::ACC_BITS-1:0] acc
);

  import mac8_pkg::*;

  logic [ACC_BITS-1:0] acc_q;
  logic [2*OPERAND_BITS-1:0] product;

  assign product = a * b;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      acc_q <= '0;
    end else begin
      if (clr) begin
        acc_q <= '0;
      end else if (en) begin
        acc_q <= acc_q + {{($bits(acc_q) - $bits(product)) {1'b0}}, product};
      end
    end
  end

  assign acc = acc_q;

endmodule
