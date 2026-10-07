"""L0 startup test: power the booster from supply CH1 and capture the turn-on.

Supply CH1: 5.000 V, 0.300 A limit, OVP 5.5 V. CH2/CH3 untouched.
Scope: CH1 = 10 V out (C7), CH2 = 5 V in, CH3 = 12 V (C3), all 10:1 probes.
Single trigger on CH1 rising through 5 V, 2 ms/div.

Safety: if the supply is current limiting, or the 10 V / 12 V rails come up
outside their expected windows, CH1 output is switched off.

Usage:  python startup_L0.py
"""
import json, time

import numpy as np

from scpi import Scpi, SCOPE_IP, SUPPLY_IP, read_waveform

VSET, ILIM, OVP = 5.000, 0.300, 5.5


def setup_scope(scope):
    scope.write(":STOP")
    for ch, (scale, offs) in {1: (2.0, -6.0), 2: (1.0, -2.5), 3: (2.0, -6.0)}.items():
        scope.write(f":CHAN{ch}:DISP ON")
        scope.write(f":CHAN{ch}:PROB 10")
        scope.write(f":CHAN{ch}:COUP DC")
        scope.write(f":CHAN{ch}:BWL OFF")
        scope.write(f":CHAN{ch}:SCAL {scale}")
        scope.write(f":CHAN{ch}:OFFS {offs}")
    scope.write(":CHAN4:DISP OFF")
    scope.write(":ACQ:TYPE NORM")
    # at 100k depth the single-shot RAW record came back rotated (trigger not
    # at t=0); 10k depth gave a correctly aligned record
    scope.write(":ACQ:MDEP 10k")
    scope.write(":TIM:MAIN:SCAL 0.005")
    scope.write(":TIM:MAIN:OFFS 0.02")        # window about -5 ms .. +45 ms
    scope.write(":TRIG:MODE EDGE")
    scope.write(":TRIG:EDGE:SOUR CHAN1")
    scope.write(":TRIG:EDGE:SLOP POS")
    scope.write(":TRIG:EDGE:LEV 5.0")
    scope.write(":TRIG:SWE SING")
    errs = scope.errors()
    if errs:
        print("scope setup errors:", errs)


def setup_supply(psu):
    assert psu.query(":OUTP? CH1") == "0", "CH1 output already on; aborting"
    psu.write(f":SOUR1:VOLT {VSET}")
    psu.write(f":SOUR1:CURR {ILIM}")
    psu.write(f":OUTP:OVP:VAL CH1,{OVP}")
    psu.write(":OUTP:OVP CH1,ON")
    rb = {q: psu.query(q) for q in (":SOUR1:VOLT?", ":SOUR1:CURR?",
                                    ":OUTP:OVP:VAL? CH1", ":OUTP:OVP? CH1")}
    print("supply readback:", rb)
    assert abs(float(rb[":SOUR1:VOLT?"]) - VSET) < 0.01
    assert abs(float(rb[":SOUR1:CURR?"]) - ILIM) < 0.01
    errs = psu.errors()
    if errs:
        print("supply setup errors:", errs)


def crossing(t, v, level):
    idx = np.nonzero(v >= level)[0]
    return t[idx[0]] if len(idx) else None


def analyse(t, v1, v2, v3):
    tail = slice(int(len(t) * 0.9), None)
    final = {k: float(np.mean(v[tail])) for k, v in (("ch1", v1), ("ch2", v2), ("ch3", v3))}
    r = {"final_V": final}
    f1 = final["ch1"]
    t10, t90 = crossing(t, v1, 0.1 * f1), crossing(t, v1, 0.9 * f1)
    r["ch1_rise_10_90_ms"] = (t90 - t10) * 1e3 if t10 is not None and t90 is not None else None
    r["ch1_overshoot_pct"] = float((v1.max() - f1) / f1 * 100)
    r["ch3_overshoot_pct"] = float((v3.max() - final["ch3"]) / final["ch3"] * 100)
    tin = crossing(t, v2, 4.5)
    r["delay_ch2_4V5_to_ch1_9V_ms"] = (crossing(t, v1, 9.0) - tin) * 1e3 if tin is not None and crossing(t, v1, 9.0) is not None else None
    # monotonicity: largest drop after first reaching 90 % of final
    if t90 is not None:
        after = v1[t >= t90]
        r["ch1_max_dip_after_90pct_V"] = float(np.max(np.maximum.accumulate(after) - after))
    r["ch2_min_after_on_V"] = float(v2[t >= (tin if tin is not None else t[0])].min())
    return r


def main():
    scope = Scpi(SCOPE_IP)
    psu = Scpi(SUPPLY_IP)
    try:
        setup_scope(scope)
        setup_supply(psu)

        scope.write(":SING")
        for _ in range(50):
            if scope.query(":TRIG:STAT?") == "WAIT":
                break
            time.sleep(0.1)
        print("scope armed:", scope.query(":TRIG:STAT?"))

        psu.write(":OUTP CH1,ON")
        print("CH1 output ON")
        for _ in range(100):
            if scope.query(":TRIG:STAT?") == "STOP":
                break
            time.sleep(0.1)
        status = scope.query(":TRIG:STAT?")
        print("scope status:", status)

        time.sleep(1.0)
        meas = psu.query(":MEAS:ALL? CH1")
        mode = psu.query(":OUTP:MODE? CH1")
        v_in, i_in, p_in = (float(x) for x in meas.split(","))
        print(f"supply CH1: {v_in:.4f} V  {i_in*1e3:.2f} mA  {p_in:.4f} W  mode={mode}")

        result = {"supply": {"V": v_in, "I": i_in, "P": p_in, "mode": mode},
                  "scope_status": status}
        if status == "STOP":
            t, v1 = read_waveform(scope, 1)
            _, v2 = read_waveform(scope, 2)
            _, v3 = read_waveform(scope, 3)
            np.savez(time.strftime("startup_L0_%Y%m%d_%H%M%S.npz"), t=t, ch1=v1, ch2=v2, ch3=v3)
            result.update(analyse(t, v1, v2, v3))
            # scaling cross-check: CH2 should match the supply's own reading
            result["ch2_vs_supply_V"] = result["final_V"]["ch2"] - v_in

        # safety decision on a fresh free-running capture, not the end of the
        # startup window (the supply's own ramp can outlast the window)
        scope.write(":TRIG:SWE AUTO")
        scope.write(":RUN")
        time.sleep(0.5)
        scope.write(":STOP")
        time.sleep(0.3)
        steady = {ch: float(np.mean(read_waveform(scope, ch)[1])) for ch in (1, 2, 3)}
        result["steady_V"] = steady
        print("steady state:", steady)
        bad = []
        if mode == "CC" or i_in > 0.9 * ILIM:
            bad.append("supply current limiting")
        if not 9.5 <= steady[1] <= 10.5:
            bad.append(f"10 V rail at {steady[1]:.3f} V")
        if not 11.0 <= steady[3] <= 13.0:
            bad.append(f"12 V rail at {steady[3]:.3f} V")
        if bad:
            psu.write(":OUTP CH1,OFF")
            print("SAFETY: CH1 output switched OFF:", bad)
        result["safety_trips"] = bad
        result["output_left_on"] = not bad

        fn = time.strftime("startup_L0_%Y%m%d_%H%M%S.json")
        with open(fn, "w") as f:
            json.dump(result, f, indent=2, default=str)
        print(json.dumps(result, indent=2, default=str))
        print("saved", fn)
    finally:
        scope.close()
        psu.close()


if __name__ == "__main__":
    main()
