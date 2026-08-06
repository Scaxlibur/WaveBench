from __future__ import annotations

from html import escape
import json
from math import isfinite, log10
from pathlib import Path
from typing import Any


PLOTLY_ASSET_RELATIVE = Path("report-assets") / "plotly.min.js"


def write_plotly_asset(output_dir: Path) -> Path | None:
    """Write Plotly.js beside the report when the optional dependency is installed."""
    try:
        from plotly.offline import get_plotlyjs
    except (ImportError, ModuleNotFoundError):
        return None
    path = output_dir / PLOTLY_ASSET_RELATIVE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(get_plotlyjs(), encoding="utf-8")
    return path


def build_surface_payload(
    rows: list[dict[str, str]], *, plot_id: str, response_label: str
) -> dict[str, Any] | None:
    frequencies = sorted(
        {
            value
            for row in rows
            if (value := _finite_float(row.get("requested_frequency_hz"))) is not None and value > 0
        }
    )
    amplitudes = sorted(
        {
            value
            for row in rows
            if (value := _finite_float(row.get("requested_vpp"))) is not None and value > 0
        }
    )
    if len(frequencies) < 2 or len(amplitudes) < 2:
        return None

    lookup = {
        (amplitude, frequency): row
        for row in rows
        if (amplitude := _finite_float(row.get("requested_vpp"))) is not None
        and amplitude > 0
        and (frequency := _finite_float(row.get("requested_frequency_hz"))) is not None
        and frequency > 0
    }
    modes_to_basis_unit = {
        "raw_db": ("raw", "db"),
        "raw_linear": ("raw", "linear"),
        "corrected_db": ("corrected", "db"),
        "corrected_linear": ("corrected", "linear"),
    }
    modes: dict[str, list[list[float | None]]] = {}
    for mode, (basis, unit) in modes_to_basis_unit.items():
        matrix = [
            [
                _gain_value(lookup.get((amplitude, frequency)), basis=basis, unit=unit)
                for frequency in frequencies
            ]
            for amplitude in amplitudes
        ]
        if any(value is not None for row in matrix for value in row):
            modes[mode] = matrix
    if "raw_db" not in modes and "raw_linear" not in modes:
        return None

    default_mode = next(
        mode
        for mode in ("corrected_db", "corrected_linear", "raw_db", "raw_linear")
        if mode in modes
    )
    points = []
    for amplitude in amplitudes:
        for frequency in frequencies:
            row = lookup.get((amplitude, frequency))
            if row is None:
                continue
            values = {
                mode: _gain_value(row, basis=basis, unit=unit)
                for mode, (basis, unit) in modes_to_basis_unit.items()
            }
            points.append(
                {
                    "frequency_hz": frequency,
                    "x_log10": log10(frequency),
                    "requested_vpp": amplitude,
                    "status": str(row.get("status", "")).lower(),
                    "warnings": str(row.get("warnings", "")),
                    "error": str(row.get("error", "")),
                    "quality_retry_count": _nonnegative_int(row.get("quality_retry_count")),
                    "initial_warnings": str(row.get("initial_warnings", "")),
                    "initial_capture_package": str(row.get("initial_capture_package", "")),
                    "capture_package": str(row.get("capture_package", "")),
                    **values,
                }
            )
    tick_indexes = _sample_indexes(len(frequencies), 8)
    return {
        "plot_id": plot_id,
        "response_label": response_label,
        "frequencies_hz": frequencies,
        "x_log10": [log10(value) for value in frequencies],
        "amplitudes_vpp": amplitudes,
        "modes": modes,
        "default_mode": default_mode,
        "points": points,
        "x_ticks": [log10(frequencies[index]) for index in tick_indexes],
        "x_tick_labels": [_format_frequency(frequencies[index]) for index in tick_indexes],
    }


def render_surface_card(payload: dict[str, Any] | None, *, plotly_url: str | None) -> str:
    if payload is None:
        return ""
    if plotly_url is None:
        return (
            '<article class="card frequency-response-card response-3d-card">'
            '<h3>三维增益曲面 / Interactive 3D gain surface</h3>'
            '<p class="muted">安装 <code>WaveBench[report3d]</code> 后重新生成 HTML，'
            '即可离线旋转二维扫频增益曲面。</p></article>'
        )
    plot_id = escape(str(payload["plot_id"]), quote=True)
    corrected_available = any(mode.startswith("corrected_") for mode in payload["modes"])
    corrected_disabled = "" if corrected_available else " disabled"
    corrected_selected = " selected" if payload["default_mode"].startswith("corrected") else ""
    raw_selected = "" if corrected_selected else " selected"
    linear_selected = " selected" if payload["default_mode"].endswith("linear") else ""
    db_selected = "" if linear_selected else " selected"
    return (
        '<article class="card frequency-response-card response-3d-card">'
        '<h3>三维增益曲面 / Interactive 3D gain surface</h3>'
        '<div class="response-3d-controls">'
        f'<label>数据 / Data <select data-plot-target="{plot_id}" data-role="basis">'
        f'<option value="raw"{raw_selected}>Raw</option>'
        f'<option value="corrected"{corrected_selected}{corrected_disabled}>Corrected</option>'
        '</select></label>'
        f'<label>增益 / Gain <select data-plot-target="{plot_id}" data-role="unit">'
        f'<option value="db"{db_selected}>dB</option>'
        f'<option value="linear"{linear_selected}>V/V</option>'
        '</select></label>'
        f'<button type="button" data-plot-target="{plot_id}" data-role="reset">'
        '重置视角 / Reset view</button></div>'
        f'<div id="{plot_id}" class="response-3d-plot" role="img" '
        'aria-label="Interactive three-dimensional frequency response"></div>'
        f'<script type="application/json" class="response-3d-data" data-plot-target="{plot_id}">'
        f'{_json_for_html(payload)}</script>'
        '<p class="muted">曲面只连接相邻实测节点；圆点代表真实采样，failed 节点留洞且不外推。</p>'
        '</article>'
    )


def plotly_head_tag(plotly_url: str | None) -> str:
    if plotly_url is None:
        return ""
    return f'<script defer src="{escape(plotly_url, quote=True)}"></script>'


def plotly_initializer(plotly_url: str | None) -> str:
    if plotly_url is None:
        return ""
    return """<script>
window.addEventListener("DOMContentLoaded", () => {
  const modeLabel = {
    raw_db: "Raw gain (dB)",
    raw_linear: "Raw gain (V/V)",
    corrected_db: "Corrected gain (dB)",
    corrected_linear: "Corrected gain (V/V)",
  };
  const escapeHtml = value => String(value).replace(/[&<>\"']/g, character => {
    const replacements = {"&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;"};
    return character === '"' ? "&quot;" : replacements[character];
  });
  const hoverText = (point, mode) => {
    const details = [
      `Frequency: ${escapeHtml(point.frequency_hz)} Hz`,
      `Requested: ${escapeHtml(point.requested_vpp)} Vpp`,
      `${modeLabel[mode]}: ${escapeHtml(point[mode])}`,
      `Status: ${escapeHtml(point.status || "unknown")}`,
    ];
    if (point.quality_retry_count > 0) details.push(
      `Recovered retries: ${escapeHtml(point.quality_retry_count)}`,
      `Initial warnings: ${escapeHtml(point.initial_warnings || "-")}`,
      `Initial capture: ${escapeHtml(point.initial_capture_package || "-")}`,
    );
    if (point.warnings) details.push(`Warnings: ${escapeHtml(point.warnings)}`);
    if (point.error) details.push(`Error: ${escapeHtml(point.error)}`);
    details.push(`Final capture: ${escapeHtml(point.capture_package || "-")}`);
    return details.join("<br>");
  };
  document.querySelectorAll("script.response-3d-data").forEach(node => {
    const payload = JSON.parse(node.textContent);
    const target = document.getElementById(payload.plot_id);
    if (!window.Plotly) {
      target.innerHTML = '<p class="muted">Plotly.js failed to load; static Bode plots remain available.</p>';
      return;
    }
    const controls = document.querySelectorAll(`[data-plot-target="${payload.plot_id}"]`);
    const basis = Array.from(controls).find(control => control.dataset.role === "basis");
    const unit = Array.from(controls).find(control => control.dataset.role === "unit");
    const reset = Array.from(controls).find(control => control.dataset.role === "reset");
    const defaultCamera = () => ({eye: {x: 1.55, y: 1.45, z: 1.05}});
    const draw = () => {
      let mode = `${basis.value}_${unit.value}`;
      if (!(mode in payload.modes)) {
        const rawMode = `raw_${unit.value}`;
        mode = rawMode in payload.modes ? rawMode : payload.default_mode;
        basis.value = mode.startsWith("corrected") ? "corrected" : "raw";
        unit.value = mode.endsWith("linear") ? "linear" : "db";
      }
      const samples = payload.points.filter(point => point.status !== "failed" && Number.isFinite(point[mode]));
      const traces = [{
        type: "surface",
        x: payload.x_log10,
        y: payload.amplitudes_vpp,
        z: payload.modes[mode],
        connectgaps: false,
        colorscale: "Viridis",
        colorbar: {title: modeLabel[mode]},
        hoverinfo: "skip",
        name: "Visual surface",
      }];
      [
        {name: "Measured", color: "#2563eb", symbol: "circle", match: point => point.status !== "warning" && point.quality_retry_count === 0},
        {name: "Recovered", color: "#059669", symbol: "diamond", match: point => point.status !== "warning" && point.quality_retry_count > 0},
        {name: "Warning", color: "#d97706", symbol: "x", match: point => point.status === "warning"},
      ].forEach(style => {
        const group = samples.filter(style.match);
        if (!group.length) return;
        traces.push({
          type: "scatter3d",
          mode: "markers",
          x: group.map(point => point.x_log10),
          y: group.map(point => point.requested_vpp),
          z: group.map(point => point[mode]),
          text: group.map(point => hoverText(point, mode)),
          hovertemplate: "%{text}<extra></extra>",
          marker: {size: 4, color: style.color, symbol: style.symbol, line: {color: "#ffffff", width: 0.5}},
          name: style.name,
        });
      });
      const layout = {
        margin: {l: 0, r: 0, t: 36, b: 0},
        title: {text: modeLabel[mode], font: {size: 14}},
        uirevision: payload.plot_id,
        scene: {
          xaxis: {title: "Frequency (Hz, log)", tickmode: "array", tickvals: payload.x_ticks, ticktext: payload.x_tick_labels},
          yaxis: {title: "Requested Vpp"},
          zaxis: {title: modeLabel[mode]},
          camera: defaultCamera(),
        },
        paper_bgcolor: "#f8fafc",
        plot_bgcolor: "#f8fafc",
        legend: {orientation: "h"},
      };
      Plotly.react(target, traces, layout, {
        responsive: true,
        displaylogo: false,
        scrollZoom: true,
      });
    };
    basis.addEventListener("change", draw);
    unit.addEventListener("change", draw);
    reset.addEventListener("click", () => {
      Plotly.relayout(target, {"scene.camera": defaultCamera()});
    });
    draw();
  });
});
</script>"""


def _gain_value(row: dict[str, str] | None, *, basis: str, unit: str) -> float | None:
    if row is None or str(row.get("status", "")).lower() == "failed":
        return None
    suffix = "_corrected" if basis == "corrected" else ""
    value = _finite_float(row.get(f"gain_{unit}{suffix}"))
    if value is not None:
        return value
    if unit == "db":
        linear = _finite_float(row.get(f"gain_linear{suffix}"))
        return 20.0 * log10(linear) if linear is not None and linear > 0 else None
    gain_db = _finite_float(row.get(f"gain_db{suffix}"))
    return 10.0 ** (gain_db / 20.0) if gain_db is not None else None


def _finite_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) else None


def _nonnegative_int(value: Any) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, result)


def _sample_indexes(count: int, maximum: int) -> list[int]:
    if count <= maximum:
        return list(range(count))
    return sorted({round(index * (count - 1) / (maximum - 1)) for index in range(maximum)})


def _format_frequency(value: float) -> str:
    if value >= 1e6:
        return f"{value / 1e6:g} MHz"
    if value >= 1e3:
        return f"{value / 1e3:g} kHz"
    return f"{value:g} Hz"


def _json_for_html(value: Any) -> str:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )
