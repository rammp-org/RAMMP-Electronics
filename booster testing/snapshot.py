"""Read-only snapshot of the DP2031 supply and MHO984 scope settings.

Sends queries only (every command ends in '?'), so nothing on either
instrument is changed. Any query the firmware does not recognise is reported
and the error queue is drained with :SYST:ERR? afterwards.

Usage:  python snapshot.py
"""
import json, socket, time

SCOPE = "192.168.1.101"
SUPPLY = "192.168.1.102"


class Scpi:
    def __init__(self, ip, timeout=2.0):
        self.s = socket.create_connection((ip, 5555), timeout=5)
        self.s.settimeout(timeout)

    def query(self, cmd):
        assert "?" in cmd.split()[0], f"refusing non-query {cmd!r}"
        self.s.sendall((cmd + "\n").encode())
        buf = b""
        try:
            while not buf.endswith(b"\n"):
                chunk = self.s.recv(4096)
                if not chunk:
                    break
                buf += chunk
        except socket.timeout:
            return None
        return buf.decode(errors="replace").strip()

    def drain_errors(self):
        errs = []
        for _ in range(20):
            e = self.query(":SYST:ERR?")
            if e is None or e.startswith(("0,", "+0,")):
                break
            errs.append(e)
        return errs

    def close(self):
        self.s.close()


def run(name, ip, queries):
    print(f"\n=== {name} ({ip}) ===")
    inst = Scpi(ip)
    out = {}
    for q in queries:
        r = inst.query(q)
        out[q] = r
        print(f"{q:<34} {r if r is not None else '(no reply)'}")
        time.sleep(0.05)
    errs = inst.drain_errors()
    if errs:
        print("errors cleared:", errs)
    out["_errors"] = errs
    inst.close()
    time.sleep(1)  # instruments refuse rapid reconnection
    return out


supply_q = ["*IDN?"]
for ch in (1, 2, 3):
    supply_q += [
        f":SOUR{ch}:VOLT?", f":SOUR{ch}:CURR?",
        f":OUTP? CH{ch}", f":MEAS:ALL? CH{ch}",
        f":OUTP:OVP? CH{ch}", f":OUTP:OVP:VAL? CH{ch}",
        f":OUTP:OCP? CH{ch}", f":OUTP:OCP:VAL? CH{ch}",
        f":OUTP:MODE? CH{ch}",
    ]

scope_q = ["*IDN?", ":TRIG:STAT?",
           ":TIM:MAIN:SCAL?", ":TIM:MAIN:OFFS?",
           ":ACQ:TYPE?", ":ACQ:MDEP?", ":ACQ:SRAT?",
           ":TRIG:MODE?", ":TRIG:SWE?", ":TRIG:EDGE:SOUR?",
           ":TRIG:EDGE:LEV?", ":TRIG:EDGE:SLOP?"]
for ch in (1, 2, 3, 4):
    scope_q += [f":CHAN{ch}:DISP?", f":CHAN{ch}:SCAL?", f":CHAN{ch}:OFFS?",
                f":CHAN{ch}:COUP?", f":CHAN{ch}:PROB?", f":CHAN{ch}:BWL?",
                f":CHAN{ch}:IMP?", f":CHAN{ch}:INV?"]

snap = {"time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "supply": run("DP2031 supply", SUPPLY, supply_q),
        "scope": run("MHO984 scope", SCOPE, scope_q)}

fn = time.strftime("snapshot_%Y%m%d_%H%M%S.json")
with open(fn, "w") as f:
    json.dump(snap, f, indent=2)
print(f"\nsaved {fn}")
