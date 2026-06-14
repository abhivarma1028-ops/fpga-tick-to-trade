# QuestaSim: itch_parser waveform.   Usage: vsim -do parser.do
vlib work
vmap work work
vlog -sv +acc ../../rtl/itch_parser.sv tb_itch_parser.sv
vsim -voptargs=+acc work.tb_itch_parser
set tb /tb_itch_parser
add wave -divider "Clock / Reset"
add wave $tb/clk $tb/rst_n
add wave -divider "AXI-Stream in"
add wave $tb/s_axis_tvalid $tb/s_axis_tready $tb/s_axis_tlast
add wave -radix hexadecimal $tb/s_axis_tdata
add wave $tb/dut/byte_cnt
add wave -divider "Decoded output"
add wave $tb/m_valid $tb/m_ready
add wave -radix hexadecimal $tb/msg_type
add wave -radix unsigned $tb/order_ref $tb/new_order_ref $tb/shares $tb/price
add wave $tb/side $tb/msg_unsupported
run -all
