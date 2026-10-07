"""Minimal DHCP server that leases one fixed address to the Rigol DP2031 only.

The DP2031 will not accept a static IP from its front panel, and the bench
switch has no DHCP server, so the supply falls back to a 169.254.x.x address
this laptop cannot reach. This script answers DHCP requests from the supply's
MAC alone (every other device is ignored), hands it SUPPLY_IP, then confirms
with a read-only *IDN? on port 5555 and exits.

The supply only asks for a lease at boot: start this script, then power-cycle
the supply.

Usage:  python dhcp_supply.py [timeout_s]
"""
import socket, struct, sys, time

SUPPLY_MAC = bytes.fromhex("0019af1700d8")
SERVER_IP = "192.168.1.50"     # this laptop's manual Ethernet address
SUPPLY_IP = "192.168.1.102"
NETMASK = "255.255.255.0"
LEASE_S = 7 * 24 * 3600
MAGIC = b"\x63\x82\x53\x63"
MSG = {1: "DISCOVER", 2: "OFFER", 3: "REQUEST", 4: "DECLINE", 5: "ACK",
       6: "NAK", 7: "RELEASE", 8: "INFORM"}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def parse_options(data):
    opts, i = {}, 240
    while i < len(data):
        code = data[i]
        if code == 255:
            break
        if code == 0:
            i += 1
            continue
        length = data[i + 1]
        opts[code] = data[i + 2:i + 2 + length]
        i += 2 + length
    return opts


def build_reply(req, msg_type, yiaddr):
    xid, flags, giaddr, chaddr = req[4:8], req[10:12], req[24:28], req[28:44]
    pkt = struct.pack("!BBBB", 2, 1, 6, 0) + xid + b"\x00\x00" + flags
    pkt += b"\x00" * 4                                   # ciaddr
    pkt += socket.inet_aton(yiaddr)                      # yiaddr
    pkt += socket.inet_aton(SERVER_IP)                   # siaddr
    pkt += giaddr + chaddr + b"\x00" * 192 + MAGIC
    pkt += bytes([53, 1, msg_type])
    pkt += bytes([54, 4]) + socket.inet_aton(SERVER_IP)
    if msg_type != 6:
        pkt += bytes([51, 4]) + struct.pack("!I", LEASE_S)
        pkt += bytes([58, 4]) + struct.pack("!I", LEASE_S // 2)
        pkt += bytes([59, 4]) + struct.pack("!I", LEASE_S * 7 // 8)
        pkt += bytes([1, 4]) + socket.inet_aton(NETMASK)
    pkt += b"\xff"
    return pkt + b"\x00" * max(0, 300 - len(pkt))


def idn(ip, tries=10):
    for _ in range(tries):
        try:
            s = socket.create_connection((ip, 5555), timeout=2)
            s.sendall(b"*IDN?\n")
            s.settimeout(3)
            r = s.recv(256).decode(errors="replace").strip()
            s.close()
            return r
        except OSError:
            time.sleep(2)
    return None


def main():
    timeout = float(sys.argv[1]) if len(sys.argv) > 1 else 600
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind((SERVER_IP, 67))
    sock.settimeout(1)
    log(f"DHCP server on {SERVER_IP}:67, leasing {SUPPLY_IP} to "
        f"{SUPPLY_MAC.hex(':')} only. Power-cycle the supply now.")

    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            data, src = sock.recvfrom(2048)
        except socket.timeout:
            continue
        if len(data) < 240 or data[0] != 1 or data[236:240] != MAGIC:
            continue
        mac = data[28:34]
        opts = parse_options(data)
        mtype = opts.get(53, b"\x00")[0]
        if mac != SUPPLY_MAC:
            log(f"ignored {MSG.get(mtype, mtype)} from {mac.hex(':')}")
            continue
        log(f"{MSG.get(mtype, mtype)} from supply")

        if mtype == 1:
            sock.sendto(build_reply(data, 2, SUPPLY_IP), ("255.255.255.255", 68))
            log(f"OFFER {SUPPLY_IP}")
        elif mtype == 3:
            wanted = opts.get(50) or data[12:16]
            server = opts.get(54)
            if server and server != socket.inet_aton(SERVER_IP):
                log("REQUEST is for another server; ignoring")
                continue
            if wanted not in (socket.inet_aton(SUPPLY_IP), b"\x00" * 4):
                sock.sendto(build_reply(data, 6, "0.0.0.0"), ("255.255.255.255", 68))
                log(f"NAK (asked for {socket.inet_ntoa(wanted)})")
                continue
            sock.sendto(build_reply(data, 5, SUPPLY_IP), ("255.255.255.255", 68))
            log(f"ACK {SUPPLY_IP}; checking *IDN? ...")
            time.sleep(3)
            r = idn(SUPPLY_IP)
            if r:
                log(f"{SUPPLY_IP} -> {r}")
                return 0
            log("no *IDN? reply yet; still serving")
    log("timed out without a confirmed lease")
    return 1


if __name__ == "__main__":
    sys.exit(main())
