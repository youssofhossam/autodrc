.subckt ota_ff_1065 VDD VSS VP VN
M5  net4 net9 VDD  VDD sky130_fd_pr__pfet_01v8 l=150e-9 w=10.5e-7 nf=4
M39 net3 net3 VDD  VDD sky130_fd_pr__pfet_01v8 l=150e-9 w=10.5e-7 nf=4
M82 VN   VN   net4 VDD sky130_fd_pr__pfet_01v8 l=150e-9 w=10.5e-7 nf=4
M81 VSS  VP   net4 VDD sky130_fd_pr__pfet_01v8 l=150e-9 w=10.5e-7 nf=4
M40 net9 net3 VDD  VDD sky130_fd_pr__pfet_01v8 l=150e-9 w=10.5e-7 nf=4
M50 v1   v1   VSS  VSS sky130_fd_pr__nfet_01v8 l=150e-9 w=10.5e-7 nf=4
M36 net6 v1   VSS  VSS sky130_fd_pr__nfet_01v8 l=150e-9 w=10.5e-7 nf=4
M38 net3 VN   net6 VSS sky130_fd_pr__nfet_01v8 l=150e-9 w=10.5e-7 nf=4
M35 VDD  VP   net7 VSS sky130_fd_pr__nfet_01v8 l=150e-9 w=10.5e-7 nf=4
M34 VN   VN   net7 VSS sky130_fd_pr__nfet_01v8 l=150e-9 w=10.5e-7 nf=2
M6  net7 v1   VSS  VSS sky130_fd_pr__nfet_01v8 l=150e-9 w=10.5e-7 nf=4
M37 net9 VP   net6 VSS sky130_fd_pr__nfet_01v8 l=150e-9 w=10.5e-7 nf=2
M1  v1   v1   VDD  VDD sky130_fd_pr__pfet_01v8 l=150e-9 w=10.5e-7 nf=4
.ends
