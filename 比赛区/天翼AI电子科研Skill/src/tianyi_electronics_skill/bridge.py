"""跨主机反向 TCP 桥接。

设备侧主机主动连接中继端，Agent 侧通过本地监听端口访问设备。适合 A/B 不在同一局域网、
但设备侧能访问一台公网/VPN/SSH 转发主机的场景。桥接只转发字节，不解释协议。
"""

from __future__ import annotations

import socket
import threading
from dataclasses import dataclass


def _split_address(address: str) -> tuple[str, int]:
    host, separator, port = address.rpartition(":")
    if not separator or not port.isdigit():
        raise ValueError(f"地址必须是 host:port: {address}")
    return host or "127.0.0.1", int(port)


def _relay(left: socket.socket, right: socket.socket) -> None:
    def pump(source: socket.socket, target: socket.socket) -> None:
        try:
            while True:
                data = source.recv(65536)
                if not data:
                    break
                target.sendall(data)
        except OSError:
            pass
        finally:
            for sock in (source, target):
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    first = threading.Thread(target=pump, args=(left, right), daemon=True)
    second = threading.Thread(target=pump, args=(right, left), daemon=True)
    first.start(); second.start(); first.join(); second.join()


@dataclass
class _Registration:
    connection: socket.socket
    ready: threading.Event
    start: threading.Event
    peer: socket.socket | None = None


class BridgeServer:
    def __init__(self, host: str, port: int):
        self.host, self.port = host, port
        self.registrations: dict[str, _Registration] = {}
        self.lock = threading.Lock()

    def serve_forever(self) -> None:
        with socket.create_server((self.host, self.port), reuse_port=False) as listener:
            print(f"bridge server listening on {self.host}:{self.port}", flush=True)
            while True:
                connection, address = listener.accept()
                threading.Thread(target=self._handle, args=(connection, address), daemon=True).start()

    def _handle(self, connection: socket.socket, address: tuple[str, int]) -> None:
        connection.settimeout(60)
        try:
            reader = connection.makefile("rb")
            first = reader.readline().decode("ascii", "replace").strip().split()
            if len(first) != 2 or first[0] not in {"REGISTER", "CONNECT"}:
                connection.sendall(b"ERROR invalid-handshake\n")
                reader.close(); connection.close()
                return
            role, token = first
            if role == "REGISTER":
                self._register(token, connection, reader)
            else:
                reader.close()
                self._connect(token, connection)
        except (OSError, UnicodeError):
            try:
                connection.close()
            except OSError:
                pass

    def _register(self, token: str, connection: socket.socket, reader) -> None:
        slot = _Registration(connection, threading.Event(), threading.Event())
        with self.lock:
            previous = self.registrations.get(token)
            if previous:
                try:
                    previous.connection.close()
                except OSError:
                    pass
            self.registrations[token] = slot
        try:
            while not slot.start.is_set():
                line = reader.readline()
                if not line:
                    break
                if line.strip() == b"READY":
                    slot.ready.set()
                    slot.start.wait(60)
                    break
        finally:
            with self.lock:
                if self.registrations.get(token) is slot:
                    self.registrations.pop(token, None)
            if slot.peer:
                _relay(connection, slot.peer)
            try:
                reader.close()
            except OSError:
                pass
            try:
                connection.close()
            except OSError:
                pass

    def _connect(self, token: str, connection: socket.socket) -> None:
        with self.lock:
            slot = self.registrations.get(token)
        if not slot:
            connection.sendall(b"ERROR no-registered-device\n")
            return
        slot.peer = connection
        slot.connection.sendall(b"OPEN\n")
        if not slot.ready.wait(60):
            connection.sendall(b"ERROR device-not-ready\n")
            return
        slot.connection.sendall(b"START\n")
        connection.sendall(b"START\n")
        slot.start.set()
        # REGISTER 线程负责实际双向转发，CONNECT 线程只负责配对握手。


def run_expose(server: str, token: str, local: str) -> None:
    server_host, server_port = _split_address(server)
    local_host, local_port = _split_address(local)
    with socket.create_connection((server_host, server_port), timeout=10) as upstream:
        upstream.sendall(f"REGISTER {token}\n".encode("ascii"))
        reader = upstream.makefile("rb")
        while True:
            line = reader.readline()
            if not line:
                return
            command = line.strip()
            if command == b"OPEN":
                try:
                    local_socket = socket.create_connection((local_host, local_port), timeout=10)
                except OSError:
                    upstream.sendall(b"ERROR local-device-unreachable\n")
                    return
                upstream.sendall(b"READY\n")
                if reader.readline().strip() != b"START":
                    local_socket.close()
                    return
                _relay(upstream, local_socket)
                return


def run_connect(server: str, token: str, listen: str) -> None:
    server_host, server_port = _split_address(server)
    listen_host, listen_port = _split_address(listen)
    with socket.create_server((listen_host, listen_port), reuse_port=False) as listener:
        print(f"bridge proxy listening on {listen_host}:{listen_port}", flush=True)
        while True:
            local_socket, _ = listener.accept()
            threading.Thread(target=_connect_once, args=(local_socket, server_host, server_port, token), daemon=True).start()


def _connect_once(local_socket: socket.socket, server_host: str, server_port: int, token: str) -> None:
    try:
        upstream = socket.create_connection((server_host, server_port), timeout=10)
        upstream.sendall(f"CONNECT {token}\n".encode("ascii"))
        if upstream.makefile("rb").readline().strip() != b"START":
            local_socket.close(); upstream.close(); return
        _relay(local_socket, upstream)
    except OSError:
        local_socket.close()
