# -*- coding: utf-8 -*-
"""LXI mDNS 仪器发现：监听 _scpi._tcp.local / _lxi._tcp.local / _vxi-11._tcp.local 服务。"""
import socket
import sys
import time

from zeroconf import Zeroconf, ServiceBrowser, ServiceListener

SERVICE_TYPES = [
    "_scpi._tcp.local.",
    "_lxi._tcp.local.",
    "_vxi-11._tcp.local.",
    "_visa._tcp.local.",
    "_works._tcp.local.",
]


class LXIListener(ServiceListener):
    def __init__(self):
        self.found = []

    def add_service(self, zc, type_, name):
        info = zc.get_service_info(type_, name)
        if info is None:
            return
        entry = {
            "service_type": type_,
            "name": name,
            "server": info.server,
            "addresses": [socket.inet_ntoa(a) for a in info.addresses],
            "port": info.port,
            "properties": {
                k.decode("utf-8", "replace") if isinstance(k, bytes) else k:
                (v.decode("utf-8", "replace") if isinstance(v, bytes) else v)
                for k, v in (info.properties or {}).items()
            },
        }
        self.found.append(entry)
        print("FOUND: %s" % entry)

    def update_service(self, zc, type_, name):
        pass

    def remove_service(self, zc, type_, name):
        pass


def main(timeout_s=12):
    print("mDNS discovery: listening %ss for %s" % (timeout_s, ", ".join(SERVICE_TYPES)))
    zc = Zeroconf()
    listener = LXIListener()
    for st in SERVICE_TYPES:
        ServiceBrowser(zc, st, listener)
    end = time.time() + timeout_s
    try:
        while time.time() < end:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        zc.close()
    print("RESULT_COUNT=%d" % len(listener.found))
    return 0


if __name__ == "__main__":
    sys.exit(main())
