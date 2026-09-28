import sys, socket, time, json

# --- Part 1: VISA resource discovery ---
print("=== VISA Resource Discovery (pyvisa-py, all interfaces) ===")
try:
    import pyvisa
    rm = pyvisa.ResourceManager("@py")
    resources = list(rm.list_resources())
    print(f"  Total VISA resources: {len(resources)}")
    for r in resources:
        print(f"  Found: {r}")
        try:
            inst = rm.open_resource(r)
            inst.timeout = 2000
            idn = inst.query("*IDN?").strip()
            print(f"    *IDN?: {idn}")
            inst.close()
        except Exception as e:
            print(f"    Query failed: {e}")
    if not resources:
        print("  (no VISA resources found)")
except Exception as e:
    print(f"  VISA error: {e}")

# --- Part 2: SCPI/TCP port scan on 10.17.216.0/24 ---
print("\n=== SCPI/TCP Port Scan (10.17.216.0/24) ===")
import ipaddress
from concurrent.futures import ThreadPoolExecutor, as_completed

COMMON_PORTS = (5025, 5555, 4000, 502, 1110, 1234, 7654, 9999)

def scpi_query(host, port, command="*IDN?", timeout=0.35):
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall((command + "\n").encode("ascii"))
            chunks = []
            while True:
                try:
                    data = sock.recv(4096)
                except socket.timeout:
                    break
                if not data:
                    break
                chunks.append(data)
                if b"\n" in data or sum(map(len, chunks)) >= 65536:
                    break
            return b"".join(chunks).decode("utf-8", "replace").strip()
    except (OSError, UnicodeError):
        return None

network = ipaddress.ip_network("10.17.216.0/24", strict=False)
targets = [(str(ip), int(port)) for ip in network.hosts() for port in COMMON_PORTS]
print(f"  Scanning {len(targets)} targets ({len(list(network.hosts()))} hosts x {len(COMMON_PORTS)} ports)...")

found = []
with ThreadPoolExecutor(max_workers=64) as pool:
    futures = {pool.submit(lambda t: (t[0], t[1], scpi_query(t[0], t[1])), t): t for t in targets}
    for future in as_completed(futures):
        host, port, idn = future.result()
        if idn:
            found.append({"ip": host, "port": port, "idn": idn})
            print(f"  FOUND: {host}:{port} -> {idn}")

if not found:
    print("  (no SCPI devices found on /24 subnet)")
else:
    print(f"\n  Summary: {len(found)} device(s) found")

# --- Part 3: Also try broader LXI mDNS discovery via zeroconf ---
print("\n=== LXI mDNS Discovery (zeroconf) ===")
try:
    from zeroconf import Zeroconf, ServiceBrowser
    import threading

    lxi_services = []
    class LXIListener:
        def add_service(self, zc, type_, name):
            info = zc.get_service_info(type_, name)
            if info:
                lxi_services.append({"name": name, "type": type_, "address": socket.inet_ntoa(info.addresses[0]) if info.addresses else None, "port": info.port})
        def remove_service(self, zc, type_, name):
            pass
        def update_service(self, zc, type_, name):
            pass

    zc = Zeroconf()
    listener = LXIListener()
    browser = ServiceBrowser(zc, "_lxi._tcp.local.", listener)
    time.sleep(3)
    zc.close()

    if lxi_services:
        for svc in lxi_services:
            print(f"  Found LXI: {svc}")
    else:
        print("  (no LXI mDNS services found)")
except Exception as e:
    print(f"  mDNS error: {e}")

print("\n=== Discovery Complete ===")
