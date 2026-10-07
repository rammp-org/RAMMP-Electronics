"""Steady-state ripple and noise capture of the booster rails (scope only).

The supply is only read, never written.

Captures
  HF  250 us, full bandwidth, NORM acquisition, one channel at a time
      (CH1 10 V out, CH2 5 V in). Band split <1 MHz / 1-20 MHz / >20 MHz.
  LF  1 s, 20 MHz bandwidth limit, HRES, CH1 + CH2 + CH3 in one acquisition
      (phase-coherent). Integrated noise, burst tone, transfer + coherence.

All channels are DC coupled with the vertical offset nulling the rail, and
auto-ranged with explicit clip detection (samples pinned at the screen edge).

Usage:  python ripple.py <tag>        e.g. python ripple.py L0
"""
import json, sys, time

import numpy as np
from scipy import signal

from scpi import Scpi, SCOPE_IP, SUPPLY_IP, read_waveform

NAMES = {1: "10V_out", 2: "5V_in", 3: "12V"}
SCALES = [0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0]
NDIV = 4.0  # divisions either side of centre


def acquire(scope, trig_ch, level, timeout=15):
    record_s = 10 * float(scope.query(":TIM:MAIN:SCAL?"))
    if record_s > 0.05:
        # long records: SING + forced trigger stores nothing on this firmware;
        # free-run for a few records and stop instead
        scope.write(":TRIG:SWE AUTO")
        scope.write(":RUN")
        time.sleep(3 * record_s + 1)
        scope.write(":STOP")
        time.sleep(0.5)
        return
    scope.write(":TRIG:MODE EDGE")
    scope.write(f":TRIG:EDGE:SOUR CHAN{trig_ch}")
    scope.write(f":TRIG:EDGE:LEV {level:.5f}")
    scope.write(":TRIG:SWE SING")
    scope.write(":SING")
    t0 = time.time()
    forced = False
    while time.time() - t0 < timeout:
        time.sleep(0.3)
        if scope.query(":TRIG:STAT?") == "STOP":
            return
        if not forced and time.time() - t0 > 4:
            scope.write(":TFOR")
            forced = True
    raise RuntimeError("acquisition did not complete")


def clip_check(v, centre, scale):
    dev = np.abs(v - centre) / scale
    clipped = np.mean(dev >= NDIV * 0.985)
    span_div = (v.max() - v.min()) / scale
    return clipped, span_div


def autorange(scope, chans, dc, scale0, trig_ch, label):
    """Iterate scale/offset until no channel clips and each fills >= 1.5 div."""
    scale = dict(scale0)
    for it in range(6):
        for ch in chans:
            scope.write(f":CHAN{ch}:SCAL {scale[ch]}")
            scope.write(f":CHAN{ch}:OFFS {-dc[ch]:.5f}")
        time.sleep(0.5)
        acquire(scope, trig_ch, dc[trig_ch])
        data, ok = {}, True
        for ch in chans:
            t, v = read_waveform(scope, ch)
            offs = float(scope.query(f":CHAN{ch}:OFFS?"))
            sc = float(scope.query(f":CHAN{ch}:SCAL?"))
            clipped, span = clip_check(v, -offs, sc)
            data[ch] = (t, v)
            i = SCALES.index(min(SCALES, key=lambda s: abs(s - sc)))
            msg = f"  [{label} it{it}] CH{ch} {sc*1e3:g} mV/div offs {offs:.4f}: span {span:.2f} div, clipped {clipped*100:.3f} %"
            if clipped > 0:
                ok = False
                scale[ch] = SCALES[min(i + 1, len(SCALES) - 1)]
                dc[ch] = float(np.median(v)) if clipped < 0.2 else dc[ch]
                msg += " -> up"
            elif span < 1.5 and i > 0:
                ok = False
                scale[ch] = SCALES[i - 1]
                msg += " -> down"
            dc[ch] = float(np.mean(v)) if clipped == 0 else dc[ch]
            print(msg, flush=True)
        if ok:
            return data, {ch: float(scope.query(f":CHAN{ch}:SCAL?")) for ch in chans}
    print(f"  [{label}] autorange did not converge; using last capture")
    return data, {ch: float(scope.query(f":CHAN{ch}:SCAL?")) for ch in chans}


def fft_band(v, fs, lo, hi):
    V = np.fft.rfft(v)
    f = np.fft.rfftfreq(len(v), 1 / fs)
    V[(f < lo) | (f >= hi)] = 0
    return np.fft.irfft(V, len(v))


def band_table(v, fs):
    v = v - v.mean()
    bands = {"<1MHz": (0, 1e6), "1-20MHz": (1e6, 20e6), ">20MHz": (20e6, fs / 2), "total": (0, fs / 2)}
    out = {}
    for k, (lo, hi) in bands.items():
        x = fft_band(v, fs, lo, hi) if k != "total" else v
        rms = float(np.sqrt(np.mean(x ** 2)))
        out[k] = {"Vpp_mV": float(np.ptp(x)) * 1e3, "Vrms_mV": rms * 1e3,
                  "crest": float(np.max(np.abs(x)) / rms) if rms else None}
    # dominant ringing frequency above 20 MHz
    P = np.abs(np.fft.rfft(v * np.hanning(len(v)))) ** 2
    f = np.fft.rfftfreq(len(v), 1 / fs)
    m = f > 20e6
    out["ring_peak_MHz"] = float(f[m][np.argmax(P[m])] / 1e6) if m.any() else None
    return out


def lf_analysis(data, fs):
    x = {ch: signal.detrend(v, type="linear") for ch, (t, v) in data.items()}
    nper = 1 << 18
    res = {}
    for ch, v in x.items():
        f, P = signal.welch(v, fs, window="hann", nperseg=nper)
        df = f[1] - f[0]
        integ = {}
        for hi in (100, 1e3, 1e4, 5e4, 5e5):
            m = (f > 0) & (f <= hi)
            integ[f"DC-{hi:g}Hz_uVrms"] = float(np.sqrt(np.sum(P[m]) * df)) * 1e6
        # ripple below 1 MHz in the time domain (BWL 20 MHz, HRES ~ fs/2)
        res[NAMES[ch]] = {"integrated": integ,
                          "Vpp_mV": float(np.ptp(v)) * 1e3,
                          "Vrms_mV": float(np.std(v)) * 1e3,
                          "_f": f, "_P": P}
    # burst tone: strongest line 1 kHz - 400 kHz on the 12 V rail (source)
    src = res["12V"]
    f, P = src["_f"], src["_P"]
    m = (f > 1e3) & (f < 4e5)
    f0 = float(f[m][np.argmax(P[m])])
    tones = {}
    for h in range(1, 5):
        fh = h * f0
        for name in res:
            fr, Pr = res[name]["_f"], res[name]["_P"]
            k = np.argmin(np.abs(fr - fh))
            sl = slice(max(k - 3, 1), k + 4)
            amp = float(np.sqrt(np.sum(Pr[sl]) * (fr[1] - fr[0]))) * 1e6
            tones.setdefault(f"H{h} {fh:.0f}Hz", {})[f"{name}_uVrms"] = amp
    # transfer and coherence 12 V -> 10 V (LDO rejection), 12 V -> 5 V in
    xfer = {}
    for a, b in ((3, 1), (3, 2)):
        f, Pxy = signal.csd(x[a], x[b], fs, nperseg=nper)
        _, Pxx = signal.welch(x[a], fs, nperseg=nper)
        _, C = signal.coherence(x[a], x[b], fs, nperseg=nper)
        H = np.abs(Pxy) / Pxx
        key = f"{NAMES[a]}->{NAMES[b]}"
        xfer[key] = {}
        for h in range(1, 5):
            k = np.argmin(np.abs(f - h * f0))
            xfer[key][f"H{h}"] = {"gain_dB": float(20 * np.log10(H[k])), "coherence": float(C[k])}
    for name in res:
        res[name].pop("_f"); res[name].pop("_P")
    return {"rails": res, "burst_fundamental_Hz": f0, "tones": tones, "transfer": xfer}


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else "run"
    stamp = time.strftime("%Y%m%d_%H%M%S")
    psu = Scpi(SUPPLY_IP)
    supply_before = psu.query(":MEAS:ALL? CH1")
    assert psu.query(":OUTP? CH1") == "1", "supply CH1 is off"
    psu.close()

    scope = Scpi(SCOPE_IP)
    dc = {1: 10.006, 2: 4.984, 3: 11.991}
    result = {"tag": tag, "time": stamp, "supply_before": supply_before}

    # ---- HF: one channel at a time, full bandwidth ----
    scope.write(":ACQ:TYPE NORM")
    scope.write(":TIM:MAIN:OFFS 0")
    scope.write(":TIM:MAIN:SCAL 25e-6")
    scope.write(":ACQ:MDEP 1M")
    hf = {}
    for ch in (1, 2):
        for c in (1, 2, 3, 4):
            scope.write(f":CHAN{c}:DISP {'ON' if c == ch else 'OFF'}")
        scope.write(f":CHAN{ch}:BWL OFF")
        scope.write(f":CHAN{ch}:COUP DC")
        data, sc = autorange(scope, [ch], dc, {ch: 0.1}, ch, f"HF CH{ch}")
        t, v = data[ch]
        fs = 1 / (t[1] - t[0])
        hf[NAMES[ch]] = {"fs_GSa": fs / 1e9, "npts": len(v), "scale_V_div": sc[ch], **band_table(v, fs)}
        np.savez(f"ripple_{tag}_HF_{NAMES[ch]}_{stamp}.npz", t=t, v=v)
        print(f"HF {NAMES[ch]}: {json.dumps(hf[NAMES[ch]], indent=1)}", flush=True)
    result["HF"] = hf

    # ---- LF: three channels, one acquisition ----
    for c in (1, 2, 3):
        scope.write(f":CHAN{c}:DISP ON")
        scope.write(f":CHAN{c}:BWL 20M")
    scope.write(":CHAN4:DISP OFF")
    scope.write(":ACQ:TYPE HRES")
    scope.write(":TIM:MAIN:SCAL 0.1")
    scope.write(":ACQ:MDEP 1M")
    # CH3 offset is limited to -10 V at sensitive scales, so the 12 V rail
    # cannot be nulled; AC-couple it (corner of a few Hz) and let it settle
    scope.write(":CHAN3:COUP AC")
    dc[3] = 0.0
    print("  CH3 AC coupled; settling 30 s", flush=True)
    time.sleep(30)
    data, sc = autorange(scope, [1, 2, 3], dc, {1: 0.01, 2: 0.05, 3: 0.05}, 1, "LF")
    scope.write(":CHAN3:COUP DC")
    t = data[1][0]
    fs = 1 / (t[1] - t[0])
    np.savez(f"ripple_{tag}_LF_{stamp}.npz", t=t, **{NAMES[c]: data[c][1] for c in data})
    lf = lf_analysis(data, fs)
    lf.update({"fs_MSa": fs / 1e6, "npts": len(t), "scales_V_div": {NAMES[c]: sc[c] for c in sc},
               "dc_V": {NAMES[c]: float(np.mean(data[c][1])) for c in data}})
    result["LF"] = lf
    result["scope_errors"] = scope.errors()
    scope.close()

    psu = Scpi(SUPPLY_IP)
    result["supply_after"] = psu.query(":MEAS:ALL? CH1")
    result["supply_mode"] = psu.query(":OUTP:MODE? CH1")
    psu.close()

    fn = f"ripple_{tag}_{stamp}.json"
    with open(fn, "w") as f:
        json.dump(result, f, indent=2)
    print(json.dumps(result, indent=2))
    print("saved", fn)


if __name__ == "__main__":
    main()
