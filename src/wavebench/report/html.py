from __future__ import annotations

from dataclasses import dataclass
from html import escape
import json
import math
import os
from pathlib import Path
from typing import Any

import numpy as np

from wavebench.data.packages import FrequencyResponsePackage, RunPackage
from wavebench.errors import ConfigError


def write_run_report_html(run: RunPackage, output_path: str | Path | None = None) -> Path:
    path = Path(output_path) if output_path is not None else run.path / "report.html"
    path.write_text(render_run_report_html(run, output_dir=path.parent), encoding="utf-8")
    write_run_report_manifest(run, output_dir=path.parent, report_path=path)
    return path


def write_run_report_pdf(run: RunPackage, output_path: str | Path | None = None) -> Path:
    """Render the report as a portable PDF with its visible images and SVGs embedded."""
    path = Path(output_path) if output_path is not None else run.path / "report.pdf"
    try:
        from weasyprint import HTML
    except (ImportError, OSError) as exc:
        raise ConfigError(
            "PDF report export requires the optional PDF dependency; "
            "install WaveBench with `.[pdf]` and the platform rendering libraries"
        ) from exc
    # A full HTML report may contain hundreds of independent capture artifacts.
    # Keep the portable PDF focused on its reviewable result: summary, Bode curves,
    # fit, and point table. The run directory remains the authoritative raw evidence.
    html = render_run_report_html(run, output_dir=path.parent, compact=True)
    try:
        HTML(string=html, base_url=path.parent.resolve().as_uri() + "/").write_pdf(str(path))
    except Exception as exc:  # noqa: BLE001 - surface renderer failures as a user-facing config error
        raise ConfigError(f"PDF report export failed: {type(exc).__name__}: {exc}") from exc
    return path


def write_run_report_manifest(
    run: RunPackage, output_dir: str | Path | None = None, report_path: str | Path | None = None
) -> Path:
    report_output_dir = Path(output_dir) if output_dir is not None else run.path
    manifest_path = report_output_dir / "report-assets" / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = _build_report_manifest(
        run,
        output_dir=report_output_dir,
        report_path=Path(report_path) if report_path is not None else report_output_dir / "report.html",
    )
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest_path


@dataclass(frozen=True)
class ReportScreenshot:
    step_index: str
    package: str
    path: Path
    src: str


@dataclass(frozen=True)
class ReportSignalSummary:
    step_index: str
    package: str
    channel: str
    samples: str
    frequency_hz: str
    vpp_v: str
    rms_v: str
    mean_v: str
    duty_cycle: str
    rise_time_s: str
    fall_time_s: str
    warnings: str


@dataclass(frozen=True)
class ReportSummary:
    status: str
    experiment_label: str
    total_steps: int
    failed_steps: int
    capture_count: int
    warning_count: int
    failed_expect_count: int
    screenshot_count: int
    restore_status: str
    primary_frequency: str
    primary_vpp: str


@dataclass(frozen=True)
class ReportExpectationRow:
    step_index: str
    step_kind: str
    metric: str
    expected: str
    measured: str
    status: str
    details: str


@dataclass(frozen=True)
class ReportDmmReading:
    step_index: str
    step_name: str
    function: str
    value: str
    unit: str
    expected: str
    status: str
    details: str


@dataclass(frozen=True)
class ReportSweepRow:
    step_index: str
    label: str
    status: str
    quality_status: str
    expect_status: str
    fft_status: str
    frequency_hz: str
    vpp_v: str
    fft_peak_hz: str
    fft_peak_amplitude_v: str
    thd_ratio: str


@dataclass(frozen=True)
class ReportWaveformPreview:
    step_index: str
    package: str
    channel: str
    title: str
    svg: str
    warning: str


@dataclass(frozen=True)
class ReportArtifactLink:
    step_index: str
    kind: str
    label: str
    href: str
    status: str


@dataclass(frozen=True)
class ReportCaptureReference:
    step_index: str
    package: str
    metadata: str


@dataclass(frozen=True)
class ReportEvidenceSummary:
    source_step_count: int
    scope_capture_count: int
    dmm_reading_count: int
    failed_expectation_count: int
    run_json_available: bool
    summary_csv_available: bool
    capture_package_count: int
    screenshot_count: int
    waveform_preview_count: int


def render_run_report_html(
    run: RunPackage,
    output_dir: str | Path | None = None,
    *,
    compact: bool = False,
) -> str:
    experiment = run.run.get("experiment", {}) if isinstance(run.run.get("experiment"), dict) else {}
    restore = run.run.get("restore", {}) if isinstance(run.run.get("restore"), dict) else {}
    error = run.run.get("error", {}) if isinstance(run.run.get("error"), dict) else {}
    report_output_dir = Path(output_dir) if output_dir is not None else run.path
    screenshots = [] if compact else _collect_screenshots(run, report_output_dir)
    signals = [] if compact else _collect_signal_summaries(run)
    expectations = [] if compact else _collect_expectation_rows(run)
    dmm_readings = [] if compact else _collect_dmm_readings(run)
    sweep_rows = [] if compact else _collect_sweep_rows(run)
    waveform_previews = [] if compact else _collect_waveform_previews(run)
    evidence = _build_evidence_summary(run, expectations, dmm_readings, screenshots, waveform_previews)
    artifact_links = [] if compact else _collect_artifact_links(run, report_output_dir, screenshots)
    summary = _build_report_summary(run, screenshots, signals)
    screenshots_by_step = {item.step_index: item for item in screenshots}
    evidence_timeline_block = "" if compact else _evidence_timeline_block(run, screenshots_by_step)
    rows = "\n".join(_step_row(step, screenshots_by_step.get(str(step.get("index", "")))) for step in run.steps)
    if not rows:
        rows = '<tr><td colspan="9">没有记录步骤 / No steps recorded.</td></tr>'
    screenshots_block = "" if compact else _screenshots_block(screenshots)
    acceptance_block = "" if compact else _acceptance_block(expectations)
    expectations_block = "" if compact else _expectations_block(expectations)
    dmm_block = "" if compact else _dmm_readings_block(dmm_readings)
    sweep_block = "" if compact else _sweep_summary_block(sweep_rows)
    frequency_response_block = _frequency_response_block(run, include_table=not compact)
    artifact_links_block = "" if compact else _artifact_links_block(artifact_links)
    signals_block = "" if compact else _signals_block(signals)
    waveform_previews_block = "" if compact else _waveform_previews_block(waveform_previews)
    evidence_summary_block = "" if compact else _evidence_summary_block(evidence)
    summary_note = "present" if run.summary_csv_path is not None else "missing"
    compact_note = (
        '<p class="muted">PDF 精简为结果摘要、Bode 曲线与拟合；逐点 CSV 和完整原始证据保留在 run 目录。</p>'
        if compact
        else ""
    )
    body_class = "pdf-compact" if compact else ""
    error_block = ""
    if error:
        error_block = f"<h2>运行错误 / Run error</h2><pre>{escape(str(error))}</pre>"
    restore_block = ""
    if restore:
        restore_block = f"<p><b>恢复 / Restore:</b> {escape(str(restore.get('status', 'unknown')))}</p>"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>WaveBench 运行报告 / Run report - {escape(str(experiment.get('label', run.path.name)))}</title>
<style>
body {{ --bg: #f6f7f9; --surface: #ffffff; --text: #1f2933; --muted: #667085; --line: #d9e2ec; --brand: #2563eb; --ok-bg: #e3f9e5; --ok: #0b6b3a; --failed-bg: #ffe3e3; --failed: #a61b1b; --warning-bg: #fff3c4; --warning: #915930; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans CJK SC", "Microsoft YaHei", sans-serif; line-height: 1.5; margin: 0; color: var(--text); background: var(--bg); }}
main {{ max-width: 1180px; margin: 0 auto; padding: 2rem 1rem 3rem; }}
h1, h2 {{ color: #102a43; letter-spacing: -0.02em; }}
h1 {{ margin-bottom: 0.25rem; font-size: clamp(1.75rem, 4vw, 2.7rem); }}
h2 {{ margin-top: 1.75rem; }}
section, article.card, figure.card {{ border: 1px solid var(--line); border-radius: 14px; background: var(--surface); box-shadow: 0 1px 2px rgba(16, 42, 67, 0.04); }}
table {{ border-collapse: collapse; width: 100%; background: var(--surface); }}
th, td {{ border-bottom: 1px solid var(--line); padding: 0.5rem 0.65rem; text-align: left; vertical-align: top; }}
th {{ background: #f0f4f8; color: #334e68; font-weight: 650; }}
tr:last-child td {{ border-bottom: 0; }}
code {{ background: #f0f4f8; padding: 0.1rem 0.3rem; border-radius: 5px; }}
.table {{ overflow-x: auto; border: 1px solid var(--line); border-radius: 12px; background: var(--surface); margin: 0.5rem 0 1.25rem; }}
.compact-table table {{ font-size: 0.92rem; }}
.compact-table th, .compact-table td {{ padding: 0.42rem 0.55rem; }}
.compact-table code, .artifact-link {{ font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace; font-size: 0.86rem; overflow-wrap: anywhere; }}
.artifact-table td:nth-child(3), .artifact-table td:nth-child(4) {{ color: var(--muted); }}
.meta-card {{ padding: 0.85rem 1rem; margin: 1rem 0 1.25rem; }}
.meta-card p {{ margin: 0.35rem 0; }}
.screenshot-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(18rem, 1fr)); gap: 1rem; }}
.screenshot-card {{ padding: 0.85rem; }}
.screenshot-card img {{ display: block; max-width: 100%; height: auto; border: 1px solid var(--line); border-radius: 8px; }}
.screenshot-thumb {{ max-width: 12rem; height: auto; border: 1px solid var(--line); border-radius: 6px; }}
.waveform-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(22rem, 1fr)); gap: 1rem; }}
.waveform-card {{ padding: 0.85rem; }}
.waveform-card svg {{ display: block; max-width: 100%; height: auto; margin-top: 0.5rem; border-radius: 8px; }}
.summary-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(10rem, 1fr)); gap: 0.75rem; margin: 1rem 0 1.25rem; padding: 0.85rem; }}
.summary-card {{ padding: 0.85rem; min-width: 0; }}
.summary-card .label {{ color: var(--muted); font-size: 0.85rem; }}
.summary-card .value {{ display: inline-block; font-size: 1.25rem; font-weight: 750; margin-top: 0.15rem; overflow-wrap: anywhere; }}
.badge, .summary-card .value.ok, .summary-card .value.failed, .summary-card .value.warning {{ border-radius: 999px; padding: 0.08rem 0.5rem; font-size: 0.95rem; }}
.summary-card .value.ok, .badge.ok {{ background: var(--ok-bg); color: var(--ok); }}
.summary-card .value.failed, .badge.failed {{ background: var(--failed-bg); color: var(--failed); }}
.summary-card .value.warning, .badge.warning {{ background: var(--warning-bg); color: var(--warning); }}
.evidence-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr)); gap: 0.6rem; margin: 0.5rem 0 1.25rem; }}
.evidence-card {{ padding: 0.7rem 0.8rem; min-width: 0; }}
.evidence-card .label {{ color: var(--muted); font-size: 0.82rem; line-height: 1.35; }}
.evidence-card .value {{ display: inline-block; margin-top: 0.2rem; font-weight: 750; overflow-wrap: anywhere; }}
.evidence-card .value.ok, .evidence-card .value.failed, .evidence-card .value.warning {{ border-radius: 999px; padding: 0.08rem 0.5rem; }}
.evidence-cell {{ min-width: 18rem; max-width: 40rem; }}
.evidence-token {{ display: inline-block; margin: 0 0.35rem 0.25rem 0; padding: 0.1rem 0.4rem; border: 1px solid #e5e7eb; border-radius: 999px; background: #f8fafc; line-height: 1.35; }}
.muted {{ color: var(--muted); font-weight: 500; }}
.ok {{ color: var(--ok); }}
.failed {{ color: var(--failed); }}
.warning {{ color: var(--warning); }}
.expectations-table tr.failed td {{ background: #fff8f8; }}
.expectations-table tr.ok td {{ background: #f7fff9; }}
.acceptance-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr)); gap: 0.75rem; margin: 0.5rem 0 1.25rem; }}
.acceptance-card {{ padding: 0.85rem; }}
.acceptance-card header {{ display: flex; justify-content: space-between; gap: 0.5rem; align-items: start; margin-bottom: 0.45rem; }}
.acceptance-card h3 {{ margin: 0; font-size: 1rem; }}
.acceptance-card dl {{ margin: 0; display: grid; gap: 0.25rem; }}
.acceptance-card div {{ display: grid; grid-template-columns: 5.5rem 1fr; gap: 0.5rem; }}
.acceptance-card dt {{ color: var(--muted); }}
.acceptance-card dd {{ margin: 0; font-weight: 650; overflow-wrap: anywhere; }}
.dmm-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(17rem, 1fr)); gap: 0.75rem; margin: 0.5rem 0 1.25rem; }}
.dmm-card {{ padding: 0.9rem; }}
.dmm-card header {{ display: flex; justify-content: space-between; gap: 0.5rem; align-items: start; margin-bottom: 0.55rem; }}
.dmm-card h3 {{ margin: 0; font-size: 1rem; }}
.dmm-card .reading {{ font-size: 1.9rem; line-height: 1.15; font-weight: 800; margin: 0.1rem 0 0.65rem; overflow-wrap: anywhere; }}
.dmm-card .reading .unit {{ color: var(--muted); font-size: 1rem; font-weight: 650; margin-left: 0.25rem; }}
.dmm-card dl {{ margin: 0; display: grid; gap: 0.25rem; }}
.dmm-card div {{ display: grid; grid-template-columns: 5.5rem 1fr; gap: 0.5rem; }}
.dmm-card dt {{ color: var(--muted); }}
    .dmm-card dd {{ margin: 0; font-weight: 650; overflow-wrap: anywhere; }}
    .frequency-response-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(22rem, 1fr)); gap: 1rem; margin: 0.5rem 0 1.25rem; }}
    .frequency-response-card {{ padding: 0.85rem; }}
    .frequency-response-card h3 {{ margin: 0 0 0.35rem; font-size: 1rem; }}
    .frequency-response-card svg {{ display: block; width: 100%; height: auto; margin-top: 0.5rem; border-radius: 8px; }}
    .fit-formula {{ white-space: pre-wrap; overflow-wrap: anywhere; }}
@page {{ size: A4 landscape; margin: 10mm; }}
@media print {{
  body {{ background: #fff; font-size: 9pt; }}
  main {{ max-width: none; padding: 0; }}
  section, article.card, figure.card, .table {{ box-shadow: none; }}
  article.card, figure.card {{ break-inside: avoid; }}
  .table {{ overflow: visible; }}
  .compact-table table {{ font-size: 7pt; }}
  .compact-table th, .compact-table td {{ padding: 0.25rem 0.3rem; }}
  .frequency-response-grid {{ grid-template-columns: 1fr; }}
  body.pdf-compact h1 {{ font-size: 17pt; margin: 0 0 0.15rem; }}
  body.pdf-compact h2 {{ margin: 0.65rem 0 0.3rem; font-size: 13pt; }}
  body.pdf-compact .summary-grid {{ display: block; margin: 0.35rem 0 0.55rem; padding: 0; break-inside: avoid; page-break-inside: avoid; }}
  body.pdf-compact .summary-card {{ box-sizing: border-box; display: inline-block; vertical-align: top; width: 24%; min-height: 0; margin: 0.2% 0.35%; padding: 0.38rem 0.45rem; border-radius: 7px; break-inside: avoid; page-break-inside: avoid; }}
  body.pdf-compact .summary-card .label {{ font-size: 7.5pt; line-height: 1.25; }}
  body.pdf-compact .summary-card .value {{ font-size: 10.5pt; line-height: 1.22; margin-top: 0.08rem; }}
  body.pdf-compact .summary-card .value.ok, body.pdf-compact .summary-card .value.failed, body.pdf-compact .summary-card .value.warning {{ font-size: 8.5pt; padding: 0.04rem 0.3rem; }}
}}
</style>
</head>
<body class="{body_class}">
<main>
<h1>WaveBench 运行报告 <span class="muted">Run report</span></h1>
<p class="muted">A static offline hardware validation report.</p>
{compact_note}
{_summary_block(summary, compact=compact)}
{evidence_summary_block}
{evidence_timeline_block}
{artifact_links_block}
<article class="card meta-card">
<p><b>运行目录 / Run directory:</b> <code>{escape(str(run.path))}</code></p>
<p><b>状态 / Status:</b> <span class="badge {escape(run.status)}">{escape(run.status)}</span></p>
<p><b>实验 / Experiment:</b> {escape(str(experiment.get('name', '')))} / {escape(str(experiment.get('label', '')))}</p>
<p><b>summary.csv:</b> {summary_note}</p>
{restore_block}
</article>
{error_block}
<h2>步骤 / Steps</h2>
<div class="table">
<table>
<thead><tr><th>#</th><th>类型 / Kind</th><th>状态 / Status</th><th>采集包 / Package</th><th>截图 / Screenshot</th><th>质量 / Quality</th><th>预期 / Expect</th><th>警告 / Warnings</th><th>失败项 / Failures</th></tr></thead>
<tbody>
{rows}
</tbody>
</table>
</div>
    {dmm_block}
    {sweep_block}
    {frequency_response_block}
    {acceptance_block}
{expectations_block}
{signals_block}
{waveform_previews_block}
{screenshots_block}
</main>
</body>
</html>
"""


def _build_report_manifest(run: RunPackage, *, output_dir: Path, report_path: Path) -> dict[str, Any]:
    screenshots = _collect_screenshots(run, output_dir)
    capture_packages: list[dict[str, Any]] = []
    waveform_previews: list[dict[str, Any]] = []
    warnings: list[str] = []
    for step in run.steps:
        artifact = step.get("artifact", {}) if isinstance(step.get("artifact"), dict) else {}
        package_text = artifact.get("package")
        if not package_text:
            continue
        package = str(package_text)
        package_dir = _resolve_artifact_path(run.path, package)
        step_index = str(step.get("index", ""))
        package_record = {
            "step_index": step_index,
            "package": package,
            "path": _relative_url(package_dir, output_dir),
            "exists": package_dir.exists(),
        }
        capture_packages.append(package_record)
        if not package_dir.exists():
            warnings.append(f"step {step_index}: capture package missing: {package}")
        metadata = _read_capture_metadata(package_dir, artifact.get("metadata"))
        for channel, npy_text, _summary in _metadata_waveform_npy_files(metadata):
            if npy_text:
                npy_path = _resolve_capture_file_path(run.path, package_dir, str(npy_text))
                npy_exists = npy_path.exists()
                source_npy = _relative_url(npy_path, output_dir)
                if not npy_exists:
                    warnings.append(f"step {step_index} ch{channel}: waveform npy missing: {npy_text}")
            else:
                npy_exists = False
                source_npy = ""
                warnings.append(f"step {step_index} ch{channel}: waveform npy missing")
            waveform_previews.append(
                {
                    "step_index": step_index,
                    "package": package,
                    "channel": channel,
                    "source_npy": source_npy,
                    "exists": npy_exists,
                    "generated": "inline-svg",
                }
            )
    known_packages = {str(record["package"]) for record in capture_packages}
    for reference in _capture_references(run):
        if reference.package in known_packages:
            continue
        package_dir = _resolve_artifact_path(run.path, reference.package)
        capture_packages.append(
            {
                "step_index": reference.step_index,
                "package": reference.package,
                "path": _relative_url(package_dir, output_dir),
                "exists": package_dir.exists(),
            }
        )
        if not package_dir.exists():
            warnings.append(
                f"step {reference.step_index}: capture package missing: {reference.package}"
            )
    return {
        "schema": "wavebench.report_manifest.v1",
        "report": _relative_url(report_path, output_dir),
        "run_json": _relative_url(run.run_json_path, output_dir),
        "summary_csv": _relative_url(run.summary_csv_path, output_dir) if run.summary_csv_path is not None else None,
        "frequency_response_csv": _relative_url(run.frequency_response_csv_path, output_dir)
        if run.frequency_response_csv_path is not None
        else None,
        "frequency_response_fit_json": _relative_url(run.frequency_response_fit_path, output_dir)
        if run.frequency_response_fit_path is not None
        else None,
        "frequency_response_calibration_csv": _relative_url(
            run.frequency_response_calibration_csv_path, output_dir
        )
        if run.frequency_response_calibration_csv_path is not None
        else None,
        "frequency_response_calibration_json": _relative_url(
            run.frequency_response_calibration_path, output_dir
        )
        if run.frequency_response_calibration_path is not None
        else None,
        "frequency_responses": [
            {
                "label": response.label,
                "directory": _relative_url(response.directory, output_dir),
                "csv": _relative_url(response.csv_path, output_dir)
                if response.csv_path is not None
                else None,
                "baseline_json": _relative_url(response.baseline_path, output_dir)
                if response.baseline_path is not None
                else None,
                "calibration_json": _relative_url(response.calibration_path, output_dir)
                if response.calibration_path is not None
                else None,
                "status": response.manifest_entry.get("status"),
                "adaptive": response.manifest_entry.get("adaptive"),
            }
            for response in run.frequency_responses
        ],
        "capture_packages": capture_packages,
        "screenshots": [
            {
                "step_index": item.step_index,
                "package": item.package,
                "path": _relative_url(item.path, output_dir),
            }
            for item in screenshots
        ],
        "waveform_previews": waveform_previews,
        "warnings": warnings,
    }


def _summary_block(summary: ReportSummary, *, compact: bool = False) -> str:
    css_class = "summary-grid pdf-summary-grid" if compact else "summary-grid"
    return f"""<h2>摘要 / Summary</h2>
<section class="{css_class}">
{_summary_card("状态 / Status", summary.status, css_class=summary.status)}
{_summary_card("实验 / Experiment", summary.experiment_label)}
{_summary_card("步骤 / Steps", str(summary.total_steps))}
{_summary_card("失败步骤 / Failed steps", str(summary.failed_steps), css_class="failed" if summary.failed_steps else "ok")}
{_summary_card("采集 / Captures", str(summary.capture_count))}
{_summary_card("警告 / Warnings", str(summary.warning_count), css_class="warning" if summary.warning_count else "ok")}
{_summary_card("预期失败 / Expect failed", str(summary.failed_expect_count), css_class="failed" if summary.failed_expect_count else "ok")}
{_summary_card("截图 / Screenshots", str(summary.screenshot_count))}
{_summary_card("恢复 / Restore", summary.restore_status)}
{_summary_card("主频率 / Primary frequency", summary.primary_frequency)}
{_summary_card("主峰峰值 / Primary Vpp", summary.primary_vpp)}
</section>
"""


def _summary_card(label: str, value: str, *, css_class: str = "") -> str:
    safe_class = f" {escape(css_class)}" if css_class else ""
    return (
        '<article class="card summary-card">'
        f'<div class="label">{escape(label)}</div>'
        f'<div class="value{safe_class}">{escape(value) if value else "-"}</div>'
        '</article>'
    )


def _evidence_summary_block(evidence: ReportEvidenceSummary) -> str:
    cards = [
        ("信号源设置步骤 / Source setting steps", str(evidence.source_step_count), ""),
        ("示波器采集步骤 / Scope capture steps", str(evidence.scope_capture_count), ""),
        ("DMM 读数 / DMM readings", str(evidence.dmm_reading_count), ""),
        (
            "失败预期项 / Failed expectations",
            str(evidence.failed_expectation_count),
            "failed" if evidence.failed_expectation_count else "ok",
        ),
        ("run.json", _availability_text(evidence.run_json_available), "ok" if evidence.run_json_available else "failed"),
        (
            "summary.csv",
            _availability_text(evidence.summary_csv_available),
            "ok" if evidence.summary_csv_available else "warning",
        ),
        ("采集包 / Capture packages", str(evidence.capture_package_count), ""),
        ("截图 / Screenshots", str(evidence.screenshot_count), ""),
        ("波形预览 / Waveform previews", str(evidence.waveform_preview_count), ""),
    ]
    body = "\n".join(_evidence_summary_card(label, value, css_class) for label, value, css_class in cards)
    return f"""<h2>实验证据摘要 / Run evidence summary</h2>
<section class="evidence-grid">
{body}
</section>
"""


def _evidence_summary_card(label: str, value: str, css_class: str) -> str:
    safe_class = f" {escape(css_class)}" if css_class else ""
    return (
        '<article class="card evidence-card">'
        f'<div class="label">{escape(label)}</div>'
        f'<div class="value{safe_class}">{escape(value)}</div>'
        "</article>"
    )


def _availability_text(available: bool) -> str:
    return "存在 / present" if available else "缺失 / missing"


def _evidence_timeline_block(run: RunPackage, screenshots_by_step: dict[str, ReportScreenshot]) -> str:
    rows = "\n".join(_evidence_timeline_row(step, screenshots_by_step) for step in run.steps)
    if not rows:
        rows = '<tr><td colspan="4">没有记录步骤 / No steps recorded.</td></tr>'
    return f"""<h2>证据时间线 / Evidence timeline</h2>
<div class="table"><table>
<thead><tr><th>步骤 / Step</th><th>类型 / Kind</th><th>状态 / Status</th><th>证据 / Evidence</th></tr></thead>
<tbody>
{rows}
</tbody>
</table>
</div>
"""


def _evidence_timeline_row(step: dict[str, Any], screenshots_by_step: dict[str, ReportScreenshot]) -> str:
    status = str(step.get("status", ""))
    evidence = _evidence_text_html(_step_evidence_summary(step, screenshots_by_step))
    return (
        "<tr>"
        f"<td>{escape(str(step.get('index', '')))}</td>"
        f"<td>{escape(str(step.get('kind', '')))}</td>"
        f'<td><span class="badge {escape(status)}">{escape(status)}</span></td>'
        f'<td class="evidence-cell">{evidence}</td>'
        "</tr>"
    )


def _evidence_text_html(text: str) -> str:
    parts = [part.strip() for part in text.split(";") if part.strip()]
    if not parts:
        return ""
    return "".join(f'<span class="evidence-token">{escape(part)}</span>' for part in parts)


def _step_evidence_summary(step: dict[str, Any], screenshots_by_step: dict[str, ReportScreenshot]) -> str:
    kind = str(step.get("kind", ""))
    artifact = step.get("artifact", {}) if isinstance(step.get("artifact"), dict) else {}
    if kind.startswith("source."):
        return _source_step_evidence(step, artifact)
    if kind == "scope.capture":
        return _scope_step_evidence(step, artifact, screenshots_by_step)
    if kind == "dmm.read":
        return _dmm_step_evidence(step, artifact)
    if kind == "sleep":
        duration = artifact.get("duration_s", _step_field(step, "duration_s"))
        return _join_evidence_parts(["等待 / Sleep", _labeled_value("时长 / Duration", _format_metric(duration, "s"))])
    if kind == "scope.auto":
        return _join_evidence_parts(["示波器 / Scope", _labeled_value("自动设置 / Autoscale", artifact.get("autoscale"))])
    return _join_evidence_parts([_labeled_value("产物 / Artifact", ", ".join(sorted(artifact.keys())))])


def _source_step_evidence(step: dict[str, Any], artifact: dict[str, Any]) -> str:
    status = artifact.get("source_status", {}) if isinstance(artifact.get("source_status"), dict) else {}
    parts = ["信号源 / Source"]
    for label, key, unit in (
        ("通道 / Channel", "channel", ""),
        ("功能 / Function", "function", ""),
        ("频率 / Frequency", "frequency_hz", "Hz"),
        ("输出 / Output", "output", ""),
    ):
        value = status.get(key, _step_field(step, key))
        parts.append(_labeled_value(label, _format_metric(value, unit) if unit else _format_plain(value)))
    amplitude = status.get("amplitude", _step_field(step, "value_vpp"))
    amplitude_unit = str(status.get("amplitude_unit") or "Vpp")
    parts.append(_labeled_value("幅度 / Amplitude", _format_metric(amplitude, amplitude_unit)))
    return _join_evidence_parts(parts)


def _scope_step_evidence(
    step: dict[str, Any], artifact: dict[str, Any], screenshots_by_step: dict[str, ReportScreenshot]
) -> str:
    quality = artifact.get("quality", {}) if isinstance(artifact.get("quality"), dict) else {}
    expect = artifact.get("expect", {}) if isinstance(artifact.get("expect"), dict) else {}
    expect_fft = artifact.get("expect_fft", {}) if isinstance(artifact.get("expect_fft"), dict) else {}
    warnings = quality.get("warnings", [])
    warnings_text = " | ".join(str(item) for item in warnings) if isinstance(warnings, list) else str(warnings)
    step_index = str(step.get("index", ""))
    screenshot_text = "存在 / present" if step_index in screenshots_by_step else "缺失 / missing"
    parts = [
        "示波器 / Scope",
        _labeled_value("采集包 / Package", artifact.get("package")),
        _labeled_value("截图 / Screenshot", screenshot_text),
        _labeled_value("质量 / Quality", quality.get("status")),
        _labeled_value("预期 / Expect", expect.get("status")),
        _labeled_value("FFT 预期 / FFT expect", expect_fft.get("status")),
        _labeled_value("警告 / Warnings", warnings_text),
    ]
    return _join_evidence_parts(parts)


def _dmm_step_evidence(step: dict[str, Any], artifact: dict[str, Any]) -> str:
    reading = artifact.get("dmm_reading", {}) if isinstance(artifact.get("dmm_reading"), dict) else {}
    expect = artifact.get("expect", {}) if isinstance(artifact.get("expect"), dict) else {}
    value = _format_plain(reading.get("value"))
    unit = str(reading.get("unit", ""))
    if value and unit:
        value = f"{value} {unit}"
    return _join_evidence_parts(
        [
            "DMM",
            _labeled_value("功能 / Function", reading.get("function") or _step_field(step, "function")),
            _labeled_value("读数 / Reading", value),
            _labeled_value("预期 / Expect", expect.get("status")),
        ]
    )


def _labeled_value(label: str, value: Any) -> str:
    text = _format_plain(value)
    return f"{label}: {text}" if text else ""


def _join_evidence_parts(parts: list[str]) -> str:
    return "; ".join(part for part in parts if part)


def _artifact_links_block(links: list[ReportArtifactLink]) -> str:
    if not links:
        return ""
    rows = "\n".join(_artifact_link_row(link) for link in links)
    return f"""<h2>产物链接 / Artifact links</h2>
<div class="table compact-table artifact-table"><table>
<thead><tr><th>步骤 / Step</th><th>类型 / Type</th><th>产物 / Artifact</th><th>链接 / Link</th><th>状态 / Status</th></tr></thead>
<tbody>
{rows}
</tbody>
</table>
</div>
"""


def _artifact_link_row(link: ReportArtifactLink) -> str:
    href = escape(link.href, quote=True)
    return (
        "<tr>"
        f"<td>{escape(link.step_index)}</td>"
        f"<td>{escape(link.kind)}</td>"
        f"<td><code>{escape(link.label)}</code></td>"
        f'<td><a class="artifact-link" href="{href}">{escape(link.href)}</a></td>'
        f"<td>{escape(link.status)}</td>"
        "</tr>"
    )


def _acceptance_block(expectations: list[ReportExpectationRow]) -> str:
    selected = _acceptance_rows(expectations)
    if not selected:
        return ""
    cards = "\n".join(_acceptance_card(row) for row in selected)
    return f"""<h2>验收摘要 / Acceptance summary</h2>
<section class="acceptance-grid">
{cards}
</section>
"""


def _acceptance_rows(expectations: list[ReportExpectationRow]) -> list[ReportExpectationRow]:
    preferred = ("frequency", "voltage_vpp", "vpp", "duty", "harmonic", "thd")
    rows = [row for row in expectations if any(token in row.metric.lower() for token in preferred)]
    if rows:
        return rows
    return expectations[:6]


def _acceptance_card(row: ReportExpectationRow) -> str:
    status = escape(row.status or "unknown")
    return f"""<article class="card acceptance-card">
<header><h3>{escape(_metric_label(row.metric))}</h3><span class="badge {status}">{status}</span></header>
<dl>
<div><dt>实测 / Measured</dt><dd>{escape(row.measured or "-")}</dd></div>
<div><dt>预期 / Expected</dt><dd>{escape(row.expected or "-")}</dd></div>
<div><dt>步骤 / Step</dt><dd>{escape(row.step_index)} · {escape(row.step_kind)}</dd></div>
</dl>
</article>
"""


def _dmm_readings_block(readings: list[ReportDmmReading]) -> str:
    if not readings:
        return ""
    cards = "\n".join(_dmm_reading_card(reading) for reading in readings)
    return f"""<h2>DMM 读数 / DMM readings</h2>
<section class="dmm-grid">
{cards}
</section>
"""


def _dmm_reading_card(reading: ReportDmmReading) -> str:
    status = escape(reading.status or "unknown")
    unit = f'<span class="unit">{escape(reading.unit)}</span>' if reading.unit else ""
    details = ""
    if reading.details:
        details = f"<div><dt>细节 / Details</dt><dd>{escape(reading.details)}</dd></div>"
    return f"""<article class="card dmm-card">
<header><h3>{escape(reading.step_name)}</h3><span class="badge {status}">{status}</span></header>
<p class="reading">{escape(reading.value or "-")}{unit}</p>
<dl>
<div><dt>功能 / Function</dt><dd>{escape(reading.function or "-")}</dd></div>
<div><dt>步骤 / Step</dt><dd>{escape(reading.step_index)} · {escape(reading.step_name)}</dd></div>
<div><dt>预期 / Expected</dt><dd>{escape(reading.expected or "-")}</dd></div>
{details}
</dl>
</article>
"""


def _sweep_summary_block(rows: list[ReportSweepRow]) -> str:
    if not rows:
        return ""
    body = "\n".join(_sweep_summary_row(row) for row in rows)
    return f"""<h2>扫频摘要 / Sweep summary</h2>
<div class="table compact-table"><table>
<thead><tr><th>步骤 / Step</th><th>标签 / Label</th><th>状态 / Status</th><th>质量 / Quality</th><th>预期 / Expect</th><th>FFT / FFT</th><th>频率 / Frequency</th><th>Vpp</th><th>FFT 主频 / FFT peak</th><th>主幅度 / Peak amp</th><th>THD</th></tr></thead>
<tbody>
{body}
</tbody>
</table>
</div>
"""


def _sweep_summary_row(row: ReportSweepRow) -> str:
    status = escape(row.status)
    return (
        f'<tr class="{status}">'
        f"<td>{escape(row.step_index)}</td>"
        f"<td>{escape(row.label)}</td>"
        f'<td class="{status}">{escape(row.status)}</td>'
        f"<td>{escape(row.quality_status)}</td>"
        f"<td>{escape(row.expect_status)}</td>"
        f"<td>{escape(row.fft_status)}</td>"
        f"<td>{escape(row.frequency_hz)}</td>"
        f"<td>{escape(row.vpp_v)}</td>"
        f"<td>{escape(row.fft_peak_hz)}</td>"
        f"<td>{escape(row.fft_peak_amplitude_v)}</td>"
        f"<td>{escape(row.thd_ratio)}</td>"
        "</tr>"
    )


def _frequency_response_block(run: RunPackage, *, include_table: bool = True) -> str:
    if not run.frequency_responses:
        return ""
    multiple = len(run.frequency_responses) > 1
    return "".join(
        _frequency_response_section(response, include_table=include_table, multiple=multiple)
        for response in run.frequency_responses
    )


def _frequency_response_section(
    response: FrequencyResponsePackage, *, include_table: bool, multiple: bool
) -> str:
    rows = response.rows
    raw_gain_measurements = _response_amplitude_series(rows, "gain_db")
    raw_phase_measurements = _response_amplitude_series(rows, "phase_unwrapped_deg")
    corrected_gain_measurements = _response_amplitude_series(rows, "gain_db_corrected")
    corrected_phase_measurements = _response_amplitude_series(rows, "phase_unwrapped_corrected_deg")
    raw_gain_blocks = [] if raw_gain_measurements else _response_blocks(rows, "gain_db")
    raw_phase_blocks = [] if raw_phase_measurements else _response_blocks(rows, "phase_unwrapped_deg")
    corrected_gain_blocks = [] if corrected_gain_measurements else _response_blocks(rows, "gain_db_corrected")
    corrected_phase_blocks = [] if corrected_phase_measurements else _response_blocks(rows, "phase_unwrapped_corrected_deg")
    fit_series = _fit_curve_series(response.fit)
    gain_svg = _response_svg(
        raw_gain_blocks,
        title="原始幅频 / Raw magnitude response",
        y_label="Gain (dB)",
        series=(),
        measured_series=raw_gain_measurements,
        actual_label="",
    )
    phase_svg = _response_svg(
        raw_phase_blocks,
        title="原始相频 / Raw phase response",
        y_label="Phase (deg, unwrapped)",
        series=(),
        measured_series=raw_phase_measurements,
        actual_label="",
    )
    corrected_gain_svg = _response_svg(
        corrected_gain_blocks,
        title="校正幅频 / Corrected magnitude response",
        y_label="Gain (dB)",
        series=(),
        measured_series=corrected_gain_measurements,
        actual_label="",
    ) if corrected_gain_blocks or corrected_gain_measurements else '<p class="muted">未配置软件基线校正 / No software baseline correction.</p>'
    corrected_phase_svg = _response_svg(
        corrected_phase_blocks,
        title="校正相频 / Corrected phase response",
        y_label="Phase (deg, unwrapped)",
        series=(),
        measured_series=corrected_phase_measurements,
        actual_label="",
    ) if corrected_phase_blocks or corrected_phase_measurements else '<p class="muted">未配置软件基线校正 / No software baseline correction.</p>'
    linear_measurements = _response_amplitude_series(rows, "gain_linear_corrected") or _response_amplitude_series(
        rows, "gain_linear"
    )
    linear_blocks = [] if linear_measurements else [
        [point]
        for block in _response_blocks(rows, "gain_linear_corrected") or _response_blocks(rows, "gain_linear")
        for point in block
    ]
    fit_svg = _response_svg(
        linear_blocks,
        title="线性增益拟合 / Linear gain fit comparison",
        y_label="Linear gain (V/V)",
        series=fit_series,
        measured_series=linear_measurements,
        actual_label="Measured",
    )
    table_block = ""
    if include_table:
        table_rows = "\n".join(_frequency_response_row(row) for row in rows)
        if not table_rows:
            table_rows = '<tr><td colspan="10">频响 CSV 没有可读取的记录 / No readable response rows.</td></tr>'
        table_block = f"""<div class="table compact-table"><table>
<thead><tr><th>#</th><th>请求幅值 / Requested Vpp</th><th>请求频率 / Requested</th><th>输入峰值 / Input peak</th><th>输出峰值 / Output peak</th><th>线性增益</th><th>增益 / Gain</th><th>相位 / Phase</th><th>展开相位 / Unwrapped</th><th>状态 / Status</th><th>警告或错误 / Warning or error</th></tr></thead>
<tbody>
{table_rows}
</tbody>
</table></div>"""
    else:
        table_block = '<p class="muted">逐频点结果请见本响应目录的 <code>frequency_response.csv</code>。</p>'
    fit_summary = (
        _fit_summary_block(response.fit, response.fit_error)
        if include_table
        else _compact_fit_summary_block(response.fit, response.fit_error)
    )
    calibration_block = _frequency_response_calibration_block(response)
    baseline_block = _frequency_response_baseline_block(response)
    adaptive_block = _frequency_response_adaptive_block(response)
    matrix_summary = _frequency_response_matrix_summary(rows)
    title = "频率响应 / Frequency response"
    if multiple:
        title += f" — {escape(response.label)}"
    return f"""<h2>{title}</h2>
<p class="muted">响应标签 / Response label: <code>{escape(response.label)}</code>。原始幅相保留为证据；软件基线校正不会改写仪器 deskew 或前面板设置。</p>
{matrix_summary}
{baseline_block}
{adaptive_block}
<section class="frequency-response-grid">
<article class="card frequency-response-card"><h3>原始幅频 / Raw magnitude</h3>{gain_svg}</article>
<article class="card frequency-response-card"><h3>原始相频 / Raw phase</h3>{phase_svg}</article>
</section>
<section class="frequency-response-grid">
<article class="card frequency-response-card"><h3>校正幅频 / Corrected magnitude</h3>{corrected_gain_svg}</article>
<article class="card frequency-response-card"><h3>校正相频 / Corrected phase</h3>{corrected_phase_svg}</article>
</section>
<section class="frequency-response-grid">
<article class="card frequency-response-card"><h3>拟合对比 / Fit comparison</h3>{fit_svg}</article>
</section>
{fit_summary}
{calibration_block}
{table_block}
"""


def _frequency_response_baseline_block(response: FrequencyResponsePackage) -> str:
    if response.baseline_error:
        return f'<p class="warning">基线审计 JSON 无法读取：{escape(response.baseline_error)}</p>'
    if not response.baseline:
        return ""
    mode = response.baseline.get("mode", "")
    source = response.baseline.get("baseline_response", "")
    domain = response.baseline.get("valid_domain_hz_by_requested_vpp", {})
    delay = response.baseline.get("estimated_delay_s_by_requested_vpp", {})
    return (
        '<div class="table compact-table"><table><thead><tr><th>软件基线 / Software baseline</th>'
        '<th>模式 / Mode</th><th>有效域 / Valid domain</th><th>估算延迟 / Estimated delay</th></tr></thead>'
        f'<tbody><tr><td>{escape(str(source))}</td><td>{escape(str(mode))}</td>'
        f'<td><code>{escape(json.dumps(domain, ensure_ascii=False))}</code></td>'
        f'<td><code>{escape(json.dumps(delay, ensure_ascii=False))}</code></td></tr></tbody></table></div>'
    )


def _frequency_response_adaptive_block(response: FrequencyResponsePackage) -> str:
    adaptive = response.manifest_entry.get("adaptive")
    if not isinstance(adaptive, dict):
        return ""
    return (
        '<div class="table compact-table"><table><thead><tr><th>自适应加密 / Adaptive refinement</th>'
        '<th>初始点</th><th>加密层数</th><th>最终点数</th><th>预算限制</th></tr></thead><tbody><tr>'
        f'<td>{escape(str(adaptive.get("configuration", {})))}</td>'
        f'<td>{escape(str(adaptive.get("initial_frequency_count", "")))}</td>'
        f'<td>{escape(str(adaptive.get("refinement_levels_completed", "")))}</td>'
        f'<td>{escape(str(adaptive.get("final_frequency_count", "")))}</td>'
        f'<td>{escape(str(adaptive.get("budget_limited", False)))}</td>'
        '</tr></tbody></table></div>'
    )


def _frequency_response_row(row: dict[str, str]) -> str:
    status = str(row.get("status", ""))
    details = " | ".join(
        value for value in (str(row.get("warnings", "")), str(row.get("error", ""))) if value
    )
    return (
        f'<tr class="{escape(status)}">'
        f"<td>{escape(str(row.get('index', '')))}</td>"
        f"<td>{escape(_format_metric(row.get('requested_vpp'), 'Vpp'))}</td>"
        f"<td>{escape(_format_metric(row.get('requested_frequency_hz'), 'Hz'))}</td>"
        f"<td>{escape(_format_metric(row.get('reference_amplitude_peak_v'), 'V'))}</td>"
        f"<td>{escape(_format_metric(row.get('response_amplitude_peak_v'), 'V'))}</td>"
        f"<td>{escape(_format_plain(row.get('gain_linear')))}</td>"
        f"<td>{escape(_format_metric(row.get('gain_db'), 'dB'))}</td>"
        f"<td>{escape(_format_metric(row.get('phase_wrapped_deg'), 'deg'))}</td>"
        f"<td>{escape(_format_metric(row.get('phase_unwrapped_deg'), 'deg'))}</td>"
        f'<td class="{escape(status)}">{escape(status)}</td>'
        f"<td>{escape(details)}</td>"
        "</tr>"
    )


def _fit_summary_block(document: dict[str, Any] | None, error: str | None) -> str:
    if error:
        return f'<p class="warning">拟合 JSON 无法读取 / Fit JSON unavailable: {escape(error)}</p>'
    if not document:
        return '<p class="muted">未启用拟合 / Fit was not enabled for this run.</p>'
    methods = document.get("methods", {})
    if not isinstance(methods, dict) or not methods:
        return '<p class="muted">没有可用的拟合结果 / No fit results available.</p>'
    rows = []
    for name, raw in methods.items():
        payload = raw if isinstance(raw, dict) else {}
        metrics = payload.get("metrics", {}) if isinstance(payload.get("metrics"), dict) else {}
        parameters = payload.get("parameters", {})
        parameter_text = json.dumps(parameters, ensure_ascii=False) if parameters else ""
        rows.append(
            "<tr>"
            f"<td>{escape(str(payload.get('display') or name))}</td>"
            f"<td class=\"{escape(str(payload.get('status', '')))}\">{escape(str(payload.get('status', '')))}</td>"
            f"<td><code class=\"fit-formula\">{escape(str(payload.get('formula', payload.get('reason', ''))))}</code></td>"
            f"<td>{escape(_format_plain(metrics.get('rmse')))}</td>"
            f"<td>{escape(_format_plain(metrics.get('r_squared')))}</td>"
            f"<td><code class=\"fit-formula\">{escape(parameter_text)}</code></td>"
            "</tr>"
        )
    return f"""<h3>拟合公式与指标 / Fit formulas and metrics</h3>
<div class="table compact-table"><table>
<thead><tr><th>方法 / Method</th><th>状态 / Status</th><th>公式 / Formula</th><th>RMSE</th><th>R²</th><th>参数 / Parameters</th></tr></thead>
<tbody>{''.join(rows)}</tbody>
</table></div>
"""


def _compact_fit_summary_block(document: dict[str, Any] | None, error: str | None) -> str:
    """Keep portable PDFs readable when interpolation metadata has hundreds of segments."""
    if error:
        return f'<p class="warning">拟合 JSON 无法读取 / Fit JSON unavailable: {escape(error)}</p>'
    if not document:
        return '<p class="muted">未启用拟合 / Fit was not enabled for this run.</p>'
    methods = document.get("methods", {})
    if not isinstance(methods, dict) or not methods:
        return '<p class="muted">没有可用的拟合结果 / No fit results available.</p>'
    rows = []
    for name, raw in methods.items():
        payload = raw if isinstance(raw, dict) else {}
        metrics = payload.get("metrics", {}) if isinstance(payload.get("metrics"), dict) else {}
        formula = str(payload.get("formula", "")) if name == "polynomial" else "完整公式见拟合 JSON"
        rows.append(
            "<tr>"
            f"<td>{escape(str(payload.get('display') or name))}</td>"
            f"<td class=\"{escape(str(payload.get('status', '')))}\">{escape(str(payload.get('status', '')))}</td>"
            f"<td>{escape(_format_plain(metrics.get('rmse')))}</td>"
            f"<td>{escape(_format_plain(metrics.get('r_squared')))}</td>"
            f"<td><code class=\"fit-formula\">{escape(formula)}</code></td>"
            "</tr>"
        )
    return f"""<h3>线性增益拟合摘要 / Linear-gain fit summary</h3>
<div class="table compact-table"><table>
<thead><tr><th>方法 / Method</th><th>状态 / Status</th><th>RMSE</th><th>R²</th><th>公式 / Formula</th></tr></thead>
<tbody>{''.join(rows)}</tbody>
</table></div>"""


def _response_blocks(rows: list[dict[str, str]], key: str) -> list[list[tuple[float, float]]]:
    blocks: list[list[tuple[float, float]]] = []
    block: list[tuple[float, float]] = []
    amplitude: str | None = None
    for row in rows:
        current_amplitude = str(row.get("amplitude_index", row.get("requested_vpp", "")))
        if amplitude is not None and current_amplitude != amplitude and block:
            blocks.append(block)
            block = []
        amplitude = current_amplitude
        frequency = _finite_float(row.get("requested_frequency_hz"))
        value = _finite_float(row.get(key))
        if row.get("status") == "failed" or frequency is None or frequency <= 0 or value is None:
            if block:
                blocks.append(block)
                block = []
            continue
        block.append((frequency, value))
    if block:
        blocks.append(block)
    return blocks


def _response_amplitude_series(
    rows: list[dict[str, str]], key: str
) -> list[tuple[str, str, list[tuple[float, float]]]]:
    """Split a 2D sweep into one labeled, colored Bode series per requested Vpp."""
    groups: dict[str, tuple[float, list[tuple[float, float]]]] = {}
    for row in rows:
        requested_vpp = _finite_float(row.get("requested_vpp"))
        frequency = _finite_float(row.get("requested_frequency_hz"))
        value = _finite_float(row.get(key))
        if (
            requested_vpp is None
            or requested_vpp <= 0
            or frequency is None
            or frequency <= 0
            or value is None
            or row.get("status") == "failed"
        ):
            continue
        group_key = str(row.get("amplitude_index", "")) or f"{requested_vpp:.12g}"
        if group_key not in groups:
            groups[group_key] = (requested_vpp, [])
        groups[group_key][1].append((frequency, value))
    colors = ("#2563eb", "#dc2626", "#0891b2", "#7c3aed", "#ea580c", "#16a34a")
    return [
        (f"Measured · {_format_metric(amplitude, 'Vpp')}", colors[index % len(colors)], sorted(values))
        for index, (_group_key, (amplitude, values)) in enumerate(
            sorted(groups.items(), key=lambda item: item[1][0])
        )
    ]


def _frequency_response_matrix_summary(rows: list[dict[str, str]]) -> str:
    amplitudes = sorted(
        {
            value
            for row in rows
            if (value := _finite_float(row.get("requested_vpp"))) is not None and value > 0
        }
    )
    frequencies = sorted(
        {
            value
            for row in rows
            if (value := _finite_float(row.get("requested_frequency_hz"))) is not None and value > 0
        }
    )
    if not amplitudes or not frequencies:
        return ""
    combinations = len(amplitudes) * len(frequencies)
    observed = {
        (amplitude, frequency)
        for row in rows
        if (amplitude := _finite_float(row.get("requested_vpp"))) is not None
        and amplitude > 0
        and (frequency := _finite_float(row.get("requested_frequency_hz"))) is not None
        and frequency > 0
    }
    kind = "二维 / 2D" if len(amplitudes) > 1 else "单幅值 / single-amplitude"
    return (
        '<p class="muted"><strong>扫频矩阵 / Sweep matrix:</strong> '
        f'{escape(kind)}，{len(amplitudes)} 个 Vpp 切片 × {len(frequencies)} 个频率节点 '
        f'= {combinations} 个请求组合；CSV 已记录 {len(observed)}/{combinations} 个组合。'
        '</p>'
    )


def _frequency_response_calibration_block(response: FrequencyResponsePackage) -> str:
    if response.calibration_error:
        return (
            '<h3>二维校准 / 2D calibration</h3>'
            f'<p class="warning">校准 JSON 无法读取 / Calibration unavailable: '
            f'{escape(response.calibration_error)}</p>'
        )
    document = response.calibration
    rows = response.calibration_rows
    if not document and not rows:
        return ""
    configuration = document.get("configuration", {}) if isinstance(document, dict) else {}
    validation = document.get("validation", {}) if isinstance(document, dict) else {}
    domain = document.get("valid_domain", {}) if isinstance(document, dict) else {}
    target = document.get("target_gain_db") if isinstance(document, dict) else None
    summary = f"""<div class="table compact-table"><table>
<thead><tr><th>目标 / Target</th><th>有效频段 / Frequency range</th><th>请求幅值 / Requested Vpp</th><th>频率留点 RMSE</th><th>幅值留点 RMSE</th></tr></thead>
<tbody><tr><td>{escape(_format_metric(target, 'dB'))} ({escape(str(configuration.get('target_mode', '')) )})</td>
<td>{escape(_format_range(domain.get('frequency_hz'), 'Hz'))}</td>
<td>{escape(_format_range(domain.get('requested_vpp'), 'Vpp'))}</td>
<td>{escape(_format_metric(validation.get('frequency_holdout_rmse_db'), 'dB'))}</td>
<td>{escape(_format_metric(validation.get('amplitude_holdout_rmse_db'), 'dB'))}</td></tr></tbody>
</table></div>"""
    heatmap = _calibration_heatmap_svg(rows)
    curves = _calibration_curve_svg(rows)
    note = (
        '<p class="muted">二维 LUT 使用频率 dB 平滑样条与相邻请求 Vpp 线性插值；超出有效域不外推。'
        '完整浮点 LUT 与 Chebyshev 系数见 <code>frequency_response_calibration.csv</code> 和 '
        '<code>frequency_response_calibration.json</code>。</p>'
    )
    fixed_point = document.get("fixed_point") if isinstance(document, dict) else None
    fixed_note = ""
    if isinstance(fixed_point, dict):
        fixed_note = (
            '<p class="muted">定点部署 / Fixed point: '
            f'<code>{escape(str(fixed_point.get("q_format", "")))}</code>, '
            f'{escape(str(fixed_point.get("encoding", "")))}, 最大量化误差 / max error '
            f'{escape(_format_plain(fixed_point.get("max_abs_quantization_error")))}</p>'
        )
    return f"""<h3>二维校准 / 2D calibration</h3>
{summary}
<section class="frequency-response-grid">
<article class="card frequency-response-card"><h3>补偿热图 / Correction heatmap</h3>{heatmap}</article>
<article class="card frequency-response-card"><h3>代表性切片 / Representative slices</h3>{curves}</article>
</section>
{note}
{fixed_note}"""


def _calibration_heatmap_svg(rows: list[dict[str, str]]) -> str:
    points = _calibration_points(rows)
    if not points:
        return '<p class="warning">没有可绘制的校准 LUT 点 / No readable calibration points.</p>'
    amplitudes = sorted({point[0] for point in points})
    frequencies = sorted({point[1] for point in points})
    amplitude_indexes = _sample_indexes(len(amplitudes), 16)
    frequency_indexes = _sample_indexes(len(frequencies), 70)
    selected_amplitudes = [amplitudes[index] for index in amplitude_indexes]
    selected_frequencies = [frequencies[index] for index in frequency_indexes]
    lookup = {(amplitude, frequency): correction for amplitude, frequency, correction in points}
    values = [lookup[(amplitude, frequency)] for amplitude in selected_amplitudes for frequency in selected_frequencies if (amplitude, frequency) in lookup]
    if not values:
        return '<p class="warning">校准 LUT 不是完整矩阵，无法绘制热图。</p>'
    span = max(max(abs(value) for value in values), 0.1)
    width, height, left, top, right, bottom = 680, 285, 58, 28, 20, 48
    plot_width, plot_height = width - left - right, height - top - bottom
    cell_width = plot_width / len(selected_frequencies)
    cell_height = plot_height / len(selected_amplitudes)
    cells = []
    for row_index, amplitude in enumerate(selected_amplitudes):
        for column_index, frequency in enumerate(selected_frequencies):
            correction = lookup.get((amplitude, frequency))
            if correction is None:
                color = "#e5e7eb"
            else:
                color = _calibration_color(correction, span)
            cells.append(
                f'<rect x="{left + column_index * cell_width:.2f}" y="{top + row_index * cell_height:.2f}" '
                f'width="{cell_width + 0.1:.2f}" height="{cell_height + 0.1:.2f}" fill="{color}"/>'
            )
    return (
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Calibration correction heatmap">'
        f'<rect width="{width}" height="{height}" fill="#f8fafc"/>{"".join(cells)}'
        f'<rect x="{left}" y="{top}" width="{plot_width}" height="{plot_height}" fill="none" stroke="#9fb3c8"/>'
        f'<text x="{left}" y="16" font-size="12" fill="#334e68">Correction (dB), frequency increases left to right</text>'
        f'<text x="{left}" y="{height - 24}" font-size="10" fill="#627d98">{selected_frequencies[0]:.4g} Hz</text>'
        f'<text x="{left + plot_width - 80}" y="{height - 24}" font-size="10" fill="#627d98">{selected_frequencies[-1]:.4g} Hz</text>'
        f'<text x="{left}" y="{height - 8}" font-size="10" fill="#627d98">Vpp: {selected_amplitudes[0]:.4g} .. {selected_amplitudes[-1]:.4g}; color: -{span:.3g} .. +{span:.3g} dB</text>'
        '</svg>'
    )


def _calibration_curve_svg(rows: list[dict[str, str]]) -> str:
    points = _calibration_points(rows)
    grouped: dict[float, list[tuple[float, float]]] = {}
    for amplitude, frequency, correction in points:
        grouped.setdefault(amplitude, []).append((frequency, correction))
    amplitudes = sorted(grouped)
    if not amplitudes:
        return '<p class="warning">没有可绘制的校准 LUT 点 / No readable calibration points.</p>'
    indexes = sorted({0, len(amplitudes) // 2, len(amplitudes) - 1})
    colors = ("#2563eb", "#7c3aed", "#dc2626")
    series = [
        (f"{amplitudes[index]:.6g} Vpp", colors[position], sorted(grouped[amplitudes[index]]))
        for position, index in enumerate(indexes)
    ]
    return _response_svg(
        [],
        title="Correction by requested amplitude",
        y_label="Correction (dB)",
        series=series,
        actual_label="",
    )


def _calibration_points(rows: list[dict[str, str]]) -> list[tuple[float, float, float]]:
    points: list[tuple[float, float, float]] = []
    for row in rows:
        amplitude = _finite_float(row.get("requested_vpp"))
        frequency = _finite_float(row.get("frequency_hz"))
        correction = _finite_float(row.get("correction_db"))
        if amplitude is not None and amplitude > 0 and frequency is not None and frequency > 0 and correction is not None:
            points.append((amplitude, frequency, correction))
    return points


def _sample_indexes(count: int, maximum: int) -> list[int]:
    if count <= maximum:
        return list(range(count))
    return sorted({round(value) for value in np.linspace(0, count - 1, maximum)})


def _calibration_color(value: float, span: float) -> str:
    normalized = min(1.0, abs(value) / span)
    if value < 0:
        red = round(232 - 120 * normalized)
        green = round(241 - 75 * normalized)
        blue = round(248 - 5 * normalized)
    else:
        red = round(255 - 15 * normalized)
        green = round(247 - 105 * normalized)
        blue = round(237 - 135 * normalized)
    return f"#{red:02x}{green:02x}{blue:02x}"


def _format_range(value: Any, unit: str) -> str:
    if not isinstance(value, list) or len(value) != 2:
        return ""
    return f"{_format_metric(value[0], unit)} .. {_format_metric(value[1], unit)}"


def _fit_curve_series(document: dict[str, Any] | None) -> list[tuple[str, str, list[tuple[float, float]]]]:
    if not document:
        return []
    methods = document.get("methods", {})
    if not isinstance(methods, dict):
        return []
    colors = ("#7c3aed", "#dc2626", "#0891b2", "#ea580c")
    series = []
    for index, (name, raw) in enumerate(methods.items()):
        payload = raw if isinstance(raw, dict) else {}
        curve = payload.get("curve", [])
        values: list[tuple[float, float]] = []
        if isinstance(curve, list):
            for item in curve:
                if not isinstance(item, dict):
                    continue
                frequency = _finite_float(item.get("frequency_hz"))
                gain = _finite_float(item.get("gain_linear"))
                if frequency is not None and frequency > 0 and gain is not None:
                    values.append((frequency, gain))
        if values:
            series.append((str(payload.get("display") or name), colors[index % len(colors)], values))
    return series


def _response_svg(
    blocks: list[list[tuple[float, float]]],
    *,
    title: str,
    y_label: str,
    series: list[tuple[str, str, list[tuple[float, float]]]],
    measured_series: list[tuple[str, str, list[tuple[float, float]]]] = (),
    actual_label: str = "Measured",
) -> str:
    actual = [point for block in blocks for point in block]
    measured = [point for _name, _color, values in measured_series for point in values]
    all_points = actual + measured + [point for _name, _color, values in series for point in values]
    if not all_points:
        return '<p class="warning">没有可绘制的有效频点 / No valid points to plot.</p>'
    x_values = np.asarray([np.log10(point[0]) for point in all_points], dtype=float)
    y_values = np.asarray([point[1] for point in all_points], dtype=float)
    x_min = float(np.min(x_values))
    x_max = float(np.max(x_values))
    y_min = float(np.min(y_values))
    y_max = float(np.max(y_values))
    if x_min == x_max:
        x_min -= 0.5
        x_max += 0.5
    if y_min == y_max:
        y_min -= max(abs(y_min) * 0.1, 0.5)
        y_max += max(abs(y_max) * 0.1, 0.5)
    else:
        pad_y = (y_max - y_min) * 0.08
        y_min -= pad_y
        y_max += pad_y
    y_min, y_max, y_ticks, y_tick_step = _nice_linear_axis_ticks(y_min, y_max)
    x_ticks = _log_frequency_ticks(x_min, x_max)
    width, pad_left, pad_right, pad_top = 680, 72, 20, 28
    legend_items = (
        ([(actual_label, "#2563eb")] if actual and actual_label else [])
        + [(name, color) for name, color, _values in measured_series]
        + [(name, color) for name, color, _values in series]
    )
    legend_columns = 2
    legend_row_height = 15
    legend_rows = max(1, (len(legend_items) + legend_columns - 1) // legend_columns)
    # Tick labels and the common x-axis label sit above the legend.  They are
    # emitted into the SVG itself so the browser and WeasyPrint PDF receive the
    # exact same, self-contained coordinate system.
    legend_footer = 52
    pad_bottom = legend_footer + legend_rows * legend_row_height
    height = 288 + max(0, legend_rows - 2) * legend_row_height
    axis_y = height - pad_bottom

    def position(point: tuple[float, float]) -> tuple[float, float]:
        x_value = np.log10(point[0])
        px = pad_left + (x_value - x_min) / (x_max - x_min) * (width - pad_left - pad_right)
        py = axis_y - (point[1] - y_min) / (y_max - y_min) * (axis_y - pad_top)
        return float(px), float(py)

    polylines = []
    for block in blocks:
        if len(block) < 2:
            continue
        points = " ".join(f"{x:.2f},{y:.2f}" for x, y in (position(point) for point in block))
        polylines.append(f'<polyline points="{points}" fill="none" stroke="#2563eb" stroke-width="1.8"/>')
    fit_lines = []
    for name, color, values in series:
        points = " ".join(f"{x:.2f},{y:.2f}" for x, y in (position(point) for point in values))
        fit_lines.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="1.8"/>')
    circles = "".join(
        f'<circle cx="{x:.2f}" cy="{y:.2f}" r="3" fill="#2563eb"/>'
        for x, y in (position(point) for point in actual)
    )
    measured_lines = []
    measured_circles = []
    for _name, color, values in measured_series:
        points = " ".join(f"{x:.2f},{y:.2f}" for x, y in (position(point) for point in values))
        measured_lines.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="1.8"/>')
        measured_circles.extend(
            f'<circle cx="{x:.2f}" cy="{y:.2f}" r="2.6" fill="{color}"/>'
            for x, y in (position(point) for point in values)
        )
    x_grid = []
    x_tick_labels = []
    for frequency_hz in x_ticks:
        x_value = math.log10(frequency_hz)
        x = pad_left + (x_value - x_min) / (x_max - x_min) * (width - pad_left - pad_right)
        x_grid.append(
            f'<line class="plot-grid x-grid" x1="{x:.2f}" y1="{pad_top}" '
            f'x2="{x:.2f}" y2="{axis_y}" stroke="#d9e2ec" stroke-width="0.8"/>'
        )
        x_grid.append(
            f'<line class="x-axis-tick" x1="{x:.2f}" y1="{axis_y}" '
            f'x2="{x:.2f}" y2="{axis_y + 4}" stroke="#627d98" stroke-width="0.9"/>'
        )
        x_tick_labels.append(
            f'<text class="x-axis-label" x="{x:.2f}" y="{axis_y + 16}" '
            f'text-anchor="middle" font-size="9" fill="#486581">'
            f'{escape(_format_frequency_axis_tick(frequency_hz))}</text>'
        )
    y_grid = []
    for value in y_ticks:
        y = axis_y - (value - y_min) / (y_max - y_min) * (axis_y - pad_top)
        y_grid.append(
            f'<line class="plot-grid y-grid" x1="{pad_left}" y1="{y:.2f}" '
            f'x2="{width - pad_right}" y2="{y:.2f}" stroke="#d9e2ec" stroke-width="0.8"/>'
        )
        y_grid.append(
            f'<line class="y-axis-tick" x1="{pad_left - 4}" y1="{y:.2f}" '
            f'x2="{pad_left}" y2="{y:.2f}" stroke="#627d98" stroke-width="0.9"/>'
        )
        y_grid.append(
            f'<text class="y-axis-label" x="{pad_left - 7}" y="{y + 3.2:.2f}" '
            f'text-anchor="end" font-size="9" fill="#486581">'
            f'{escape(_format_linear_axis_tick(value, y_tick_step))}</text>'
        )
    legend_column_width = (width - pad_left - pad_right) / legend_columns
    legend_base_y = axis_y + 54
    legends = []
    for index, (name, color) in enumerate(legend_items):
        row, column = divmod(index, legend_columns)
        marker_x = pad_left + column * legend_column_width
        legend_y = legend_base_y + row * legend_row_height
        legends.append(
            f'<g class="legend-item" data-label="{escape(name, quote=True)}">'
            f'<line x1="{marker_x:.2f}" y1="{legend_y - 3:.2f}" '
            f'x2="{marker_x + 12:.2f}" y2="{legend_y - 3:.2f}" '
            f'stroke="{color}" stroke-width="1.8"/>'
            f'<text x="{marker_x + 17:.2f}" y="{legend_y:.2f}" font-size="10" fill="{color}">'
            f'{escape(_short_svg_legend_label(name))}</text></g>'
        )
    return (
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title, quote=True)}">'
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#f8fafc"/>'
        f'{"".join(x_grid)}{"".join(y_grid)}{"".join(x_tick_labels)}'
        f'<line x1="{pad_left}" y1="{axis_y}" x2="{width - pad_right}" y2="{axis_y}" stroke="#9fb3c8"/>'
        f'<line x1="{pad_left}" y1="{pad_top}" x2="{pad_left}" y2="{axis_y}" stroke="#9fb3c8"/>'
        f'<text x="{pad_left}" y="16" font-size="12" fill="#334e68">{escape(title)}</text>'
        f'<text class="x-axis-title" x="{(pad_left + width - pad_right) / 2:.2f}" y="{axis_y + 34}" '
        f'text-anchor="middle" font-size="10" fill="#334e68">Frequency / Hz (log)</text>'
        f'<text class="y-axis-title" x="{pad_left + 4}" y="{pad_top + 14}" font-size="10" fill="#334e68">{escape(y_label)}</text>'
        f'{"".join(polylines)}{circles}{"".join(measured_lines)}{"".join(measured_circles)}'
        f'{"".join(fit_lines)}{"".join(legends)}'
        '</svg>'
    )


def _log_frequency_ticks(x_min: float, x_max: float) -> list[float]:
    """Return conventional 1-2-5 log-frequency ticks within the visible domain."""
    ticks = [
        factor * 10.0**exponent
        for exponent in range(math.floor(x_min), math.ceil(x_max) + 1)
        for factor in (1.0, 2.0, 5.0)
        if x_min - 1e-12 <= math.log10(factor * 10.0**exponent) <= x_max + 1e-12
    ]
    # Very broad plots would otherwise turn into a forest of labels.  Retain
    # decade ticks first; the common measurement ranges keep all 1-2-5 ticks.
    if len(ticks) > 10:
        ticks = [tick for tick in ticks if math.isclose(math.log10(tick) % 1.0, 0.0, abs_tol=1e-10)]
    return ticks


def _nice_linear_axis_ticks(y_min: float, y_max: float) -> tuple[float, float, list[float], float]:
    """Expand a numeric domain to readable, evenly spaced linear ticks."""
    span = y_max - y_min
    raw_step = span / 5.0
    exponent = 10.0 ** math.floor(math.log10(raw_step))
    normalized = raw_step / exponent
    multiplier = next(value for value in (1.0, 2.0, 2.5, 5.0, 10.0) if normalized <= value)
    step = multiplier * exponent
    tick_min = math.floor(y_min / step) * step
    tick_max = math.ceil(y_max / step) * step
    count = int(round((tick_max - tick_min) / step)) + 1
    ticks = [tick_min + index * step for index in range(count)]
    return tick_min, tick_max, ticks, step


def _format_frequency_axis_tick(frequency_hz: float) -> str:
    if frequency_hz >= 1e6:
        return f"{frequency_hz / 1e6:g} M"
    if frequency_hz >= 1e3:
        return f"{frequency_hz / 1e3:g} k"
    return f"{frequency_hz:g}"


def _format_linear_axis_tick(value: float, step: float) -> str:
    if math.isclose(value, 0.0, abs_tol=abs(step) * 1e-9):
        value = 0.0
    precision = max(0, min(6, int(math.ceil(-math.log10(abs(step)))) + 1))
    return f"{value:.{precision}f}".rstrip("0").rstrip(".")


def _short_svg_legend_label(label: str, *, limit: int = 30) -> str:
    normalized = " ".join(label.split())
    return normalized if len(normalized) <= limit else normalized[: limit - 1] + "…"


def _finite_float(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if np.isfinite(numeric) else None


def _metric_label(metric: str) -> str:
    labels = {
        "frequency_estimate_hz": "频率 / Frequency",
        "frequency_error_ratio": "频率误差 / Frequency error",
        "voltage_vpp_v": "峰峰值 / Vpp",
        "duty_cycle": "占空比 / Duty",
        "thd_ratio": "总谐波失真 / THD",
        "fft.peak_frequency_hz": "FFT 主频 / FFT peak",
        "fft.peak_amplitude_v": "FFT 主幅度 / FFT peak amp",
        "fft.thd_ratio": "FFT 总谐波失真 / FFT THD",
    }
    if metric.startswith("fft.harmonic_") and metric.endswith("_amplitude_v"):
        order = metric.split("_")[1]
        return f"FFT H{order} 幅度 / FFT H{order} amplitude"
    return labels.get(metric, metric.replace("_", " "))


def _expectations_block(expectations: list[ReportExpectationRow]) -> str:
    if not expectations:
        return ""
    rows = "\n".join(_expectation_row(row) for row in expectations)
    return f"""<h2>预期 vs 实测 / Expected vs measured</h2>
<div class="table"><table class="expectations-table">
<thead><tr><th>步骤 / Step</th><th>类型 / Kind</th><th>指标 / Metric</th><th>预期 / Expected</th><th>实测 / Measured</th><th>状态 / Status</th><th>细节 / Details</th></tr></thead>
<tbody>
{rows}
</tbody>
</table>
</div>
"""


def _expectation_row(row: ReportExpectationRow) -> str:
    status = escape(row.status)
    return (
        f'<tr class="{status}">'
        f"<td>{escape(row.step_index)}</td>"
        f"<td>{escape(row.step_kind)}</td>"
        f"<td>{escape(row.metric)}</td>"
        f"<td>{escape(row.expected)}</td>"
        f"<td>{escape(row.measured)}</td>"
        f'<td class="{status}">{escape(row.status)}</td>'
        f"<td>{escape(row.details)}</td>"
        "</tr>"
    )


def _step_row(step: dict[str, Any], screenshot: ReportScreenshot | None = None) -> str:
    artifact = step.get("artifact", {}) if isinstance(step.get("artifact"), dict) else {}
    quality = artifact.get("quality", {}) if isinstance(artifact.get("quality"), dict) else {}
    expect = artifact.get("expect", {}) if isinstance(artifact.get("expect"), dict) else {}
    package = artifact.get("package", "")
    package_link = ""
    if package:
        package_link = f'<code>{escape(str(package))}</code>'
    screenshot_cell = ""
    if screenshot is not None:
        screenshot_cell = (
            f'<a href="{escape(screenshot.src, quote=True)}">'
            f'<img class="screenshot-thumb" src="{escape(screenshot.src, quote=True)}" '
            f'alt="Step {escape(screenshot.step_index, quote=True)} screenshot"></a>'
        )
    warnings = quality.get("warnings", [])
    if isinstance(warnings, list):
        warnings_text = " | ".join(str(item) for item in warnings)
    else:
        warnings_text = str(warnings)
    failures = expect.get("failures", [])
    if isinstance(failures, list):
        failures_text = " | ".join(str(item) for item in failures)
    else:
        failures_text = str(failures)
    status = str(step.get("status", ""))
    return (
        "<tr>"
        f"<td>{escape(str(step.get('index', '')))}</td>"
        f"<td>{escape(str(step.get('kind', '')))}</td>"
        f"<td class=\"{escape(status)}\">{escape(status)}</td>"
        f"<td>{package_link}</td>"
        f"<td>{screenshot_cell}</td>"
        f"<td>{escape(str(quality.get('status', '')))}</td>"
        f"<td>{escape(str(expect.get('status', '')))}</td>"
        f"<td>{escape(warnings_text)}</td>"
        f"<td>{escape(failures_text)}</td>"
        "</tr>"
    )


def _collect_expectation_rows(run: RunPackage) -> list[ReportExpectationRow]:
    rows: list[ReportExpectationRow] = []
    for step in run.steps:
        artifact = step.get("artifact", {}) if isinstance(step.get("artifact"), dict) else {}
        for expect_key, prefix in (("expect", ""), ("expect_fft", "fft.")):
            expect = artifact.get(expect_key, {}) if isinstance(artifact.get(expect_key), dict) else {}
            checks = expect.get("checks", {})
            if not isinstance(checks, dict):
                continue
            for metric, raw_check in checks.items():
                check = raw_check if isinstance(raw_check, dict) else {}
                status = str(check.get("status", "")) or str(expect.get("status", ""))
                rows.append(
                    ReportExpectationRow(
                        step_index=str(step.get("index", "")),
                        step_kind=str(step.get("kind", "")),
                        metric=f"{prefix}{metric}",
                        expected=_format_limits(check.get("limits", {})),
                        measured=_format_expect_measured(check),
                        status=status,
                        details=_format_expect_details(check),
                    )
                )
    return rows


def _collect_dmm_readings(run: RunPackage) -> list[ReportDmmReading]:
    readings: list[ReportDmmReading] = []
    for step in run.steps:
        if step.get("kind") != "dmm.read":
            continue
        artifact = step.get("artifact", {}) if isinstance(step.get("artifact"), dict) else {}
        reading = artifact.get("dmm_reading", {}) if isinstance(artifact.get("dmm_reading"), dict) else {}
        expect = artifact.get("expect", {}) if isinstance(artifact.get("expect"), dict) else {}
        value_check = _dmm_value_check(expect)
        unit = str(reading.get("unit", ""))
        expected = _format_limits(value_check.get("limits", {})) if value_check else ""
        if expected and unit:
            expected = f"{expected} {unit}"
        status = str(value_check.get("status", "")) if value_check else str(expect.get("status", "") or step.get("status", ""))
        details = _format_expect_details(value_check) if value_check else _format_dmm_failures(expect)
        step_name = _step_display_name(step)
        readings.append(
            ReportDmmReading(
                step_index=str(step.get("index", "")),
                step_name=step_name,
                function=str(reading.get("function") or _step_field(step, "function") or ""),
                value=_format_plain(reading.get("value")),
                unit=unit,
                expected=expected,
                status=status,
                details=details,
            )
        )
    return readings


def _collect_sweep_rows(run: RunPackage) -> list[ReportSweepRow]:
    rows: list[ReportSweepRow] = []
    for step in run.steps:
        if step.get("kind") != "scope.capture":
            continue
        fields = step.get("fields", {}) if isinstance(step.get("fields"), dict) else {}
        artifact = step.get("artifact", {}) if isinstance(step.get("artifact"), dict) else {}
        quality = artifact.get("quality", {}) if isinstance(artifact.get("quality"), dict) else {}
        expect = artifact.get("expect", {}) if isinstance(artifact.get("expect"), dict) else {}
        expect_fft = artifact.get("expect_fft", {}) if isinstance(artifact.get("expect_fft"), dict) else {}
        fft = artifact.get("fft", {}) if isinstance(artifact.get("fft"), dict) else {}
        if not (quality or expect or expect_fft):
            continue
        rows.append(
            ReportSweepRow(
                step_index=str(step.get("index", "")),
                label=str(fields.get("label") or _step_display_name(step)),
                status=str(step.get("status", "")),
                quality_status=str(quality.get("status", "")),
                expect_status=str(expect.get("status", "")),
                fft_status=str(expect_fft.get("status", "")),
                frequency_hz=_format_metric(quality.get("frequency_estimate_hz"), "Hz"),
                vpp_v=_format_metric(quality.get("voltage_vpp_v"), "V"),
                fft_peak_hz=_format_metric(fft.get("peak_frequency_hz"), "Hz"),
                fft_peak_amplitude_v=_format_metric(fft.get("peak_amplitude_v"), "V"),
                thd_ratio=_format_percent(fft.get("thd_ratio")),
            )
        )
    if len(rows) < 2 and not any("sweep" in row.label.lower() for row in rows):
        return []
    return rows


def _dmm_value_check(expect: dict[str, Any]) -> dict[str, Any]:
    checks = expect.get("checks", {})
    if not isinstance(checks, dict):
        return {}
    value_check = checks.get("value", {})
    return value_check if isinstance(value_check, dict) else {}


def _format_dmm_failures(expect: dict[str, Any]) -> str:
    failures = expect.get("failures", [])
    if isinstance(failures, list):
        return " | ".join(str(item) for item in failures)
    return str(failures) if failures else ""


def _step_display_name(step: dict[str, Any]) -> str:
    for key in ("name", "label", "kind"):
        value = step.get(key)
        if value:
            return str(value)
    return "step"


def _step_field(step: dict[str, Any], key: str) -> Any:
    fields = step.get("fields", {})
    if isinstance(fields, dict):
        return fields.get(key)
    return None


def _format_limits(limits: Any) -> str:
    if not isinstance(limits, dict):
        return ""
    minimum = limits.get("min")
    maximum = limits.get("max")
    if minimum is not None and maximum is not None:
        return f"{_format_plain(minimum)}..{_format_plain(maximum)}"
    if minimum is not None:
        return f">= {_format_plain(minimum)}"
    if maximum is not None:
        return f"<= {_format_plain(maximum)}"
    return ""


def _format_expect_measured(check: dict[str, Any]) -> str:
    if "value" in check:
        return _format_plain(check.get("value"))
    reason = check.get("reason")
    if reason:
        return str(reason)
    return ""


def _format_expect_details(check: dict[str, Any]) -> str:
    reasons = check.get("reasons")
    if isinstance(reasons, list):
        return " | ".join(str(item) for item in reasons)
    reason = check.get("reason")
    if reason:
        return str(reason)
    return ""


def _build_report_summary(
    run: RunPackage, screenshots: list[ReportScreenshot], signals: list[ReportSignalSummary]
) -> ReportSummary:
    experiment = run.run.get("experiment", {}) if isinstance(run.run.get("experiment"), dict) else {}
    restore = run.run.get("restore", {}) if isinstance(run.run.get("restore"), dict) else {}
    failed_steps = 0
    packages = {reference.package for reference in _capture_references(run)}
    warning_messages: set[str] = set()
    failed_expect_count = 0
    for step in run.steps:
        if step.get("status") == "failed":
            failed_steps += 1
        artifact = step.get("artifact", {}) if isinstance(step.get("artifact"), dict) else {}
        quality = artifact.get("quality", {}) if isinstance(artifact.get("quality"), dict) else {}
        warnings = quality.get("warnings", [])
        if isinstance(warnings, list):
            warning_messages.update(str(item) for item in warnings if item)
        elif warnings:
            warning_messages.add(str(warnings))
        expect = artifact.get("expect", {}) if isinstance(artifact.get("expect"), dict) else {}
        expect_fft = artifact.get("expect_fft", {}) if isinstance(artifact.get("expect_fft"), dict) else {}
        if expect.get("status") == "failed":
            failed_expect_count += 1
        if expect_fft.get("status") == "failed":
            failed_expect_count += 1
    for signal in signals:
        if signal.warnings:
            warning_messages.update(item for item in signal.warnings.split(" | ") if item)
    primary = signals[0] if signals else None
    return ReportSummary(
        status=run.status,
        experiment_label=str(experiment.get("label") or experiment.get("name") or run.path.name),
        total_steps=len(run.steps),
        failed_steps=failed_steps,
        capture_count=len(packages),
        warning_count=len(warning_messages),
        failed_expect_count=failed_expect_count,
        screenshot_count=len(screenshots),
        restore_status=str(restore.get("status", "not configured")) if restore else "not configured",
        primary_frequency=primary.frequency_hz if primary is not None else "",
        primary_vpp=primary.vpp_v if primary is not None else "",
    )


def _build_evidence_summary(
    run: RunPackage,
    expectations: list[ReportExpectationRow],
    dmm_readings: list[ReportDmmReading],
    screenshots: list[ReportScreenshot],
    waveform_previews: list[ReportWaveformPreview],
) -> ReportEvidenceSummary:
    packages = {reference.package for reference in _capture_references(run)}
    return ReportEvidenceSummary(
        source_step_count=sum(1 for step in run.steps if str(step.get("kind", "")).startswith("source.")),
        scope_capture_count=sum(1 for step in run.steps if step.get("kind") == "scope.capture"),
        dmm_reading_count=len(dmm_readings),
        failed_expectation_count=sum(1 for row in expectations if row.status == "failed"),
        run_json_available=run.run_json_path.exists(),
        summary_csv_available=run.summary_csv_path is not None and run.summary_csv_path.exists(),
        capture_package_count=len(packages),
        screenshot_count=len(screenshots),
        waveform_preview_count=len(waveform_previews),
    )


def _collect_artifact_links(
    run: RunPackage, output_dir: Path, screenshots: list[ReportScreenshot]
) -> list[ReportArtifactLink]:
    links = [
        ReportArtifactLink(
            step_index="-",
            kind="运行记录 / Run JSON",
            label="run.json",
            href=_relative_url(run.run_json_path, output_dir),
            status=_availability_text(run.run_json_path.exists()),
        )
    ]
    if run.summary_csv_path is not None:
        links.append(
            ReportArtifactLink(
                step_index="-",
                kind="摘要 CSV / Summary CSV",
                label="summary.csv",
                href=_relative_url(run.summary_csv_path, output_dir),
                status=_availability_text(run.summary_csv_path.exists()),
            )
        )
    if run.frequency_response_csv_path is not None:
        links.append(
            ReportArtifactLink(
                step_index="-",
                kind="频率响应 CSV / Frequency response CSV",
                label="frequency_response.csv",
                href=_relative_url(run.frequency_response_csv_path, output_dir),
                status=_availability_text(run.frequency_response_csv_path.exists()),
            )
        )
    if run.frequency_response_fit_path is not None:
        links.append(
            ReportArtifactLink(
                step_index="-",
                kind="频响拟合 JSON / Frequency response fit JSON",
                label="frequency_response_fit.json",
                href=_relative_url(run.frequency_response_fit_path, output_dir),
                status=_availability_text(run.frequency_response_fit_path.exists()),
            )
        )
    if run.frequency_response_calibration_csv_path is not None:
        links.append(
            ReportArtifactLink(
                step_index="-",
                kind="二维校准 CSV / 2D calibration CSV",
                label="frequency_response_calibration.csv",
                href=_relative_url(run.frequency_response_calibration_csv_path, output_dir),
                status=_availability_text(run.frequency_response_calibration_csv_path.exists()),
            )
        )
    if run.frequency_response_calibration_path is not None:
        links.append(
            ReportArtifactLink(
                step_index="-",
                kind="二维校准 JSON / 2D calibration JSON",
                label="frequency_response_calibration.json",
                href=_relative_url(run.frequency_response_calibration_path, output_dir),
                status=_availability_text(run.frequency_response_calibration_path.exists()),
            )
        )
    for response in run.frequency_responses:
        prefix = f"[{response.label}] " if len(run.frequency_responses) > 1 else ""
        if response.baseline_path is not None:
            links.append(
                ReportArtifactLink(
                    step_index=str(response.step_index) if response.step_index is not None else "-",
                    kind=f"{prefix}软件基线 JSON / Software baseline JSON",
                    label="frequency_response_baseline.json",
                    href=_relative_url(response.baseline_path, output_dir),
                    status=_availability_text(response.baseline_path.exists()),
                )
            )
        fixed_point = response.calibration.get("fixed_point", {}) if response.calibration else {}
        outputs = fixed_point.get("outputs", {}) if isinstance(fixed_point, dict) else {}
        if isinstance(outputs, dict):
            for name, raw_path in outputs.items():
                if not isinstance(raw_path, str):
                    continue
                path = Path(raw_path)
                links.append(
                    ReportArtifactLink(
                        step_index=str(response.step_index) if response.step_index is not None else "-",
                        kind=f"{prefix}定点 {name.upper()} / Fixed-point {name.upper()}",
                        label=path.name,
                        href=_relative_url(path, output_dir),
                        status=_availability_text(path.exists()),
                    )
                )
    screenshots_by_package = {item.package: item for item in screenshots}
    for reference in _capture_references(run):
        package_dir = _resolve_artifact_path(run.path, reference.package)
        links.append(
            ReportArtifactLink(
                step_index=reference.step_index,
                kind="采集包 / Capture package",
                label=reference.package,
                href=_relative_url(package_dir, output_dir),
                status=_availability_text(package_dir.exists()),
            )
        )
        screenshot = screenshots_by_package.get(reference.package)
        if screenshot is not None:
            links.append(
                ReportArtifactLink(
                    step_index=reference.step_index,
                    kind="截图 / Screenshot",
                    label=Path(screenshot.src).name,
                    href=screenshot.src,
                    status=_availability_text(screenshot.path.exists()),
                )
            )
        metadata = _read_capture_metadata(package_dir, reference.metadata)
        for channel, npy_text, _summary in _metadata_waveform_npy_files(metadata):
            if not npy_text:
                continue
            npy_path = _resolve_capture_file_path(run.path, package_dir, str(npy_text))
            if not npy_path.exists():
                continue
            npy_name = Path(str(npy_text).replace("\\", "/")).name
            links.append(
                ReportArtifactLink(
                    step_index=reference.step_index,
                    kind="波形原始数据 / Waveform raw artifact",
                    label=f"ch{channel} {npy_name}",
                    href=_relative_url(npy_path, output_dir),
                    status=_availability_text(True),
                )
            )
    return links


def _screenshots_block(screenshots: list[ReportScreenshot]) -> str:
    if not screenshots:
        return ""
    cards = "\n".join(_screenshot_card(item) for item in screenshots)
    return f"""<h2>截图 / Screenshots</h2>
<div class="screenshot-grid">
{cards}
</div>
"""


def _waveform_previews_block(previews: list[ReportWaveformPreview]) -> str:
    if not previews:
        return ""
    cards = "\n".join(_waveform_preview_card(item) for item in previews)
    return f"""<h2>波形预览 / Waveform previews</h2>
<div class="waveform-grid">
{cards}
</div>
"""


def _waveform_preview_card(preview: ReportWaveformPreview) -> str:
    warning = f'<p class="warning">{escape(preview.warning)}</p>' if preview.warning else ""
    return (
        '<figure class="card waveform-card">'
        f"<figcaption>{escape(preview.title)}<br><code>{escape(preview.package)}</code></figcaption>"
        f"{preview.svg}"
        f"{warning}"
        "</figure>"
    )


def _signals_block(signals: list[ReportSignalSummary]) -> str:
    if not signals:
        return ""
    rows = "\n".join(_signal_row(item) for item in signals)
    return f"""<h2>信号分析 / Signal analysis</h2>
<div class="table"><table>
<thead><tr><th>步骤 / Step</th><th>通道 / Channel</th><th>采样点 / Samples</th><th>频率 / Frequency</th><th>Vpp</th><th>RMS</th><th>Mean</th><th>Duty</th><th>Rise</th><th>Fall</th><th>警告 / Warnings</th></tr></thead>
<tbody>
{rows}
</tbody>
</table>
</div>
"""


def _signal_row(signal: ReportSignalSummary) -> str:
    return (
        "<tr>"
        f"<td>{escape(signal.step_index)}</td>"
        f"<td>{escape(signal.channel)}</td>"
        f"<td>{escape(signal.samples)}</td>"
        f"<td>{escape(signal.frequency_hz)}</td>"
        f"<td>{escape(signal.vpp_v)}</td>"
        f"<td>{escape(signal.rms_v)}</td>"
        f"<td>{escape(signal.mean_v)}</td>"
        f"<td>{escape(signal.duty_cycle)}</td>"
        f"<td>{escape(signal.rise_time_s)}</td>"
        f"<td>{escape(signal.fall_time_s)}</td>"
        f"<td>{escape(signal.warnings)}</td>"
        "</tr>"
    )


def _screenshot_card(screenshot: ReportScreenshot) -> str:
    step = escape(screenshot.step_index)
    src = escape(screenshot.src, quote=True)
    package = escape(screenshot.package)
    return (
        '<figure class="card screenshot-card">'
        f'<a href="{src}"><img src="{src}" alt="Step {step} screenshot"></a>'
        f'<figcaption>Step {step}: <code>{package}</code></figcaption>'
        '</figure>'
    )


def _collect_waveform_previews(run: RunPackage) -> list[ReportWaveformPreview]:
    previews: list[ReportWaveformPreview] = []
    for step in run.steps:
        artifact = step.get("artifact", {}) if isinstance(step.get("artifact"), dict) else {}
        package_text = artifact.get("package")
        if not package_text:
            continue
        package_dir = _resolve_artifact_path(run.path, str(package_text))
        metadata = _read_capture_metadata(package_dir, artifact.get("metadata"))
        for channel, npy_text, summary in _metadata_waveform_npy_files(metadata):
            title = _waveform_preview_title(step.get("index", ""), channel, summary)
            if not npy_text:
                previews.append(
                    ReportWaveformPreview(
                        step_index=str(step.get("index", "")),
                        package=str(package_text),
                        channel=channel,
                        title=title,
                        svg="",
                        warning="waveform preview unavailable: missing npy artifact",
                    )
                )
                continue
            npy_path = _resolve_capture_file_path(run.path, package_dir, str(npy_text))
            svg = ""
            warning = ""
            try:
                waveform = np.load(npy_path)
                svg = _waveform_svg(waveform)
            except Exception as exc:  # noqa: BLE001 - report generation should survive bad artifacts
                warning = f"waveform preview unavailable: {type(exc).__name__}: {exc}"
            previews.append(
                ReportWaveformPreview(
                    step_index=str(step.get("index", "")),
                    package=str(package_text),
                    channel=channel,
                    title=title,
                    svg=svg,
                    warning=warning,
                )
            )
    return previews


def _metadata_waveform_npy_files(metadata: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    channels = metadata.get("channels")
    files = metadata.get("files")
    if isinstance(channels, dict):
        file_map = files if isinstance(files, dict) else {}
        result: list[tuple[str, str, dict[str, Any]]] = []
        for raw_channel, payload in sorted(channels.items(), key=lambda item: int(item[0])):
            channel_payload = payload if isinstance(payload, dict) else {}
            summary = channel_payload.get("summary") if isinstance(channel_payload.get("summary"), dict) else {}
            channel_files = file_map.get(str(raw_channel), {}) if isinstance(file_map, dict) else {}
            npy_text = channel_files.get("npy", "") if isinstance(channel_files, dict) else ""
            result.append((str(raw_channel), str(npy_text) if npy_text else "", summary))
        return result
    waveform = metadata.get("waveform")
    if isinstance(waveform, dict):
        summary = waveform.get("summary") if isinstance(waveform.get("summary"), dict) else {}
        operation = metadata.get("operation") if isinstance(metadata.get("operation"), dict) else {}
        channel = summary.get("channel", operation.get("channel", ""))
        file_map = files if isinstance(files, dict) else {}
        npy_text = file_map.get("npy", "") if isinstance(file_map, dict) else ""
        return [(str(channel), str(npy_text) if npy_text else "", summary)]
    return []


def _waveform_preview_title(step_index: Any, channel: str, summary: dict[str, Any]) -> str:
    parts = [f"Step {step_index} ch{channel}"]
    frequency = _format_metric(summary.get("frequency_estimate_hz"), "Hz")
    vpp = _format_metric(summary.get("voltage_vpp_v"), "Vpp")
    if frequency:
        parts.append(frequency)
    if vpp:
        parts.append(vpp)
    return " — ".join(parts)


def _waveform_svg(waveform: Any, *, max_points: int = 600) -> str:
    data = np.asarray(waveform, dtype=float)
    if data.ndim != 2 or data.shape[1] < 2:
        raise ValueError("expected an Nx2 waveform array")
    finite = np.isfinite(data[:, 0]) & np.isfinite(data[:, 1])
    data = data[finite]
    if data.shape[0] < 2:
        raise ValueError("need at least two finite waveform samples")
    if data.shape[0] > max_points:
        indices = np.linspace(0, data.shape[0] - 1, max_points).astype(int)
        data = data[indices]
    x = data[:, 0]
    y = data[:, 1]
    if float(np.max(x)) == float(np.min(x)):
        x = np.arange(data.shape[0], dtype=float)
    width = 640
    height = 180
    pad = 24
    x_min = float(np.min(x))
    x_max = float(np.max(x))
    y_min = float(np.min(y))
    y_max = float(np.max(y))
    if y_max == y_min:
        y_min -= 0.5
        y_max += 0.5
    x_scale = (width - 2 * pad) / (x_max - x_min)
    y_scale = (height - 2 * pad) / (y_max - y_min)
    points = []
    for x_value, y_value in zip(x, y):
        px = pad + (float(x_value) - x_min) * x_scale
        py = height - pad - (float(y_value) - y_min) * y_scale
        points.append(f"{px:.2f},{py:.2f}")
    zero_line = ""
    if y_min <= 0 <= y_max:
        zero_y = height - pad - (0 - y_min) * y_scale
        zero_line = f'<line x1="{pad}" y1="{zero_y:.2f}" x2="{width - pad}" y2="{zero_y:.2f}" stroke="#bcccdc" stroke-width="1"/>'
    return (
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="waveform preview">'
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#f8fafc"/>'
        f'{zero_line}'
        f'<polyline points="{" ".join(points)}" fill="none" stroke="#2563eb" stroke-width="1.5"/>'
        f'<text x="{pad}" y="16" font-size="12" fill="#627d98">'
        f't={_format_axis_value(x_min)}..{_format_axis_value(x_max)} s, '
        f'v={_format_axis_value(y_min)}..{_format_axis_value(y_max)} V'
        f'</text>'
        f'</svg>'
    )


def _format_axis_value(value: float) -> str:
    return f"{value:.4g}"


def _resolve_capture_file_path(run_path: Path, package_dir: Path, file_text: str) -> Path:
    candidate = _resolve_artifact_path(run_path, file_text)
    if candidate.exists() or Path(file_text).is_absolute():
        return candidate
    return package_dir / file_text.replace("\\", "/")


def _collect_signal_summaries(run: RunPackage) -> list[ReportSignalSummary]:
    summaries: list[ReportSignalSummary] = []
    for step in run.steps:
        artifact = step.get("artifact", {}) if isinstance(step.get("artifact"), dict) else {}
        package_text = artifact.get("package")
        if not package_text:
            continue
        package_dir = _resolve_artifact_path(run.path, str(package_text))
        metadata = _read_capture_metadata(package_dir, artifact.get("metadata"))
        for summary in _metadata_signal_summaries(metadata):
            warnings = summary.get("quality_warnings", [])
            if isinstance(warnings, list):
                warnings_text = " | ".join(str(item) for item in warnings)
            else:
                warnings_text = str(warnings) if warnings else ""
            summaries.append(
                ReportSignalSummary(
                    step_index=str(step.get("index", "")),
                    package=str(package_text),
                    channel=_format_plain(summary.get("channel")),
                    samples=_format_plain(summary.get("samples")),
                    frequency_hz=_format_metric(summary.get("frequency_estimate_hz"), "Hz"),
                    vpp_v=_format_metric(summary.get("voltage_vpp_v"), "V"),
                    rms_v=_format_metric(summary.get("voltage_rms_v"), "V"),
                    mean_v=_format_metric(summary.get("voltage_mean_v"), "V"),
                    duty_cycle=_format_percent(summary.get("duty_cycle")),
                    rise_time_s=_format_metric(summary.get("rise_time_s"), "s"),
                    fall_time_s=_format_metric(summary.get("fall_time_s"), "s"),
                    warnings=warnings_text,
                )
            )
    return summaries


def _metadata_signal_summaries(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    channels = metadata.get("channels")
    if isinstance(channels, dict):
        result: list[dict[str, Any]] = []
        for _raw_channel, payload in sorted(channels.items(), key=lambda item: int(item[0])):
            channel_payload = payload if isinstance(payload, dict) else {}
            summary = channel_payload.get("summary")
            if isinstance(summary, dict):
                result.append(summary)
        return result
    waveform = metadata.get("waveform")
    if isinstance(waveform, dict) and isinstance(waveform.get("summary"), dict):
        return [waveform["summary"]]
    return []


def _collect_screenshots(run: RunPackage, output_dir: Path) -> list[ReportScreenshot]:
    screenshots: list[ReportScreenshot] = []
    for reference in _capture_references(run):
        package_dir = _resolve_artifact_path(run.path, reference.package)
        metadata = _read_capture_metadata(package_dir, reference.metadata)
        screenshot_text = _metadata_screenshot_path(metadata)
        if not screenshot_text:
            continue
        screenshot_path = _resolve_artifact_path(run.path, screenshot_text)
        if not screenshot_path.exists():
            screenshot_path = package_dir / "screenshot.png"
        if not screenshot_path.exists():
            continue
        screenshots.append(
            ReportScreenshot(
                step_index=reference.step_index,
                package=reference.package,
                path=screenshot_path,
                src=_relative_url(screenshot_path, output_dir),
            )
        )
    return screenshots


def _capture_references(run: RunPackage) -> list[ReportCaptureReference]:
    references: list[ReportCaptureReference] = []
    seen: set[str] = set()

    def add(step_index: str, package: Any, metadata: Any) -> None:
        package_text = str(package or "").strip()
        if not package_text or package_text in seen:
            return
        seen.add(package_text)
        references.append(
            ReportCaptureReference(
                step_index=step_index,
                package=package_text,
                metadata=str(metadata or ""),
            )
        )

    for step in run.steps:
        artifact = step.get("artifact", {}) if isinstance(step.get("artifact"), dict) else {}
        add(str(step.get("index", "")), artifact.get("package"), artifact.get("metadata"))
        response = artifact.get("frequency_response", {})
        captures = response.get("captures", []) if isinstance(response, dict) else []
        if isinstance(captures, list):
            for capture in captures:
                if isinstance(capture, dict):
                    point_index = str(capture.get("index", ""))
                    add(
                        f"frequency response {point_index}",
                        capture.get("package"),
                        capture.get("metadata"),
                    )

    for row in run.frequency_response_rows:
        point_index = str(row.get("index", ""))
        add(
            f"frequency response {point_index}",
            row.get("capture_package"),
            row.get("metadata_path"),
        )
    return references


def _read_capture_metadata(package_dir: Path, metadata_text: Any) -> dict[str, Any]:
    metadata_path = _resolve_artifact_path(package_dir, str(metadata_text)) if metadata_text else package_dir / "metadata.json"
    if not metadata_path.exists():
        metadata_path = package_dir / "metadata.json"
    try:
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _metadata_screenshot_path(metadata: dict[str, Any]) -> str | None:
    files = metadata.get("files", {})
    if isinstance(files, dict):
        screenshot = files.get("screenshot")
        if screenshot:
            return str(screenshot)
    return None


def _format_plain(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def _format_metric(value: Any, unit: str) -> str:
    if value is None:
        return ""
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{numeric:.6g} {unit}"


def _format_percent(value: Any) -> str:
    if value is None:
        return ""
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{numeric * 100:.3g}%"


def _resolve_artifact_path(run_path: Path, artifact_path: str) -> Path:
    normalized = artifact_path.replace("\\", "/")
    path = Path(normalized)
    if path.is_absolute():
        return path
    root = _project_root_from_run_path(run_path)
    return root / path


def _project_root_from_run_path(run_path: Path) -> Path:
    parts = run_path.parts
    if len(parts) >= 3 and parts[-3:-1] == ("data", "runs"):
        return Path(*parts[:-3]) if len(parts[:-3]) > 0 else Path(".")
    return run_path.parent


def _relative_url(path: Path, output_dir: Path) -> str:
    try:
        relative = os.path.relpath(path, start=output_dir)
    except ValueError:
        relative = str(path)
    return relative.replace(os.sep, "/")
