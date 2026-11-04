// ================================================================
//  SPDX-FileCopyrightText:    2026 Filippo Quadri
//  SPDX-License-Identifier:   Apache-2.0 WITH SHL-2.1
//  Description:               cve2_wrap -- the CVE2 (CV32E20) core, no memories
//
//  The flow's top level: cve2_top from upstream (src/cve2/, unmodified),
//  with flat ports only, so every netlist the flow writes keeps the same
//  pin names the testbench drives.
//
//  * No instruction or data memory: both OBI buses are top-level ports.  The
//    testbench (tb/test_cve2_wrap.py) is the memory.
//  * Sideband tied off inside: no interrupts, no debug request, no CV-X-IF
//    coprocessor, hart 0, test mode off.  The boot address is a parameter.
//  * The core configuration is upstream's CI-tested one (cve2_configs.yaml,
//    "small"): RV32IMC, fast multiplier, 10 x 40-bit HPM counters.
// ================================================================

module cve2_wrap #(
    parameter logic [31:0] BootAddr = 32'h0000_0000
) (
    input  logic        clk_i,
    input  logic        rst_ni,

    input  logic        fetch_enable_i,
    output logic        core_sleep_o,

    // Instruction memory (OBI)
    output logic        instr_req_o,
    input  logic        instr_gnt_i,
    input  logic        instr_rvalid_i,
    output logic [31:0] instr_addr_o,
    input  logic [31:0] instr_rdata_i,
    input  logic        instr_err_i,

    // Data memory (OBI)
    output logic        data_req_o,
    input  logic        data_gnt_i,
    input  logic        data_rvalid_i,
    output logic        data_we_o,
    output logic [ 3:0] data_be_o,
    output logic [31:0] data_addr_o,
    output logic [31:0] data_wdata_o,
    input  logic [31:0] data_rdata_i,
    input  logic        data_err_i
);

  import cve2_pkg::*;

  // CV-X-IF is disabled (XInterface = 0): its outputs go nowhere, its
  // inputs are zero.
  x_issue_req_t  x_issue_req_unused;
  x_register_t   x_register_unused;
  x_commit_t     x_commit_unused;
  crash_dump_t   crash_dump_unused;
  logic          x_issue_valid_unused, x_commit_valid_unused, x_result_ready_unused;
  logic          debug_halted_unused;

  cve2_top #(
      .MHPMCounterNum  (10),
      .MHPMCounterWidth(40),
      .RV32E           (1'b0),
      .RV32M           (RV32MFast),
      .XInterface      (1'b0)
  ) u_cve2_top (
      .clk_i,
      .rst_ni,

      .test_en_i  (1'b0),
      .ram_cfg_i  ('0),

      .hart_id_i  (32'h0),
      .boot_addr_i(BootAddr),

      .instr_req_o,
      .instr_gnt_i,
      .instr_rvalid_i,
      .instr_addr_o,
      .instr_rdata_i,
      .instr_err_i,

      .data_req_o,
      .data_gnt_i,
      .data_rvalid_i,
      .data_we_o,
      .data_be_o,
      .data_addr_o,
      .data_wdata_o,
      .data_rdata_i,
      .data_err_i,

      .x_issue_valid_o (x_issue_valid_unused),
      .x_issue_ready_i (1'b0),
      .x_issue_req_o   (x_issue_req_unused),
      .x_issue_resp_i  ('0),
      .x_register_o    (x_register_unused),
      .x_commit_valid_o(x_commit_valid_unused),
      .x_commit_o      (x_commit_unused),
      .x_result_valid_i(1'b0),
      .x_result_ready_o(x_result_ready_unused),
      .x_result_i      ('0),

      .irq_software_i(1'b0),
      .irq_timer_i   (1'b0),
      .irq_external_i(1'b0),
      .irq_fast_i    (16'h0),
      .irq_nm_i      (1'b0),

      .debug_req_i        (1'b0),
      .debug_halted_o     (debug_halted_unused),
      .dm_halt_addr_i     (32'h1A11_0800),
      .dm_exception_addr_i(32'h1A11_0808),
      .crash_dump_o       (crash_dump_unused),

      .fetch_enable_i,
      .core_sleep_o
  );

endmodule
