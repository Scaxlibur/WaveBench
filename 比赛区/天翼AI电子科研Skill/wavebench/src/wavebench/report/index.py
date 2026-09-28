from __future__ import annotations

import csv
import html
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from wavebench.data.packages import load_run_package
from wavebench.errors import ConfigError
from wavebench.report.path_utils import artifact_url


@dataclass(frozen=True)
class ReportIndexResult:
    output_dir: Path
    manifest_json_path: Path
    manifest_csv_path: Path
    index_html_path: Path
    count: int


def write_report_index(run_dirs: list[str | Path], output_dir: str | Path) -> ReportIndexResult:
    if not run_dirs:
        raise ConfigError('report-index requires at least one run directory')
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    entries = [build_manifest_entry(Path(run_dir)) for run_dir in run_dirs]
    manifest = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'source_runs': [str(Path(run_dir)) for run_dir in run_dirs],
        'count': len(entries),
        'runs': entries,
    }

    manifest_json_path = output / 'manifest.json'
    manifest_csv_path = output / 'manifest.csv'
    index_html_path = output / 'index.html'
    manifest_json_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf-8')
    _write_manifest_csv(entries, manifest_csv_path)
    index_html_path.write_text(render_report_index_html(entries, output), encoding='utf-8')
    return ReportIndexResult(
        output_dir=output,
        manifest_json_path=manifest_json_path,
        manifest_csv_path=manifest_csv_path,
        index_html_path=index_html_path,
        count=len(entries),
    )


def build_manifest_entry(run_dir: str | Path) -> dict[str, Any]:
    package = load_run_package(run_dir)
    run = package.run
    steps = run.get('steps', []) if isinstance(run.get('steps'), list) else []
    restore = run.get('restore', {}) if isinstance(run.get('restore'), dict) else {}

    scope_step = next((step for step in steps if step.get('kind') == 'scope.capture'), None)
    dmm_step = next((step for step in steps if step.get('kind') == 'dmm.read'), None)

    scope_summary = _scope_summary(scope_step)
    dmm_summary = _dmm_summary(dmm_step)
    expect_failures = sum(1 for step in steps if _expect_failed(step.get('artifact', {})))
    expect_fft_failures = sum(1 for step in steps if _expect_fft_failed(step.get('artifact', {})))
    failed_steps = sum(1 for step in steps if step.get('status') == 'failed')

    report_path = package.path / 'report.html'
    restore_channels = restore.get('source_channels') if isinstance(restore.get('source_channels'), list) else None
    if restore_channels is None:
        channel = restore.get('source_channel')
        restore_channels = [channel] if channel is not None else []

    entry: dict[str, Any] = {
        'run_dir': str(package.path),
        'report_path': str(report_path) if report_path.exists() else None,
        'plan_path': str(run.get('plan')) if run.get('plan') is not None else None,
        'experiment_name': _nested(run, 'experiment', 'name'),
        'experiment_label': _nested(run, 'experiment', 'label'),
        'status': run.get('status'),
        'step_count': len(steps),
        'failed_steps': failed_steps,
        'has_dmm': dmm_step is not None,
        'has_scope_capture': scope_step is not None,
        'expect_failures': expect_failures,
        'expect_fft_failures': expect_fft_failures,
        'restore_status': restore.get('status'),
        'restore_source_channels': restore_channels,
        'primary_scope_frequency_hz': scope_summary.get('frequency_estimate_hz'),
        'primary_scope_vpp_v': scope_summary.get('voltage_vpp_v'),
        'primary_dmm_value': dmm_summary.get('value'),
        'primary_dmm_unit': dmm_summary.get('unit'),
        'primary_dmm_function': dmm_summary.get('function'),
        'artifact_paths': {
            'run_json': str(package.run_json_path),
            'summary_csv': str(package.summary_csv_path) if package.summary_csv_path is not None else None,
            'report_html': str(report_path) if report_path.exists() else None,
        },
    }
    return entry


CSV_FIELDS = [
    'run_dir',
    'status',
    'experiment_label',
    'step_count',
    'failed_steps',
    'expect_failures',
    'expect_fft_failures',
    'restore_status',
    'restore_source_channels',
    'primary_scope_frequency_hz',
    'primary_scope_vpp_v',
    'primary_dmm_value',
    'primary_dmm_unit',
    'primary_dmm_function',
    'report_path',
]


def _write_manifest_csv(entries: list[dict[str, Any]], path: Path) -> None:
    with path.open('w', newline='', encoding='utf-8') as file:
        writer = csv.DictWriter(file, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for entry in entries:
            row = {key: entry.get(key) for key in CSV_FIELDS}
            channels = row.get('restore_source_channels')
            if isinstance(channels, list):
                row['restore_source_channels'] = '|'.join(str(channel) for channel in channels)
            writer.writerow(row)


def render_report_index_html(entries: list[dict[str, Any]], output_dir: Path) -> str:
    ok_count = sum(1 for entry in entries if entry.get('status') == 'ok')
    failed_count = sum(1 for entry in entries if entry.get('status') == 'failed')
    has_dmm_count = sum(1 for entry in entries if entry.get('has_dmm'))
    has_scope_count = sum(1 for entry in entries if entry.get('has_scope_capture'))

    rows: list[str] = []
    for entry in entries:
        restore_channels = entry.get('restore_source_channels') or []
        restore_text = '|'.join(str(channel) for channel in restore_channels) if restore_channels else '-'
        dmm_text = '-'
        if entry.get('primary_dmm_value') is not None:
            dmm_text = f"{entry['primary_dmm_value']} {entry.get('primary_dmm_unit') or ''} {entry.get('primary_dmm_function') or ''}".strip()
        report_link = _html_link(entry.get('report_path'), output_dir, 'report.html')
        run_json_link = _html_link(_artifact(entry, 'run_json'), output_dir, 'run.json')
        summary_link = _html_link(_artifact(entry, 'summary_csv'), output_dir, 'summary.csv')
        rows.append(
            '<tr>'
            f"<td>{html.escape(str(entry.get('status') or '-'))}</td>"
            f"<td>{html.escape(str(entry.get('experiment_label') or '-'))}</td>"
            f"<td>{html.escape(str(entry.get('restore_status') or '-'))} / {html.escape(restore_text)}</td>"
            f"<td>{html.escape(_fmt(entry.get('primary_scope_frequency_hz')))}</td>"
            f"<td>{html.escape(_fmt(entry.get('primary_scope_vpp_v')))}</td>"
            f"<td>{html.escape(dmm_text)}</td>"
            f"<td>{entry.get('expect_failures', 0)}</td>"
            f"<td>{entry.get('expect_fft_failures', 0)}</td>"
            f"<td>{report_link} {run_json_link} {summary_link}</td>"
            '</tr>'
        )

    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WaveBench report index</title>
<style>
body {{ font-family: ui-sans-serif, system-ui, sans-serif; margin: 1.5rem; color: #102a43; background: #f8fbff; }}
h1, h2 {{ margin-bottom: 0.5rem; }}
.summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(10rem, 1fr)); gap: 0.75rem; margin: 1rem 0 1.25rem; }}
.card {{ background: white; border: 1px solid #d9e2ec; border-radius: 10px; padding: 0.8rem 0.9rem; }}
.label {{ color: #627d98; font-size: 0.85rem; }}
.value {{ font-size: 1.2rem; font-weight: 700; margin-top: 0.15rem; }}
table {{ width: 100%; border-collapse: collapse; background: white; border: 1px solid #d9e2ec; border-radius: 10px; overflow: hidden; }}
th, td {{ padding: 0.55rem 0.65rem; border-bottom: 1px solid #e6edf3; text-align: left; vertical-align: top; }}
th {{ background: #f0f4f8; }}
code {{ background: #f0f4f8; padding: 0.1rem 0.25rem; border-radius: 4px; }}
a {{ color: #0b69a3; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
.muted {{ color: #627d98; }}
</style>
</head>
<body>
<h1>WaveBench report index / 报告索引</h1>
<p class="muted">Static local summary for a group of runs.</p>
<div class="summary">
  <div class="card"><div class="label">runs</div><div class="value">{len(entries)}</div></div>
  <div class="card"><div class="label">ok</div><div class="value">{ok_count}</div></div>
  <div class="card"><div class="label">failed</div><div class="value">{failed_count}</div></div>
  <div class="card"><div class="label">has DMM</div><div class="value">{has_dmm_count}</div></div>
  <div class="card"><div class="label">has scope capture</div><div class="value">{has_scope_count}</div></div>
</div>
<table>
<thead>
<tr>
<th>status</th>
<th>label</th>
<th>restore</th>
<th>scope freq (Hz)</th>
<th>scope Vpp (V)</th>
<th>DMM</th>
<th>expect fail</th>
<th>fft fail</th>
<th>links</th>
</tr>
</thead>
<tbody>
{''.join(rows)}
</tbody>
</table>
</body>
</html>
'''


def _artifact(entry: dict[str, Any], key: str) -> str | None:
    artifacts = entry.get('artifact_paths', {})
    if isinstance(artifacts, dict):
        value = artifacts.get(key)
        return str(value) if value is not None else None
    return None


def _html_link(path_text: str | None, output_dir: Path, label: str) -> str:
    if not path_text:
        return f'<span class="muted">{html.escape(label)}: missing</span>'
    rel = artifact_url(Path(path_text), output_dir)
    return f'<a href="{html.escape(rel)}">{html.escape(label)}</a>'


def _nested(obj: dict[str, Any], *keys: str) -> Any:
    cur: Any = obj
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _scope_summary(step: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(step, dict):
        return {}
    artifact = step.get('artifact', {})
    capture = artifact.get('capture') if isinstance(artifact, dict) else None
    if isinstance(capture, dict):
        quality = capture.get('quality', {})
        if isinstance(quality, dict):
            summary = quality.get('summary', {})
            if isinstance(summary, dict):
                return summary
    quality = artifact.get('quality', {}) if isinstance(artifact, dict) else {}
    if isinstance(quality, dict):
        summary = quality.get('summary', {})
        if isinstance(summary, dict):
            return summary
    return {}


def _dmm_summary(step: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(step, dict):
        return {}
    artifact = step.get('artifact', {})
    if isinstance(artifact, dict):
        reading = artifact.get('dmm_reading', {})
        if isinstance(reading, dict):
            return reading
    return {}


def _expect_failed(artifact: dict[str, Any]) -> bool:
    expect = artifact.get('expect') if isinstance(artifact, dict) else None
    return isinstance(expect, dict) and expect.get('status') == 'failed'


def _expect_fft_failed(artifact: dict[str, Any]) -> bool:
    expect_fft = artifact.get('expect_fft') if isinstance(artifact, dict) else None
    return isinstance(expect_fft, dict) and expect_fft.get('status') == 'failed'


def _fmt(value: Any) -> str:
    if value is None:
        return '-'
    if isinstance(value, float):
        return f'{value:.6g}'
    return str(value)
