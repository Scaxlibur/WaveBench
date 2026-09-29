"""命令行入口：所有结果均输出 JSON，便于 TeleAgent 解析。"""

from __future__ import annotations

import argparse
import json
import math
import sys

from .artifacts import write_single_frame
from .bridge import BridgeServer, run_connect, run_expose
from .core import analyze_samples, demo_waveform, discover_devices, inspect_device, load_waveform, measure_scope
from .protocols import ProtocolRegistry, capture_socketcan, read_payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="天翼AI电子科研 TeleAgent Skill")
    sub = parser.add_subparsers(dest="command", required=True)
    discover = sub.add_parser("discover", help="扫描网段并读取 SCPI *IDN?")
    discover.add_argument("--subnet", required=True)
    discover.add_argument("--ports", default="5025,5555,4000,502")
    inspect = sub.add_parser("inspect", help="只读检查设备")
    inspect.add_argument("--address", required=True)
    inspect.add_argument("--port", type=int, default=5025)
    measure = sub.add_parser("measure", help="读取示波器测量值")
    measure.add_argument("--address", required=True)
    measure.add_argument("--port", type=int, default=5025)
    measure.add_argument("--channel", default="CHAN1")
    analyze = sub.add_parser("analyze", help="离线分析波形")
    analyze.add_argument("--file")
    analyze.add_argument("--sample-rate", type=float, default=100_000)
    analyze.add_argument("--demo", action="store_true")
    frame = sub.add_parser("frame", help="生成单帧 JSON/CSV/SVG 工件")
    frame.add_argument("--file")
    frame.add_argument("--sample-rate", type=float, default=100_000)
    frame.add_argument("--output-dir", default="artifacts")
    frame.add_argument("--prefix", default="capture")
    protocol = sub.add_parser("protocol", help="识别或解析厂商自定义协议")
    protocol_sub = protocol.add_subparsers(dest="protocol_command", required=True)
    identify = protocol_sub.add_parser("identify", help="输出候选协议与置信度")
    identify.add_argument("--file", required=True)
    identify.add_argument("--manifest-dir")
    decode = protocol_sub.add_parser("decode", help="解析协议并生成标准波形工件")
    decode.add_argument("--file", required=True)
    decode.add_argument("--manifest-dir")
    decode.add_argument("--sample-rate", type=float)
    decode.add_argument("--output-dir", default="artifacts")
    decode.add_argument("--source", default="protocol-input")
    can_capture = protocol_sub.add_parser("capture-can", help="Arm Linux/SocketCAN 采集若干帧")
    can_capture.add_argument("--channel", default="can0")
    can_capture.add_argument("--count", type=int, default=16)
    can_capture.add_argument("--timeout", type=float, default=1.0)
    bridge = sub.add_parser("bridge", help="跨主机反向桥接")
    bridge_sub = bridge.add_subparsers(dest="bridge_command", required=True)
    serve = bridge_sub.add_parser("serve", help="启动中继端")
    serve.add_argument("--listen", default="0.0.0.0:9000")
    expose = bridge_sub.add_parser("expose", help="设备所在主机主动暴露设备")
    expose.add_argument("--server", required=True, help="中继 host:port")
    expose.add_argument("--token", required=True)
    expose.add_argument("--local", required=True, help="设备 host:port")
    connect = bridge_sub.add_parser("connect", help="Agent 主机创建本地代理端口")
    connect.add_argument("--server", required=True)
    connect.add_argument("--token", required=True)
    connect.add_argument("--listen", required=True, help="本地代理 host:port")
    return parser


def main(argv: list[str] | None = None) -> int:
    # TeleAgent 的工作区可能使用 GBK/CP1252 控制台；JSON 与中文路径统一用 UTF-8 输出。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass
    args = _parser().parse_args(argv)
    try:
        if args.command == "discover":
            ports = tuple(int(port.strip()) for port in args.ports.split(",") if port.strip())
            result = {"devices": discover_devices(args.subnet, ports)}
        elif args.command == "inspect":
            result = inspect_device(args.address, args.port)
        elif args.command == "measure":
            result = measure_scope(args.address, args.port, args.channel)
        elif args.command == "frame":
            if args.file:
                samples, rate = load_waveform(args.file)
                if rate == 1.0:
                    rate = args.sample_rate
                source = args.file
            else:
                demo = demo_waveform(args.sample_rate)
                samples = [0.8 * math.sin(2 * math.pi * 1000 * i / args.sample_rate) + 0.02
                           for i in range(demo["samples"])]
                rate, source = args.sample_rate, "built-in-demo"
            result = {"source": source, "artifacts": write_single_frame(samples, rate, args.output_dir, args.prefix)}
        elif args.command == "protocol":
            registry = ProtocolRegistry()
            manifest_dir = getattr(args, "manifest_dir", None)
            if manifest_dir:
                registry.load_manifests(manifest_dir)
            if args.protocol_command == "identify":
                payload = read_payload(args.file)
                result = registry.identify(payload, {"source": args.file})
            elif args.protocol_command == "decode":
                payload = read_payload(args.file)
                frame = registry.decode(payload, args.source, {"source": args.file})
                if args.sample_rate:
                    frame.sample_rate_hz = args.sample_rate
                if not frame.sample_rate_hz:
                    raise ValueError("协议未提供 sample_rate_hz，请使用 --sample-rate")
                result = frame.as_dict()
                result["artifacts"] = write_single_frame(frame.samples, frame.sample_rate_hz, args.output_dir,
                                                         "protocol_capture", frame.metadata)
            else:
                result = {"channel": args.channel, "frames": capture_socketcan(args.channel, args.count, args.timeout)}
        elif args.command == "bridge":
            if args.bridge_command == "serve":
                host, port = args.listen.rsplit(":", 1)
                BridgeServer(host, int(port)).serve_forever()
                return 0
            if args.bridge_command == "expose":
                run_expose(args.server, args.token, args.local)
                return 0
            run_connect(args.server, args.token, args.listen)
            return 0
        elif args.demo or not args.file:
            result = demo_waveform(args.sample_rate)
        else:
            samples, rate = load_waveform(args.file)
            result = {"source": args.file, **analyze_samples(samples, rate if rate != 1.0 else args.sample_rate)}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
