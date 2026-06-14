# QuestaSim: latency_counter waveform.   Usage: vsim -do latency.do
vlib work
vmap work work
vlog -sv +acc ../../rtl/latency_counter.sv tb_latency_counter.sv
vsim -voptargs=+acc work.tb_latency_counter
set tb /tb_latency_counter
add wave -divider "Clock / Reset"
add wave $tb/clk $tb/rst_n
add wave -divider "Measurement"
add wave $tb/msg_start $tb/decision_valid
add wave -radix unsigned $tb/dut/free_cnt $tb/dut/t0 $tb/dut/delta
add wave $tb/dut/measuring
add wave -radix unsigned $tb/dut/last_latency_cycles $tb/dut/bucket
add wave -divider "AXI-Lite read"
add wave $tb/s_axil_arvalid $tb/s_axil_arready
add wave -radix hexadecimal $tb/s_axil_araddr
add wave $tb/s_axil_rvalid $tb/s_axil_rready
add wave -radix unsigned $tb/s_axil_rdata
run -all
