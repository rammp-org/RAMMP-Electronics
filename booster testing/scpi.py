"""Raw-socket SCPI client for the bench Rigol instruments (port 5555)."""
import socket, time

import numpy as np

SCOPE_IP = "192.168.1.101"
SUPPLY_IP = "192.168.1.102"


class Scpi:
    def __init__(self, ip, timeout=5.0):
        self.ip = ip
        for attempt in range(5):
            try:
                self.s = socket.create_connection((ip, 5555), timeout=5)
                break
            except OSError:
                if attempt == 4:
                    raise
                time.sleep(1.5)  # instruments refuse rapid reconnection
        self.s.settimeout(timeout)

    def write(self, cmd):
        self.s.sendall((cmd + "\n").encode())
        time.sleep(0.05)

    def query(self, cmd):
        self.s.sendall((cmd + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = self.s.recv(4096)
            if not chunk:
                break
            buf += chunk
        return buf.decode(errors="replace").strip()

    def read_block(self, cmd):
        """Send a query that returns an IEEE 488.2 definite-length block."""
        self.s.sendall((cmd + "\n").encode())
        head = b""
        while len(head) < 2:
            head += self.s.recv(2 - len(head))
        assert head[:1] == b"#", f"bad block header {head!r}"
        ndig = int(head[1:2])
        lenb = b""
        while len(lenb) < ndig:
            lenb += self.s.recv(ndig - len(lenb))
        n = int(lenb)
        data = bytearray()
        while len(data) < n:
            data += self.s.recv(min(1 << 20, n - len(data)))
        # trailing newline
        try:
            self.s.settimeout(0.5)
            self.s.recv(16)
        except socket.timeout:
            pass
        finally:
            self.s.settimeout(5.0)
        return bytes(data)

    def errors(self):
        errs = []
        for _ in range(20):
            e = self.query(":SYST:ERR?")
            if e.startswith(("0,", "+0,")):
                break
            errs.append(e)
        return errs

    def close(self):
        self.s.close()
        time.sleep(1.0)


def read_waveform(scope, ch):
    """Read the full stopped acquisition of channel ch. Returns (t, v)."""
    scope.write(f":WAV:SOUR CHAN{ch}")
    scope.write(":WAV:MODE RAW")
    scope.write(":WAV:FORM WORD")
    # the preamble's point count follows the current STAR/STOP window, which
    # persists between reads; open it to the full memory depth first
    mdep = int(float(scope.query(":ACQ:MDEP?")))
    scope.write(":WAV:STAR 1")
    scope.write(f":WAV:STOP {mdep}")
    scope.errors()  # STOP beyond the stored record is clamped with an error
    pre = scope.query(":WAV:PRE?").split(",")
    npts = int(float(pre[2]))
    xinc, xorig, xref = float(pre[4]), float(pre[5]), float(pre[6])
    yinc, yorig, yref = float(pre[7]), float(pre[8]), float(pre[9])
    raw = np.empty(0, dtype=np.uint16)
    chunk = 100_000
    for start in range(1, npts + 1, chunk):
        stop = min(start + chunk - 1, npts)
        scope.write(f":WAV:STAR {start}")
        scope.write(f":WAV:STOP {stop}")
        raw = np.concatenate([raw, np.frombuffer(scope.read_block(":WAV:DATA?"), dtype="<u2")])
    v = (raw.astype(float) - yorig - yref) * yinc
    t = (np.arange(len(v)) - xref) * xinc + xorig
    return t, v
