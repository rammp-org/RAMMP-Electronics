"""Check one INA118 output test point on CH3 for the 40 kHz tone.

CH1 = 10 V out (C7), CH2 = 5 V in, CH3 = test point (ground clip on GND).
Scope settings only; the supply is only read.

Usage:  python tpcheck.py <label>       e.g. python tpcheck.py TP2
"""
import sys, time

import numpy as np
from scipy import signal

from scpi import Scpi, SCOPE_IP, SUPPLY_IP, read_waveform
from ripple import clip_check

label = sys.argv[1] if len(sys.argv) > 1 else "TP"
p = Scpi(SUPPLY_IP)
print("supply", p.query(":MEAS:ALL? CH1"), p.query(":OUTP:MODE? CH1"))
p.close()

sc = Scpi(SCOPE_IP)
names = {1: "CH1 10V/C7", 2: "CH2 5Vin", 3: f"CH3 {label}"}
data = {}
for stage, cfg in (("coarse", {1: (1.0, -10.0), 2: (1.0, -5.0), 3: (0.5, -1.65)}),
                   ("sensitive", {1: (0.01, -10.0), 2: (0.02, -4.972), 3: (0.05, -1.65)})):
    for c, (s, o) in cfg.items():
        sc.write(f":CHAN{c}:DISP ON")
        sc.write(f":CHAN{c}:BWL 20M")
        sc.write(f":CHAN{c}:COUP DC")
        sc.write(f":CHAN{c}:SCAL {s}")
        sc.write(f":CHAN{c}:OFFS {o}")
    sc.write(":ACQ:TYPE HRES")
    sc.write(":TIM:MAIN:SCAL 0.01")
    sc.write(":ACQ:MDEP 1M")
    sc.write(":TRIG:SWE AUTO")
    time.sleep(0.5)
    sc.write(":RUN")
    time.sleep(1.0)
    sc.write(":STOP")
    time.sleep(0.5)
    print(f"-- {stage}")
    for c in (1, 2, 3):
        t, v = read_waveform(sc, c)
        fs = 1 / (t[1] - t[0])
        s = float(sc.query(f":CHAN{c}:SCAL?"))
        o = float(sc.query(f":CHAN{c}:OFFS?"))
        cl, _ = clip_check(v, -o, s)
        sos = signal.butter(4, [38e3, 43e3], btype="band", fs=fs, output="sos")
        y = signal.sosfiltfilt(sos, signal.detrend(v))
        f, P = signal.welch(signal.detrend(v), fs, nperseg=1 << 15)
        m = (f > 1e3) & (f < 2e5)
        fp = f[m][np.argmax(P[m])]
        print(f"   {names[c]:12s} dc {v.mean():8.4f} V | pp {np.ptp(v)*1e3:7.1f} mV | "
              f"40k-band {np.std(y)*1e6:8.0f} uVrms | strongest line {fp:.0f} Hz | "
              f"{s*1e3:g} mV/div clipped {cl*100:.2f}%")
        data[c] = v
sc.errors()
sc.close()

np.savez(time.strftime(f"tpcheck_{label}_%Y%m%d_%H%M%S.npz"), ch1=data[1], ch2=data[2], ch3=data[3], fs=fs)
sos = signal.butter(4, [38e3, 43e3], btype="band", fs=fs, output="sos")
a = signal.sosfiltfilt(sos, signal.detrend(data[3]))
b = signal.sosfiltfilt(sos, signal.detrend(data[1]))
f, C = signal.coherence(a, b, fs, nperseg=1 << 14)
k = np.argmax(C * ((f > 38e3) & (f < 43e3)))
print(f"coherence {label} vs 10V at {f[k]:.0f} Hz: {C[k]:.2f}")
