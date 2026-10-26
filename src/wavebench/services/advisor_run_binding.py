"""Read-only, bounded run snapshots for advisor request preparation and revalidation.

The caller is a trusted Core task input builder, not an advisor plugin. A binding
is evidence, not consent: this module neither loads grants nor executes advisors.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from hashlib import sha256
import json
import math
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import stat
import subprocess
import sys
import time
from typing import Any, Iterable

from wavebench.errors import DataError


BINDING_SCHEMA = "wavebench.advisor.run_binding.v1"
MAX_SOURCES = 32
MAX_SOURCE_BYTES = 16 * 1024**2
_CHUNK_BYTES = 64 * 1024


class RunBindingError(DataError):
    """Safe evidence for a failed read or a changed input snapshot."""

    def __init__(
        self, reason: str, *, code: str = "run_binding_unverifiable",
        paths: tuple[str, ...] = (), expected_digest: str | None = None,
        actual_digest: str | None = None,
    ) -> None:
        super().__init__(f"advisor run binding failed / advisor run 绑定失败: {reason}")
        self.code = code
        self.reason = reason
        self.paths = paths
        self.expected_digest = expected_digest
        self.actual_digest = actual_digest

    def to_envelope(self, *, operation=None, details=None, cause=None):
        evidence = {
            **(details or {}), "reason": self.reason, "paths": list(self.paths),
            "expected_digest": self.expected_digest, "actual_digest": self.actual_digest,
        }
        # Raw filesystem exceptions may disclose unrelated absolute paths.
        return super().to_envelope(operation=operation, details=evidence)


@dataclass(frozen=True)
class BindingSource:
    """One Core-selected, run-relative input; absence is opt-in."""

    path: str
    optional: bool = False


@dataclass(frozen=True)
class _SourceSnapshot:
    spec: BindingSource
    content: bytes | None = field(repr=False)

    def record(self) -> dict[str, str]:
        if self.content is None:
            return {"path": self.spec.path, "state": "missing"}
        return {
            "path": self.spec.path, "state": "present",
            "sha256": sha256(self.content).hexdigest(),
        }


@dataclass(frozen=True)
class RunBindingSnapshot:
    """Immutable source bytes and local identity, built by capture_run_binding()."""

    run_root: str
    task_id: str
    task_version: str
    _sources: tuple[_SourceSnapshot, ...] = field(repr=False)
    _requested_root: str = field(repr=False, compare=False)
    elapsed_s: float = field(compare=False)

    @property
    def bytes_read(self) -> int:
        return sum(len(source.content) for source in self._sources if source.content is not None)

    def as_dict(self) -> dict[str, Any]:
        """A fresh JSON-ready binding; raw bytes and timing never enter its hash."""
        return {
            "schema": BINDING_SCHEMA, "run_root": self.run_root,
            "task_id": self.task_id, "task_version": self.task_version,
            "sources": [source.record() for source in self._sources],
        }

    @property
    def binding_sha256(self) -> str:
        encoded = json.dumps(
            self.as_dict(), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
        return sha256(encoded).hexdigest()

    def read_bytes(self, path: str) -> bytes | None:
        """Return the already-hashed bytes, never reopen the filesystem."""
        for source in self._sources:
            if source.spec.path == path:
                return source.content
        raise KeyError(path)

    def read_json(self, path: str) -> Any:
        """Parse the same snapshot strictly; optional absence is returned as None."""
        raw = self.read_bytes(path)
        return None if raw is None else _parse_json(raw, path)


def capture_run_binding(
    run_dir: str | Path, *, task_id: str, task_version: str,
    sources: Iterable[BindingSource] = (), timeout_s: float = 30.0,
) -> RunBindingSnapshot:
    """Capture run.json plus a bounded explicit source list without writing files.

    Reads run in a short-lived subprocess so stalled filesystem I/O can be
    terminated. timeout_s is the remaining stage budget; no retries are made.
    The parent retains at most 16 MiB of source bytes (IPC/JSON overhead is extra).
    """
    started = time.monotonic()
    for value in (task_id, task_version):
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise RunBindingError("invalid_task_identity")
    if (isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float))
            or not math.isfinite(timeout_s) or timeout_s <= 0):
        raise RunBindingError("invalid_timeout")
    specs = [BindingSource("run.json")]
    seen = {"run.json"}
    for spec in sources:
        if len(specs) >= MAX_SOURCES:
            raise RunBindingError("source_count_limit")
        if not isinstance(spec, BindingSource) or type(spec.optional) is not bool:
            raise RunBindingError("invalid_source")
        _validate_source_path(spec.path)
        if spec.path in seen:
            raise RunBindingError("duplicate_source", paths=(spec.path,))
        specs.append(spec)
        seen.add(spec.path)
    specs.sort(key=lambda item: item.path)
    try:
        # Keep the original absolute spelling so revalidation catches root-link retargeting.
        requested_root = str(Path(run_dir).expanduser().absolute())
        request = json.dumps({
            "run_dir": requested_root,
            "sources": [{"path": s.path, "optional": s.optional} for s in specs],
        })
        remaining = timeout_s - (time.monotonic() - started)
        if remaining <= 0:
            raise RunBindingError("deadline_exceeded", code="timeout")
        completed = subprocess.run(
            [sys.executable, "-m", "wavebench.services.advisor_run_binding"],
            input=request.encode("utf-8"), capture_output=True, timeout=remaining, check=False,
        )
    except subprocess.TimeoutExpired:
        raise RunBindingError("deadline_exceeded", code="timeout") from None
    except KeyboardInterrupt:
        raise RunBindingError("cancelled", code="cancelled") from None
    except (OSError, ValueError, RuntimeError):
        raise RunBindingError("snapshot_worker_unavailable") from None
    if completed.returncode != 0:
        raise RunBindingError("snapshot_worker_failed")
    try:
        result = json.loads(completed.stdout)
        if "error" in result:
            raise RunBindingError(result["error"], paths=tuple(result["paths"]))
        snapshot = RunBindingSnapshot(
            run_root=result["run_root"], task_id=task_id, task_version=task_version,
            _sources=tuple(
                _SourceSnapshot(spec, None if raw is None else base64.b64decode(raw, validate=True))
                for spec, raw in zip(specs, result["contents"], strict=True)
            ),
            _requested_root=requested_root, elapsed_s=time.monotonic() - started,
        )
    except (ValueError, TypeError, KeyError):
        raise RunBindingError("invalid_worker_response") from None
    if time.monotonic() - started >= timeout_s:
        raise RunBindingError("deadline_exceeded", code="timeout")
    return snapshot


def verify_run_binding(
    expected: RunBindingSnapshot, *, sources: Iterable[BindingSource] | None = None,
    timeout_s: float = 30.0,
) -> RunBindingSnapshot:
    """Re-read inputs, optionally with a fresh Core manifest; fail closed on changes."""
    try:
        actual = capture_run_binding(
            expected._requested_root, task_id=expected.task_id, task_version=expected.task_version,
            sources=(s.spec for s in expected._sources if s.spec.path != "run.json")
            if sources is None else sources,
            timeout_s=timeout_s,
        )
    except RunBindingError as exc:
        raise RunBindingError(
            exc.reason, code=exc.code, paths=exc.paths,
            expected_digest=expected.binding_sha256,
        ) from None
    expected_digest = expected.binding_sha256
    actual_digest = actual.binding_sha256
    if actual_digest != expected_digest:
        old = {s.spec.path: s.record() for s in expected._sources}
        new = {s.spec.path: s.record() for s in actual._sources}
        raise RunBindingError(
            "source_snapshot_changed", code="run_binding_changed",
            paths=tuple(path for path in sorted(old.keys() | new.keys()) if old.get(path) != new.get(path)),
            expected_digest=expected_digest, actual_digest=actual_digest,
        )
    return actual


def _validate_source_path(raw: str) -> None:
    if (not isinstance(raw, str) or not raw or "\\" in raw or ":" in raw or "\x00" in raw
            or raw.startswith("/") or any(p in {"", ".", ".."} for p in raw.split("/"))
            or PureWindowsPath(raw).is_reserved()):
        raise RunBindingError("invalid_source_path")
    if PurePosixPath(raw).parts[0].casefold() == "decisions":
        raise RunBindingError("audit_source_forbidden", paths=(raw,))


def _parse_json(raw: bytes, path: str) -> Any:
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("non-finite number")
        return number

    def invalid_constant(value):
        raise ValueError("non-finite number")

    try:
        return json.loads(
            raw.decode("utf-8"), object_pairs_hook=object_pairs,
            parse_float=finite_float, parse_constant=invalid_constant,
        )
    except (UnicodeError, ValueError, RecursionError):
        raise RunBindingError("invalid_json", paths=(path,)) from None


def _identity(value: os.stat_result) -> tuple[int, ...]:
    # As in analysis_io: compare stat with stat and fstat with fstat on Windows.
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


def _read_source(root: Path, spec: BindingSource, remaining: int) -> bytes | None:
    original = root / spec.path
    try:
        resolved = original.resolve()
        relative = resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        raise RunBindingError("source_path_unavailable", paths=(spec.path,)) from None
    if relative.parts and relative.parts[0].casefold() == "decisions":
        raise RunBindingError("audit_source_forbidden", paths=(spec.path,))
    try:
        before_path = resolved.stat()
    except FileNotFoundError:
        if spec.optional:
            return None
        raise RunBindingError("source_missing", paths=(spec.path,)) from None
    if not stat.S_ISREG(before_path.st_mode):
        raise RunBindingError("source_not_regular", paths=(spec.path,))
    if before_path.st_size > remaining:
        raise RunBindingError("source_bytes_limit", paths=(spec.path,))
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(resolved, flags), "rb") as handle:
        before_fd = os.fstat(handle.fileno())
        if not stat.S_ISREG(before_fd.st_mode):
            raise RunBindingError("source_not_regular", paths=(spec.path,))
        chunks = []
        size = 0
        while chunk := handle.read(min(_CHUNK_BYTES, remaining - size + 1)):
            size += len(chunk)
            if size > remaining:
                raise RunBindingError("source_bytes_limit", paths=(spec.path,))
            chunks.append(chunk)
        if (_identity(before_fd) != _identity(os.fstat(handle.fileno()))
                or _identity(before_path) != _identity(resolved.stat())
                or original.resolve() != resolved):
            raise RunBindingError("source_changed_during_read", paths=(spec.path,))
    return b"".join(chunks)


def _read_snapshot(run_dir: str, specs: tuple[BindingSource, ...]) -> dict[str, Any]:
    try:
        root = Path(run_dir).resolve(strict=True)
        if not root.is_dir():
            raise RunBindingError("invalid_run_directory")
        contents = []
        total = 0
        for spec in specs:
            try:
                raw = _read_source(root, spec, MAX_SOURCE_BYTES - total)
            except OSError:
                raise RunBindingError("source_unreadable", paths=(spec.path,)) from None
            if raw is not None:
                total += len(raw)
            if spec.path == "run.json" and not isinstance(_parse_json(raw, spec.path), dict):
                raise RunBindingError("run_must_be_object", paths=(spec.path,))
            contents.append(None if raw is None else base64.b64encode(raw).decode("ascii"))
        if Path(run_dir).resolve(strict=True) != root:
            raise RunBindingError("run_root_changed_during_read")
        return {"run_root": str(root), "contents": contents}
    except (OSError, ValueError, RuntimeError):
        raise RunBindingError("run_directory_unavailable") from None


def _worker_main() -> None:
    request = json.load(sys.stdin)
    try:
        result = _read_snapshot(
            request["run_dir"], tuple(BindingSource(**s) for s in request["sources"]),
        )
    except RunBindingError as exc:
        result = {"error": exc.reason, "paths": list(exc.paths)}
    sys.stdout.write(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":  # private subprocess entry point, not an advisor CLI
    _worker_main()
