# ================================================================
#  tt_um_aion -- timing constraints (PnR and signoff STA)
#
#  LibreLane reads this for every mode; the clock period here and
#  CLOCK_PERIOD in librelane/config.json should agree.
# ================================================================

set clk_port   clk
set clk_period 40

create_clock [get_ports $clk_port] -name core_clk -period $clk_period
set_propagated_clock [all_clocks]
set_clock_uncertainty 0.25 [get_clocks core_clk]
set_clock_transition 0.15 [get_clocks core_clk]

# Every input but the clock arrives 20% into the cycle; every output is
# needed 20% before the end of it.
set non_clock_inputs [lsearch -inline -all -not -exact [all_inputs] [get_ports $clk_port]]
set_input_delay  [expr {$clk_period * 0.2}] -clock core_clk $non_clock_inputs
set_output_delay [expr {$clk_period * 0.2}] -clock core_clk [all_outputs]

# Drive and load at the boundary.
set_driving_cell -lib_cell sg13g2_buf_1 -pin X $non_clock_inputs
set_load 0.01 [all_outputs]

set_max_fanout 10 [current_design]
set_max_transition 1.5 [current_design]
