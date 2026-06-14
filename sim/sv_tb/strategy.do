# QuestaSim: strategy_imbalance waveform.   Usage: vsim -do strategy.do
vlib work
vmap work work
vlog -sv +acc ../../rtl/strategy_imbalance.sv tb_strategy_imbalance.sv
vsim -voptargs=+acc work.tb_strategy_imbalance
set tb /tb_strategy_imbalance
add wave -divider "Clock / Reset"
add wave $tb/clk $tb/rst_n
add wave -divider "Book state in"
add wave $tb/book_valid
add wave -radix unsigned $tb/best_bid_price $tb/best_ask_price
add wave -radix unsigned $tb/bid_level_size $tb/ask_level_size
add wave -divider "Weighted volumes (internal)"
add wave -radix unsigned $tb/dut/w_bid $tb/dut/w_ask
add wave $tb/dut/spread_ok $tb/dut/cooldown_cnt
add wave -divider "Decision out"
add wave $tb/decision_valid $tb/action
add wave -radix unsigned $tb/order_price $tb/order_size
run -all
