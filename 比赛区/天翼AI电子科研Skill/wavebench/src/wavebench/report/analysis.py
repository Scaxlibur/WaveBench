"""Read-only visualisation of persisted pipeline exports."""
from __future__ import annotations

from html import escape
from hashlib import sha256
import json
import csv
import os
from pathlib import Path
from typing import Any

import numpy as np

from wavebench.errors import ConfigError
from wavebench.errors import DataError
from wavebench.data.analysis_resources import AnalysisLimits, AnalysisBudget
from wavebench.data.analysis_io import mapped_npy, read_json_bounded, hash_stream, file_identity, BLOCK_ROWS
from wavebench.services.run_pipeline import _sha256_file, _atomic_write_bytes


COLUMNS = {
    ("time_s", "voltage_v"): (1, "time_s", "V"),
    ("frequency_hz", "real_v", "imaginary_v", "amplitude_v"): (3, "frequency_hz", "V"),
    ("frequency_hz", "psd_v2_per_hz"): (1, "frequency_hz", "V²/Hz"),
}


def artifact_file(root: Path, raw: str) -> Path:
    if not isinstance(raw, str) or not raw or Path(raw).is_absolute() or ".." in Path(raw).parts:
        raise ValueError("artifact path must be relative to its result directory")
    path = (root / raw).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("artifact escapes its result directory or is missing")
    return path


def analysis_entries(root: Path, resource_limits: AnalysisLimits | None = None) -> list[tuple[Path, str, dict[str, Any]]]:
    limits = resource_limits or AnalysisLimits()
    try:
        if (root / "batch.json").is_file():
            from wavebench.services.analysis_batch import _safe_attempt
            batch = read_json_bounded(root / "batch.json", limits)
            limits.check("max_output_files", len(batch["entries"]), "batch report")
            entries = []
            for item in batch["entries"]:
                if item.get("directory"):
                    directory = _safe_attempt(root, item["directory"])
                    if (directory / "analysis.json").is_file():
                        entries.extend(analysis_entries(directory, limits))
            return entries
        if (root / "analysis.json").is_file():
            result = read_json_bounded(root / "analysis.json", limits)
            if result["schema"] != "wavebench.analysis.v1":
                raise ValueError("unsupported analysis schema")
            return [(root, root.name, result["artifact"])]
        run = read_json_bounded(root / "run.json", limits)
        return [(root, f"{root.name}/{step.get('id', step['index'])}", step["artifact"])
                for step in run["steps"] if step["kind"] in {"analysis.pipeline", "analysis.pair"}]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ConfigError(f"cannot read analysis results in {root}: {exc}") from exc


def display_samples(data: np.ndarray, maximum: int = 1200) -> np.ndarray:
    """Preserve extrema within display buckets; never use this data for metrics."""
    if len(data) <= maximum:
        return data
    indices = {0, len(data) - 1}
    edges = np.linspace(0, len(data), (maximum - 2) // 2 + 1, dtype=int)
    for start, stop in zip(edges[:-1], edges[1:]):
        chunk = data[start:stop, 1]
        indices.update((start + int(np.argmin(chunk)), start + int(np.argmax(chunk))))
    return data[sorted(indices)]


def read_curve(path: Path, item: dict, column: int, limits: AnalysisLimits):
    """Two bounded passes preserve exact display buckets, complete validation and fingerprint."""
    columns = item["columns"]
    def consume(blocks, count):
        edges = np.linspace(0, count, 600, dtype=int) if count > 1200 else np.arange(count + 1)
        buckets = {}
        digest = sha256()
        previous, offset = None, 0
        first = last = None
        for block in blocks:
            if block.ndim != 2 or block.shape[1] != len(columns) or not np.all(np.isfinite(block)):
                raise ValueError("invalid export values or shape")
            if not np.all(np.diff(block[:, 0]) > 0) or previous is not None and block[0, 0] <= previous:
                raise ValueError("invalid export axis")
            previous = block[-1, 0]
            curve = np.asarray(block[:, [0, column]], dtype="<f8")
            digest.update(curve.tobytes())
            if first is None:
                first = curve[0].copy()
            last = curve[-1].copy()
            first_bucket = max(0, int(np.searchsorted(edges, offset, side="right"))-1)
            last_bucket = min(len(edges)-2, int(np.searchsorted(edges, offset+len(curve)-1, side="right"))-1)
            for bucket in range(first_bucket, last_bucket+1):
                left, right = max(offset, edges[bucket]), min(offset+len(curve), edges[bucket+1])
                values = curve[left-offset:right-offset]
                if not len(values):
                    continue
                lo, hi = int(np.argmin(values[:, 1])), int(np.argmax(values[:, 1]))
                minimum, maximum = (left+lo, values[lo].copy()), (left+hi, values[hi].copy())
                if bucket not in buckets:
                    buckets[bucket] = [minimum, maximum]
                else:
                    pair = buckets[bucket]
                    if minimum[1][1] < pair[0][1][1]:
                        pair[0] = minimum
                    if maximum[1][1] > pair[1][1][1]:
                        pair[1] = maximum
            offset += len(block)
        if offset != count or count < 2:
            raise ValueError("invalid export row count")
        points = {index: point for pair in buckets.values() for index, point in pair}
        points[0], points[count-1] = first, last
        return np.asarray([points[i] for i in sorted(points)]), digest.hexdigest()

    limits.check("max_working_bytes", BLOCK_ROWS * len(columns) * 128 + 1200 * 128, "report")
    if item["format"] == "npy":
        limits.check("max_output_bytes", path.stat().st_size, "report input")
        if _sha256_file(path) != item["sha256"]:
            raise ValueError("export SHA-256 mismatch")
        with mapped_npy(path, limits, columns=len(columns)) as (data, file):
            if hash_stream(file) != item["sha256"]:
                raise ValueError("export SHA-256 mismatch")
            return consume((data[i:i+BLOCK_ROWS] for i in range(0, len(data), BLOCK_ROWS)), len(data))
    path_before = file_identity(path.stat())
    with path.open("rb") as file:
        before = file_identity(os.fstat(file.fileno()))
        if path_before != file_identity(path.stat()):
            raise ValueError("export changed while being opened")
        limits.check("max_output_bytes", before[2], "report input")
        if hash_stream(file) != item["sha256"]:
            raise ValueError("export SHA-256 mismatch")
        def rows():
            file.seek(0)
            def lines():
                while line := file.readline(4097):
                    if len(line) > 4096:
                        raise ValueError("CSV row exceeds 4096 bytes")
                    yield line.decode("utf-8")
            reader = csv.reader(lines())
            if next(reader, None) != columns:
                raise ValueError("CSV columns do not match manifest")
            yield from reader
        count = 0
        for row in rows():
            count += 1
            limits.check("max_input_samples", count, "report")
            if len(row) != len(columns):
                raise ValueError("invalid CSV columns")
        def blocks():
            block = []
            for row in rows():
                block.append([float(value) for value in row])
                if len(block) == BLOCK_ROWS:
                    yield np.asarray(block)
                    block = []
            if block:
                yield np.asarray(block)
        result = consume(blocks(), count)
        if before != file_identity(os.fstat(file.fileno())) or path_before != file_identity(path.stat()):
            raise ValueError("export changed during report generation")
        return result


def curves_svg(curves: list[tuple[str, np.ndarray]], x_label: str, units: str,
               markers: dict[str, list[dict]] | None = None) -> str:
    width, height, pad = 900, 300, 55
    xmin = min(float(data[0, 0]) for _, data in curves)
    xmax = max(float(data[-1, 0]) for _, data in curves)
    ymin = min(float(np.min(data[:, 1])) for _, data in curves)
    ymax = max(float(np.max(data[:, 1])) for _, data in curves)
    if ymax == ymin:
        margin = max(abs(ymin) * .05, 1e-12)
        ymin, ymax = ymin - margin, ymax + margin
    colors = ("#2563eb", "#dc2626", "#059669", "#9333ea", "#d97706")
    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{escape(x_label)} / {escape(units)}">',
             '<rect width="900" height="300" fill="#f8fafc"/>']
    for fraction in np.linspace(0, 1, 5):
        x, y = pad + fraction * (width - 2 * pad), height - pad - fraction * (height - 2 * pad)
        parts.append(f'<text x="{x:.1f}" y="270" font-size="11" text-anchor="middle">{xmin + fraction * (xmax-xmin):.5g}</text>')
        parts.append(f'<text x="50" y="{y:.1f}" font-size="11" text-anchor="end">{ymin + fraction * (ymax-ymin):.5g}</text>')
    for index, (label, data) in enumerate(curves):
        sampled = display_samples(data)
        points = " ".join(f"{pad+(x-xmin)/(xmax-xmin)*(width-2*pad):.2f},{height-pad-(y-ymin)/(ymax-ymin)*(height-2*pad):.2f}"
                          for x, y in sampled)
        parts.append(f'<polyline points="{points}" fill="none" stroke="{colors[index % len(colors)]}" stroke-width="1.5"><title>{escape(label)}</title></polyline>')
        for peak in (markers or {}).get(label, []):
            px = pad + (peak["position"] - xmin) / (xmax - xmin) * (width - 2 * pad)
            py = height - pad - (peak["value"] - ymin) / (ymax - ymin) * (height - 2 * pad)
            parts.append(f'<circle class="peak-marker" cx="{px:.2f}" cy="{py:.2f}" r="3" fill="{colors[index % len(colors)]}"><title>{peak["position"]:.6g}: {peak["value"]:.6g}</title></circle>')
    parts.append(f'<text x="450" y="294" text-anchor="middle">{escape(x_label)}</text><text x="55" y="20">{escape(units)}</text></svg>')
    parts.append("<ul>" + "".join(f'<li style="color:{colors[i % len(colors)]}">{escape(label)}</li>' for i, (label, _) in enumerate(curves)) + "</ul>")
    return "".join(parts)


def render_analysis_sections(entries: list[tuple[Path, str, dict[str, Any]]], *, details: bool = True,
                             resource_limits: AnalysisLimits | None = None) -> str:
    limits = resource_limits or AnalysisLimits()
    curve_count = 0
    read_bytes = 0
    peak_rows = 0
    groups: dict[tuple, list[tuple[str, np.ndarray]]] = {}
    sections: list[str] = []
    markers: dict[str, list[dict]] = {}
    for root, label, artifact in entries:
        try:
            pipeline = artifact["analysis_pipeline"]
            manifest = read_json_bounded(artifact_file(root, pipeline["manifest"]), limits)
            limits.check("max_operations", len(manifest.get("operations", [])), "report")
            limits.check("max_output_files", len(manifest.get("exports", [])), "report")
            if details:
                sections.append(f"<h3>{escape(label)}</h3><pre>{escape(json.dumps(artifact, indent=2, ensure_ascii=False))}</pre>")
            sections.append(f'<p>{escape(label)}: sampling={escape(json.dumps(manifest.get("sampling")))}</p>')
            execution = manifest.get("execution")
            if execution:
                sections.append('<p>执行监督 / Execution supervision: '
                    + escape(str(execution.get('reason') or manifest.get('status')))
                    + '; timeout_s=' + escape(str(execution.get('timeout_s')))
                    + '; memory_backend=' + escape(str(execution.get('memory_backend')))
                    + '; forced=' + escape(str(execution.get('forced'))) + '</p>')
            for stage in manifest.get("stages", []):
                measurement = stage.get("measurement", {})
                if measurement.get("schema") == "wavebench.spectral_quality.v1":
                    sections.append('<details><summary>PSD 积分质量估计 / Spectral quality</summary><pre>'
                                    + escape(json.dumps(measurement, ensure_ascii=False, indent=2)) + '</pre></details>')
            source = manifest["source"]
            peak_sets = {}
            if manifest.get("schema") == "wavebench.analysis_pair.v1":
                curve_count += 3 * sum(item.get("format") == "npy" for item in manifest.get("exports", []))
                limits.check("max_report_curves", curve_count, "pair report")
                sections.append(render_pair_curves(root, manifest, limits))
                continue
            for peak in manifest.get("peaks", []):
                try:
                    peak_file = artifact_file(root, peak["json"])
                    if _sha256_file(peak_file) != peak["json_sha256"]:
                        raise ValueError("peak table SHA-256 mismatch")
                    detected = read_json_bounded(peak_file, limits)
                    rows = detected["peaks"]
                    peak_rows += len(rows)
                    limits.check("max_peak_candidates", peak_rows, "report peaks")
                    limits.check("max_working_bytes", peak_rows * 1024, "report peak markers")
                    if not isinstance(rows, list) or any(not isinstance(row, dict) or not all(isinstance(row.get(key), (int, float)) and np.isfinite(row[key]) for key in ("position", "value")) for row in rows):
                        raise ValueError("invalid peak table")
                    peak_sets.setdefault(detected["signal_sha256"], []).extend(rows)
                    sections.append(f'<p>{escape(label)}: {escape(peak["name"])} peaks={escape(str(peak["count"]))}, retained={escape(str(peak["retained_count"]))}</p>')
                except (OSError, ValueError, TypeError, KeyError, DataError) as exc:
                    sections.append(f'<p class="warning">Peak table unavailable: {escape(str(exc))}</p>')
            seen: set[tuple] = set()
            for item in sorted(manifest["exports"], key=lambda x: x.get("format") != "npy"):
                try:
                    columns = tuple(item["columns"])
                    column, x_label, units = COLUMNS[columns]
                    identity = (item["name"], columns)
                    if identity in seen:
                        continue
                    path = artifact_file(root, item["path"])
                    read_bytes += path.stat().st_size
                    limits.check("max_output_bytes", read_bytes, "report input total")
                    limits.check("max_report_curves", curve_count + 1, "report")
                    limits.check("max_working_bytes", peak_rows * 1024 + (curve_count+1) * 1200 * 128 + BLOCK_ROWS * len(columns) * 128,
                                 "report retained curves")
                    curve, fingerprint = read_curve(path, item, column, limits)
                    curve_count += 1
                    seen.add(identity)
                    key = (source.get("npy_sha256") or label, source.get("channel"), columns)
                    curve_label = f"{label}: {item['name']}"
                    groups.setdefault(key, []).append((curve_label, curve))
                    markers[curve_label] = peak_sets.get(fingerprint, [])
                except (OSError, ValueError, TypeError, KeyError, DataError) as exc:
                    sections.append(f'<p class="warning">{escape(label)}: curve unavailable: {escape(str(exc))}</p>')
        except (OSError, ValueError, TypeError, KeyError, DataError) as exc:
            sections.append(f'<p class="warning">{escape(label)}: analysis unavailable: {escape(str(exc))}</p>')
    for (_, _, columns), curves in groups.items():
        _, x_label, units = COLUMNS[columns]
        sections.append(curves_svg(curves, x_label, units, markers))
    if entries and not groups:
        sections.append("<p>No usable curve exports / 没有可用的曲线导出</p>")
    return "".join(sections)


def write_analysis_report(paths: list[Path], output: Path, *, resource_limits: AnalysisLimits | None = None) -> Path:
    limits = resource_limits or AnalysisLimits()
    limits.check("max_output_files", len(paths), "report inputs")
    metadata_bytes = 0
    for root in paths:
        document = root / ("batch.json" if (root / "batch.json").is_file() else
                           "analysis.json" if (root / "analysis.json").is_file() else "run.json")
        try:
            metadata_bytes += document.stat().st_size
        except OSError as exc:
            raise ConfigError(f"cannot read analysis result: {document}") from exc
    limits.check("max_working_bytes", metadata_bytes * 32 + 65536, "report result metadata")
    entries = [entry for root in paths for entry in analysis_entries(root.resolve(), limits)]
    output = output.resolve()
    if output.exists():
        raise ConfigError("analysis report output must be a new file")
    if any((parent / "metadata.json").is_file() for parent in output.parents):
        raise ConfigError("analysis report must not modify a source capture package")
    for root, _, artifact in entries:
        try:
            manifest = read_json_bounded(artifact_file(root, artifact["analysis_pipeline"]["manifest"]), limits)
            package = Path(manifest["source"]["package"])
            package = package if package.is_absolute() else root / package
            if output.is_relative_to(package.resolve()):
                raise ConfigError("analysis report must not modify a source capture package")
        except (OSError, ValueError, KeyError, TypeError, DataError):
            pass  # Broken manifests are displayed as per-entry errors below.
    html = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            '<title>Signal processing</title><style>body{max-width:1100px;margin:2em auto;font:16px sans-serif}svg{width:100%}pre{white-space:pre-wrap;overflow-wrap:anywhere}.warning{color:#b45309}</style>'
            '<h1>信号处理 / Signal processing</h1>' + render_analysis_sections(entries, resource_limits=limits) + '</html>')
    output.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_bytes(output, html.encode("utf-8"), budget=AnalysisBudget(limits))
    return output


def render_pair_curves(root, manifest, limits):
    """Validate all pair bins while retaining bounded per-bucket extrema and gap flags."""
    from wavebench.data.pair_analysis import PAIR_COLUMNS
    exports = [item for item in manifest.get('exports', []) if item.get('format') == 'npy']
    sections = ['<h3>双通道分析 / Pair analysis</h3><p>同步证据 / Synchronization: '
                + escape(str(manifest.get('source', {}).get('synchronization', {}).get('kind', 'unavailable')))
                + ' · measurement-chain delay; no deskew calibration</p>']
    used = 0
    for item in exports:
        limits.check('max_report_curves', 3 * (used+1), 'pair report')
        path = artifact_file(root, item['path'])
        if item.get('columns') != PAIR_COLUMNS or _sha256_file(path) != item['sha256']:
            raise ValueError('pair export columns or SHA-256 mismatch')
        buckets = [{}, {}, {}]
        counts = {'valid': 0, 'coherent': 0, 'total': 0}
        previous = None
        with mapped_npy(path, limits, columns=8) as (data, _):
            limits.check('max_working_bytes', 4096*128 + 600*1024, 'pair report')
            for start in range(0, len(data), 4096):
                block = np.asarray(data[start:start+4096])
                if (not np.all(np.isfinite(block[:, 0])) or not np.all(np.diff(block[:, 0]) > 0)
                        or previous is not None and block[0, 0] <= previous
                        or not np.all(np.isin(block[:, 6:], [0, 1]))):
                    raise ValueError('invalid pair frequency axis or masks')
                previous = block[-1, 0]
                valid = block[:, 6].astype(bool)
                if (not np.all(np.isfinite(block[valid, 1:6])) or not np.all(np.isnan(block[~valid, 1:6]))
                        or np.any(block[:, 7] > block[:, 6]) or np.any((block[valid, 5] < 0) | (block[valid, 5] > 1))):
                    raise ValueError('invalid pair numeric values or coherence')
                counts['total'] += len(block)
                counts['valid'] += int(valid.sum())
                counts['coherent'] += int(block[:, 7].sum())
                indices = (np.arange(start, start+len(block))*600//len(data)).astype(int)
                for bucket in np.unique(indices):
                    selected = block[indices == bucket]
                    for which, column in enumerate((3, 4, 5)):
                        current = buckets[which].setdefault(int(bucket), {'gap': False, 'min': None, 'max': None})
                        current['gap'] |= bool(np.any(selected[:, 6] == 0))
                        values = selected[selected[:, 6] == 1]
                        if not len(values):
                            continue
                        lo, hi = values[np.argmin(values[:, column])], values[np.argmax(values[:, column])]
                        for key, row in (('min', lo), ('max', hi)):
                            point = (float(row[0]), float(row[column]))
                            old = current[key]
                            if old is None or (point[1] < old[1] if key == 'min' else point[1] > old[1]):
                                current[key] = point
        sections.append('<p>有效 bin / Valid bins: ' + escape(str(counts)) + '</p>')
        for label, table in zip(('Gain (dB)', 'Phase (rad)', 'Coherence'), buckets):
            points = [p for entry in table.values() for p in (entry['min'], entry['max']) if p]
            if not points:
                continue
            xmin, xmax = min(p[0] for p in points), max(p[0] for p in points)
            ymin, ymax = min(p[1] for p in points), max(p[1] for p in points)
            commands, connected = [], False
            for _, entry in sorted(table.items()):
                if entry['gap'] or entry['min'] is None:
                    connected = False
                    continue
                for x, y in sorted({entry['min'], entry['max']}):
                    px = 50 + 800*(x-xmin)/(xmax-xmin or 1)
                    py = 250 - 210*(y-ymin)/(ymax-ymin or 1)
                    commands.append(f"{'L' if connected else 'M'}{px:.2f},{py:.2f}")
                    connected = True
            sections.append(f'<h4>{label}</h4><svg viewBox="0 0 900 290" role="img" aria-label="{label}">'
                f'<path d="{" ".join(commands)}" fill="none" stroke="#2563eb"/>'
                f'<text x="50" y="280">{xmin:.5g}–{xmax:.5g} Hz; {ymin:.5g}–{ymax:.5g}</text></svg>')
        used += 1
    if not exports:
        sections.append('<p>No NPY pair curves / 无 NPY 双通道曲线；CSV 可从导出链接下载</p>')
    return ''.join(sections)
