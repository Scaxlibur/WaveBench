#!/usr/bin/env python3
"""Install one wheel with its declared dependencies outside the checkout and exercise offline CLI."""

import argparse
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory


def smoke_wheel(wheel: Path, expected_version: str) -> None:
    wheel = wheel.resolve(strict=True)
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PYTHONUTF8"] = "1"
    with TemporaryDirectory(prefix="wavebench-wheel-") as directory:
        root = Path(directory)
        # An unrelated ancestor project must never override installed metadata.
        (root / "pyproject.toml").write_text('[project]\nname = "unrelated"\nversion = "99.0"\n')
        env["WAVEBENCH_LEASE_DIR"] = str(root / "leases")

        def run(*command: str) -> str:
            result = subprocess.run(command, cwd=root, env=env, text=True, encoding="utf-8",
                                    capture_output=True, check=False)
            if result.returncode:
                raise RuntimeError(f"{command!r}\n{result.stdout}\n{result.stderr}")
            return result.stdout

        environment = root / "runtime"
        run("uv", "venv", "--python", sys.executable, str(environment))
        binary = environment / ("Scripts" if os.name == "nt" else "bin")
        python = str(binary / ("python.exe" if os.name == "nt" else "python"))
        cli = str(binary / ("wavebench.exe" if os.name == "nt" else "wavebench"))
        run("uv", "pip", "install", "--python", python, str(wheel))
        run("uv", "pip", "check", "--python", python)
        run(python, "-I", "-c", """
import pathlib, sys, sysconfig
from importlib.metadata import version
import wavebench
assert pathlib.Path(wavebench.__file__).resolve().is_relative_to(
    pathlib.Path(sysconfig.get_paths()['purelib']).resolve())
assert wavebench.__version__ == version('wavebench') == sys.argv[1]
""", expected_version)
        # Isolated Python ignores PYTHONUTF8; set UTF-8 explicitly for captured Windows output.
        for entry in ((cli,), (python, "-I", "-X", "utf8", "-m", "wavebench")):
            if run(*entry, "--version").strip() != f"wavebench {expected_version}":
                raise ValueError("CLI version differs from distribution")
            for arguments in (("--help",), ("run", "schema"), ("run", "template", "--list"),
                              ("lock", "status", "TCPIP::192.0.2.10::INSTR")):
                run(*entry, *arguments)
        run(cli, "run", "template", "source-scope-sine", "--output", "example.toml")
        (root / "offline.toml").write_text('''[connection]
backend = "lan"
resource = "TCPIP::192.0.2.10::INSTR"
[scope]
driver = "rtm2032"
[source]
driver = "dg4202"
resource = "TCPIP::192.0.2.11::INSTR"
[safety_limits]
max_source_vpp = 1.0
''', encoding="utf-8")
        run(cli, "run", "check", "--plan", "example.toml", "--config", "offline.toml")
        print(f"isolated wheel smoke passed: {wheel.name} ({sys.version.split()[0]}, {sys.platform})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", required=True, type=Path)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    wheels = list(args.artifacts.glob("*.whl"))
    if len(wheels) != 1:
        parser.error("expected exactly one wheel")
    smoke_wheel(wheels[0], args.version)
