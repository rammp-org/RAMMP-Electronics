"""Sweep for Rigol instruments answering *IDN? on SCPI port 5555 (read-only).

From BENCH_INSTRUMENT_HANDOFF.md step 4: sweeps every /24 the laptop has an
IPv4 address on, plus 192.168.1.0/24.
"""
import socket, ipaddress, concurrent.futures, subprocess, re

nets = {"192.168.1.0/24"}
out = subprocess.run(["ipconfig"], capture_output=True, text=True).stdout
for ip in re.findall(r"IPv4 Address[ .]*: (\d+\.\d+\.\d+\.\d+)", out):
    if not ip.startswith("127."):
        nets.add(str(ipaddress.ip_network(ip + "/24", strict=False)))

def probe(ip):
    try:
        s = socket.create_connection((str(ip), 5555), timeout=0.5)
        s.sendall(b"*IDN?\n"); s.settimeout(1.5)
        return str(ip), s.recv(256).decode(errors="replace").strip()
    except OSError:
        return None

print("sweeping:", sorted(nets))
hosts = [h for n in nets for h in ipaddress.ip_network(n).hosts()]
with concurrent.futures.ThreadPoolExecutor(64) as ex:
    for r in ex.map(probe, hosts):
        if r: print(r[0], "->", r[1])
print("done")
