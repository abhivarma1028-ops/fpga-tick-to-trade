# QuestaSim: order_book_m2 waveform.   Usage: vsim -do book.do
vlib work
vmap work work
vlog -sv +acc ../../rtl/order_book_m2.sv tb_order_book_m2.sv
vsim -voptargs=+acc work.tb_order_book_m2
set tb /tb_order_book_m2
add wave -divider "Clock / Reset"
add wave $tb/clk $tb/rst_n
add wave -divider "Message in"
add wave $tb/msg_valid $tb/msg_ready
add wave -radix hexadecimal $tb/msg_type
add wave -radix unsigned $tb/order_ref $tb/shares $tb/price
add wave $tb/side
add wave -divider "FSM"
add wave $tb/dut/state $tb/dut/scan_idx
add wave -divider "Top of book"
add wave $tb/book_valid
add wave -radix unsigned $tb/best_bid_price $tb/best_bid_size
add wave -radix unsigned $tb/best_ask_price $tb/best_ask_size
add wave -divider "Depth (flattened {L3..L0})"
add wave -radix unsigned $tb/bid_level_price $tb/bid_level_size
add wave -radix unsigned $tb/ask_level_price $tb/ask_level_size
run -all
