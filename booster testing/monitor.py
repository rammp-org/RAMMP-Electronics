"""Log supply current and rail DC levels over time (read-only except scope).

Usage:  python monitor.py <minutes> [interval_s]
"""
import sys, time

import numpy as np

from scpi import Scpi, SCOPE_IP, SUPPLY_IP, read_waveform

minutes = float(sys.argv[1]) if len(sys.argv) > 1 else 10
interval = float(sys.argv[2]) if len(sys.argv) > 2 else 15

psu, sc = Scpi(SUPPLY_IP), Scpi(SCOPE_IP)
sc.write(":ACQ:TYPE NORM")
sc.write(":TIM:MAIN:OFFS 0")
sc.write(":TIM:MAIN:SCAL 0.001")
sc.write(":ACQ:MDEP 100k")
for c, (s, o) in {1: (2.0, -6.0), 2: (1.0, -2.5), 3: (2.0, -6.0)}.items():
    sc.write(f":CHAN{c}:BWL 20M")
    sc.write(f":CHAN{c}:COUP DC")
    sc.write(f":CHAN{c}:SCAL {s}")
    sc.write(f":CHAN{c}:OFFS {o}")
sc.write(":TRIG:SWE AUTO")
sc.errors()

fn = time.strftime("monitor_%Y%m%d_%H%M%S.csv")
with open(fn, "w") as f:
    f.write("t_s,V_supply,I_supply_A,P_supply_W,mode,V10,V5in,V12,V12_pp_mV\n")
    t0 = time.time()
    while time.time() - t0 < minutes * 60:
        v, i, p = psu.query(":MEAS:ALL? CH1").split(",")
        mode = psu.query(":OUTP:MODE? CH1")
        sc.write(":RUN")
        time.sleep(0.4)
        sc.write(":STOP")
        time.sleep(0.3)
        r = {c: read_waveform(sc, c)[1] for c in (1, 2, 3)}
        row = (f"{time.time()-t0:.0f},{v},{i},{p},{mode},{r[1].mean():.4f},"
               f"{r[2].mean():.4f},{r[3].mean():.4f},{np.ptp(r[3])*1e3:.0f}")
        f.write(row + "\n")
        f.flush()
        print(row, flush=True)
        time.sleep(max(0, interval - 1))
sc.close()
psu.close()
print("saved", fn)
