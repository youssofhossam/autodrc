.subckt five_transistors_ota_185 GND VDD VOUT VINN VINP VBIAS
M0 source VBIAS GND GND sky130_fd_pr__nfet_01v8 l=150e-9 w=10.5e-7 nf=16
M1 VOUT VINN source GND sky130_fd_pr__nfet_01v8 l=150e-9 w=10.5e-7 nf=12
M2 net8 VINP source GND sky130_fd_pr__nfet_01v8 l=150e-9 w=10.5e-7 nf=12
M3 VOUT net8 VDD VDD sky130_fd_pr__pfet_01v8 l=150e-9 w=10.5e-7 nf=16
M4 net8 net8 VDD VDD sky130_fd_pr__pfet_01v8 l=150e-9 w=10.5e-7 nf=16
.ends five_transistors_ota_185
