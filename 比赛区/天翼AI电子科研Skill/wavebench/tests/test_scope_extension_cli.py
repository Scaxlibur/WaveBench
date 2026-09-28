from __future__ import annotations

import io
import json
from pathlib import Path
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import Mock, patch
import zlib

import numpy as np

from wavebench.cli import main
from wavebench.errors import DataError
from wavebench.instruments.scope_extensions import (
    ScopeScreenshot,
    ScopeScreenshotRequest,
)
from wavebench.transport.contracts import BinaryResponseFraming


def _png(width: int = 2, height: int = 3) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        crc = zlib.crc32(kind + payload) & 0xFFFFFFFF
        return len(payload).to_bytes(4, "big") + kind + payload + crc.to_bytes(4, "big")

    ihdr = width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x02\x00\x00\x00"
    rows = b"".join(b"\x00" + b"\x00" * (width * 3) for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


class _Result:
    def __init__(self, value: object, payload: dict[str, object]) -> None:
        self.value = value
        self.payload = payload

    def as_dict(self) -> dict[str, object]:
        return dict(self.payload)


def test_scope_screenshot_cli_writes_payload_and_versioned_artifact(tmp_path) -> None:
    request = ScopeScreenshotRequest(menu_mode="exclude", color_mode="color")
    screenshot = ScopeScreenshot(
        data=_png(),
        media_type="image/png",
        width_px=2,
        height_px=3,
        requested=request,
        effective=request,
        framing=BinaryResponseFraming.DEFINITE_BLOCK,
    )
    result = _Result(
        screenshot,
        {
            "schema": "wavebench.scope.result.v1",
            "result": {"payload_bytes": len(screenshot.data)},
            "diagnostics": {"schema": "wavebench.scope.operation.v1"},
        },
    )
    service = Mock()
    service.screenshot_v2.return_value = result
    output = tmp_path / "screen.png"
    artifact = tmp_path / "screen.json"
    stdout = io.StringIO()

    with patch("wavebench.cli._load_service", return_value=service), redirect_stdout(stdout):
        code = main(
            [
                "scope",
                "screenshot",
                "capture",
                "--output",
                str(output),
                "--artifact",
                str(artifact),
                "--menu-mode",
                "exclude",
                "--color-mode",
                "color",
            ]
        )

    assert code == 0
    assert output.read_bytes() == screenshot.data
    persisted = json.loads(artifact.read_text(encoding="utf-8"))
    assert persisted["schema"] == "wavebench.scope.result.v1"
    assert persisted["files"] == {
        "screenshot": "screen.png",
        "artifact": "screen.json",
    }
    assert str(tmp_path) not in artifact.read_text(encoding="utf-8")
    service.screenshot_v2.assert_called_once()
    assert service.screenshot_v2.call_args.args[0] == request


def test_scope_trace_cli_writes_npy_and_forwards_typed_reference(tmp_path) -> None:
    values = np.array([1.0, 2.0], dtype=np.float64)
    result = _Result(
        SimpleNamespace(values=values),
        {
            "schema": "wavebench.scope.result.v1",
            "result": {"integrity": {"points": 2}},
            "diagnostics": {"schema": "wavebench.scope.operation.v1"},
        },
    )
    # The CLI validates the stable result type before writing.
    from wavebench.instruments.scope_extensions import (
        ScopeAxisMetadata,
        ScopeTraceData,
        ScopeTraceMetadata,
        ScopeTraceRef,
    )

    source = ScopeTraceRef("analog", index=1)
    trace = ScopeTraceData(
        ScopeTraceMetadata(
            source=source,
            x_axis=ScopeAxisMetadata("time", "s", 0.0, 1e-6, 2),
            y_unit="v",
            y_semantics="linear",
            value_encoding="real",
            operation="identity",
            fetchable=True,
        ),
        values,
    )
    result.value = trace
    service = Mock()
    service.fetch_trace.return_value = result
    output = tmp_path / "trace.npy"
    artifact = tmp_path / "trace.json"

    with patch("wavebench.cli._load_service", return_value=service):
        code = main(
            [
                "scope",
                "trace",
                "fetch",
                "--kind",
                "analog",
                "--index",
                "1",
                "--points",
                "2",
                "--output",
                str(output),
                "--artifact",
                str(artifact),
            ]
        )

    assert code == 0
    assert np.array_equal(np.load(output, allow_pickle=False), values)
    persisted = json.loads(artifact.read_text(encoding="utf-8"))
    assert persisted["schema"] == "wavebench.scope.result.v1"
    assert persisted["files"] == {"trace": "trace.npy", "artifact": "trace.json"}
    called_source = service.fetch_trace.call_args.args[0]
    assert called_source == source
    assert service.fetch_trace.call_args.kwargs["points"] == 2


def test_scope_acquisition_cli_uses_action_specific_methods() -> None:
    service = Mock()
    service.start_acquisition.return_value = _Result(
        object(),
        {"schema": "wavebench.scope.result.v1", "result": {"phase": "ready"}},
    )

    with patch("wavebench.cli._load_service", return_value=service):
        code = main(
            [
                "scope",
                "acquisition",
                "start",
                "--trigger-mode",
                "normal",
                "--error-policy",
                "disabled",
            ]
        )

    assert code == 0
    request = service.start_acquisition.call_args.args[0]
    assert request.trigger_mode == "normal"
    assert service.start_acquisition.call_args.kwargs["error_check"].policy == "disabled"


def test_scope_screenshot_cli_persists_failure_diagnostics(tmp_path) -> None:
    error = DataError("invalid screenshot payload")
    error.scope_operation_diagnostics = {
        "schema": "wavebench.scope.operation.v1",
        "operation": "scope.screenshot_v2",
        "correlation_id": "corr",
        "session_health_after": "healthy",
    }
    service = Mock()
    service.screenshot_v2.side_effect = error
    output = tmp_path / "screen.png"
    artifact = tmp_path / "screen.json"

    with patch("wavebench.cli._load_service", return_value=service):
        code = main(
            [
                "scope",
                "screenshot",
                "capture",
                "--output",
                str(output),
                "--artifact",
                str(artifact),
            ]
        )

    assert code == error.exit_code
    assert not output.exists()
    persisted = json.loads(artifact.read_text(encoding="utf-8"))
    assert persisted["status"] == "failed"
    assert persisted["diagnostics"]["correlation_id"] == "corr"
    assert persisted["error"]["schema"] == "wavebench.error.v1"


def test_scope_screenshot_cli_persists_local_output_failure(tmp_path) -> None:
    request = ScopeScreenshotRequest(menu_mode="exclude", color_mode="color")
    screenshot = ScopeScreenshot(
        data=_png(),
        media_type="image/png",
        width_px=2,
        height_px=3,
        requested=request,
        effective=request,
        framing=BinaryResponseFraming.DEFINITE_BLOCK,
    )
    result = _Result(
        screenshot,
        {
            "schema": "wavebench.scope.result.v1",
            "result": {"payload_bytes": len(screenshot.data)},
            "diagnostics": {
                "schema": "wavebench.scope.operation.v1",
                "operation": "scope.screenshot_v2",
            },
        },
    )
    service = Mock()
    service.screenshot_v2.return_value = result
    output = tmp_path / "screen.png"
    artifact = tmp_path / "screen.json"
    original_open = Path.open

    def open_with_output_failure(path, mode="r", *args, **kwargs):
        if path == output and mode == "xb":
            raise OSError("simulated output failure")
        return original_open(path, mode, *args, **kwargs)

    with (
        patch("wavebench.cli._load_service", return_value=service),
        patch.object(Path, "open", open_with_output_failure),
    ):
        code = main(
            [
                "scope",
                "screenshot",
                "capture",
                "--output",
                str(output),
                "--artifact",
                str(artifact),
            ]
        )

    assert code == 2
    assert not output.exists()
    persisted = json.loads(artifact.read_text(encoding="utf-8"))
    assert persisted["status"] == "failed"
    assert persisted["diagnostics"]["operation"] == "scope.screenshot_v2"
    assert str(tmp_path) not in artifact.read_text(encoding="utf-8")


def test_scope_screenshot_cli_removes_output_when_artifact_write_fails(tmp_path) -> None:
    request = ScopeScreenshotRequest(menu_mode="exclude", color_mode="color")
    screenshot = ScopeScreenshot(
        data=_png(),
        media_type="image/png",
        width_px=2,
        height_px=3,
        requested=request,
        effective=request,
        framing=BinaryResponseFraming.DEFINITE_BLOCK,
    )
    result = _Result(
        screenshot,
        {
            "schema": "wavebench.scope.result.v1",
            "result": {"payload_bytes": len(screenshot.data)},
            "diagnostics": {
                "schema": "wavebench.scope.operation.v1",
                "operation": "scope.screenshot_v2",
            },
        },
    )
    service = Mock()
    service.screenshot_v2.return_value = result
    output = tmp_path / "screen.png"
    artifact = tmp_path / "screen.json"
    stdout = io.StringIO()
    original_open = Path.open

    def open_with_artifact_failure(path, mode="r", *args, **kwargs):
        if path == artifact and mode == "x":
            raise OSError("simulated artifact failure")
        return original_open(path, mode, *args, **kwargs)

    with (
        patch("wavebench.cli._load_service", return_value=service),
        patch.object(Path, "open", open_with_artifact_failure),
        redirect_stdout(stdout),
    ):
        code = main(
            [
                "--json",
                "scope",
                "screenshot",
                "capture",
                "--output",
                str(output),
                "--artifact",
                str(artifact),
            ]
        )

    assert code == 2
    assert not output.exists()
    assert not artifact.exists()
    error = json.loads(stdout.getvalue())
    assert error["scope_artifact"] == {
        "status": "failed",
        "reason_code": "write_failed",
    }
    assert error["operation_diagnostics"]["operation"] == "scope.screenshot_v2"
