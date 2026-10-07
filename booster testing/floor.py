"""Probe noise floor: one probe shorted tip-to-barrel near the board.

Captures the shorted channel at the same scales as the ripple run so the
numbers are directly comparable, then compares band-by-band against the
latest ripple_<ref>_* data. The other channels stay connected to their rails
in the LF capture so common pickup (e.g. the 59 kHz line) shows up in
coherence. Scope settings only; the supply is only read.

Usage:  python floor.py <shorted_ch> <ref_tag>     e.g. python floor.py 1 L0
"""
import glob, json, sys, time

import numpy as np
from scipy import signal

from scpi import Scpi, SCOPE_IP, SUPPLY_IP, read_waveform
from ripple import NAMES, acquire, clip_check

HF_BANDS = [(0, 1e5), (1e5, 1e6), (1e6, 5e6), (5e6, 20e6), (20e6, 50e6), (50e6, 90e6),
            (90e6, 160e6), (160e6, 500e6), (500e6, 1e9)]


def bands(v, fs):
    v = v - v.mean()
    V = np.fft.rfft(v)
    f = np.fft.rfftfreq(len(v), 1 / fs)
    out = {}
    for lo, hi in HF_BANDS:
        W = V.copy()
        W[(f < lo) | (f >= hi)] = 0
        x = np.fft.irfft(W, len(v))
        out[f"{lo/1e6:g}-{hi/1e6:g}MHz"] = (float(np.sqrt(np.mean(x ** 2))) * 1e3, float(np.ptp(x)) * 1e3)
    return out


def line(v, fs, f0, nper=1 << 16):
    f, P = signal.welch(signal.detrend(v), fs, nperseg=nper)
    k = np.argmin(np.abs(f - f0))
    return float(np.sqrt(np.sum(P[k - 3:k + 4]) * (f[1] - f[0]))) * 1e6


def main():
    ch = int(sys.argv[1])
    ref = sys.argv[2] if len(sys.argv) > 2 else "L0"
    name = NAMES[ch]
    stamp = time.strftime("%Y%m%d_%H%M%S")
    ref_json = json.load(open(sorted(glob.glob(f"ripple_{ref}_2*.json"))[-1]))
    hf_scale = ref_json["HF"][name]["scale_V_div"]
    lf_scales = {NAMES_INV[k]: v for k, v in ref_json["LF"]["scales_V_div"].items()}
    dc = {NAMES_INV[k]: v for k, v in ref_json["LF"]["dc_V"].items()}
    dc[ch] = 0.0
    dc[3] = 0.0  # CH3 AC coupled in LF
    f0 = 59357.0

    scope = Scpi(SCOPE_IP)
    res = {"shorted_ch": ch, "ref": ref, "time": stamp}

    # HF, shorted channel alone, same scale as reference
    scope.write(":ACQ:TYPE NORM")
    scope.write(":TIM:MAIN:OFFS 0")
    scope.write(":TIM:MAIN:SCAL 25e-6")
    scope.write(":ACQ:MDEP 1M")
    for c in (1, 2, 3, 4):
        scope.write(f":CHAN{c}:DISP {'ON' if c == ch else 'OFF'}")
    scope.write(f":CHAN{ch}:BWL OFF")
    scope.write(f":CHAN{ch}:COUP DC")
    scope.write(f":CHAN{ch}:SCAL {hf_scale}")
    scope.write(f":CHAN{ch}:OFFS 0")
    time.sleep(0.5)
    acquire(scope, ch, 0.0)
    t, v = read_waveform(scope, ch)
    fs = 1 / (t[1] - t[0])
    clipped, span = clip_check(v, 0.0, hf_scale)
    print(f"HF floor CH{ch} {hf_scale*1e3:g} mV/div: span {span:.2f} div, clipped {clipped*100:.3f} %")
    np.savez(f"floor_CH{ch}_HF_{stamp}.npz", t=t, v=v)
    ref_hf = np.load(sorted(glob.glob(f"ripple_{ref}_HF_{name}_*.npz"))[-1])
    fl, sg = bands(v, fs), bands(ref_hf["v"], 1 / (ref_hf["t"][1] - ref_hf["t"][0]))
    res["HF"] = {}
    print(f"\n{'band':>14} {'signal mVrms':>13} {'floor mVrms':>12} {'ratio':>6}  verdict")
    for k in fl:
        r = sg[k][0] / fl[k][0] if fl[k][0] else float("inf")
        verdict = "REAL" if r > 2.5 else ("MIXED" if r > 1.5 else "ARTIFACT")
        res["HF"][k] = {"signal_mVrms": sg[k][0], "signal_mVpp": sg[k][1],
                        "floor_mVrms": fl[k][0], "floor_mVpp": fl[k][1], "ratio": r, "verdict": verdict}
        print(f"{k:>14} {sg[k][0]:13.3f} {fl[k][0]:12.3f} {r:6.2f}  {verdict}")

    # LF, all three channels, same scales as reference
    for c in (1, 2, 3):
        scope.write(f":CHAN{c}:DISP ON")
        scope.write(f":CHAN{c}:BWL 20M")
        scope.write(f":CHAN{c}:COUP {'AC' if c == 3 else 'DC'}")
        scope.write(f":CHAN{c}:SCAL {lf_scales[c]}")
        scope.write(f":CHAN{c}:OFFS {-dc[c]:.5f}")
    scope.write(":ACQ:TYPE HRES")
    scope.write(":TIM:MAIN:SCAL 0.1")
    scope.write(":ACQ:MDEP 1M")
    print("\nLF: CH3 AC coupled; settling 30 s", flush=True)
    time.sleep(30)
    acquire(scope, 1, 0.0)
    data = {}
    for c in (1, 2, 3):
        tt, vv = read_waveform(scope, c)
        sc = float(scope.query(f":CHAN{c}:SCAL?"))
        offs = float(scope.query(f":CHAN{c}:OFFS?"))
        clipped, span = clip_check(vv, -offs, sc)
        print(f"  CH{c} {sc*1e3:g} mV/div: span {span:.2f} div, clipped {clipped*100:.3f} %")
        data[c] = vv
    scope.write(":CHAN3:COUP DC")
    res["scope_errors"] = scope.errors()
    scope.close()
    fs = 1 / (tt[1] - tt[0])
    np.savez(f"floor_CH{ch}_LF_{stamp}.npz", t=tt, **{NAMES[c]: data[c] for c in data})

    ref_lf = np.load(sorted(glob.glob(f"ripple_{ref}_LF_*.npz"))[-1])
    x = signal.detrend(data[ch])
    f, P = signal.welch(x, fs, nperseg=1 << 18)
    df = f[1] - f[0]
    fr, Pr = signal.welch(signal.detrend(ref_lf[name]), fs, nperseg=1 << 18)
    res["LF"] = {}
    print(f"\n{'bandwidth':>14} {'signal uVrms':>13} {'floor uVrms':>12} {'ratio':>6}")
    for hi in (100, 1e3, 1e4, 5e4, 5e5):
        m = (f > 0) & (f <= hi)
        s_, fl_ = np.sqrt(np.sum(Pr[m]) * df) * 1e6, np.sqrt(np.sum(P[m]) * df) * 1e6
        res["LF"][f"DC-{hi:g}Hz"] = {"signal_uVrms": s_, "floor_uVrms": fl_, "ratio": s_ / fl_}
        print(f"{'DC-'+format(hi,'g')+'Hz':>14} {s_:13.1f} {fl_:12.1f} {s_/fl_:6.2f}")
    res["line_59k"] = {"signal_uVrms": line(ref_lf[name], fs, f0), "floor_uVrms": line(data[ch], fs, f0),
                       "other_rails_now_uVrms": {NAMES[c]: line(data[c], fs, f0) for c in data if c != ch}}
    others = [c for c in data if c != ch]
    fc, C = signal.coherence(x, signal.detrend(data[others[0]]), fs, nperseg=1 << 16)
    res["line_59k"]["coherence_shorted_vs_" + NAMES[others[0]]] = float(C[np.argmin(np.abs(fc - f0))])
    print("\n59.357 kHz line:", json.dumps(res["line_59k"], indent=1))

    fn = f"floor_CH{ch}_{stamp}.json"
    json.dump(res, open(fn, "w"), indent=2)
    print("saved", fn)


NAMES_INV = {v: k for k, v in NAMES.items()}

if __name__ == "__main__":
    main()
