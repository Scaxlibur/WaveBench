"""将一次波形采集保存为可追溯的 JSON/CSV/SVG 单帧工件。"""

from __future__ import annotations

import csv
import html
import json
import time
from pathlib import Path
from typing import Any, Iterable

from .core import analyze_samples


def write_single_frame(samples: Iterable[float], sample_rate_hz: float, output_dir: str | Path,
                       prefix: str = "capture", metadata: dict[str, Any] | None = None) -> dict[str, str]:
    values = [float(value) for value in samples]
    analysis = analyze_samples(values, sample_rate_hz)
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    stem = target / f"{prefix}_{stamp}"
    payload = {"created_at": time.time(), "analysis": analysis, "metadata": metadata or {}, "samples": values}
    json_path = stem.with_suffix(".json")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    csv_path = stem.with_suffix(".csv")
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["index", "time_s", "value"])
        for index, value in enumerate(values):
            writer.writerow([index, index / sample_rate_hz, value])
    svg_path = stem.with_suffix(".svg")
    svg_path.write_text(_waveform_svg(values, analysis, metadata or {}), encoding="utf-8")
    return {"json": str(json_path), "csv": str(csv_path), "svg": str(svg_path)}


def _waveform_svg(values: list[float], analysis: dict[str, Any], metadata: dict[str, Any]) -> str:
    width, height, pad = 1200, 620, 70
    plot_w, plot_h = width - 2 * pad, height - 2 * pad
    lo, hi = min(values), max(values)
    span = hi - lo or 1.0
    step = max(1, len(values) // 1500)
    points = []
    for index in range(0, len(values), step):
        x = pad + plot_w * index / max(1, len(values) - 1)
        y = pad + plot_h * (hi - values[index]) / span
        points.append(f"{x:.2f},{y:.2f}")
    title = html.escape(str(metadata.get("title", "电子科研单帧波形")))
    frequency = analysis.get("frequency_hz")
    freq_text = "未知" if frequency is None else f"{frequency:.6g} Hz"
    labels = (
        f"峰值 {analysis['peak_v']:.6g} V    Vpp {analysis['peak_to_peak_v']:.6g} V    "
        f"RMS {analysis['rms_v']:.6g} V    频率 {freq_text}"
    )
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<rect width="100%" height="100%" fill="#0b1220"/><text x="{pad}" y="32" fill="#fff" font-size="22">{title}</text>
<text x="{pad}" y="56" fill="#9fb3c8" font-size="15">{html.escape(labels)}</text>
<rect x="{pad}" y="{pad + 10}" width="{plot_w}" height="{plot_h - 10}" fill="#111c2e" stroke="#49627e"/>
<polyline fill="none" stroke="#42d3ff" stroke-width="2" points="{' '.join(points)}"/>
<line x1="{pad}" y1="{pad + plot_h / 2}" x2="{pad + plot_w}" y2="{pad + plot_h / 2}" stroke="#38506b" stroke-dasharray="5,5"/>
<text x="{pad}" y="{height - 20}" fill="#9fb3c8" font-size="13">样本数 {len(values)} / 采样率 {analysis['sample_rate_hz']:.6g} Hz</text>
</svg>'''
