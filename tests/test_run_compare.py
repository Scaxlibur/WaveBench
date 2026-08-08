from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from wavebench.services.run_compare import (
    COMPARE_SCHEMA,
    RunCompareError,
    compare_run_packages,
)


def _write_run(
    root: Path,
    name: str,
    rows: list[dict[str, object]],
    *,
    label: str | None = None,
    reference_channel: int | None = None,
    response_channel: int | None = None,
    reference_plane: str | None = None,
    plan_hash: str | None = None,
) -> Path:
    run = root / name
    run.mkdir(parents=True)
    step_response: dict[str, object] = {"label": label or "response"}
    if reference_channel is not None:
        step_response["reference_channel"] = reference_channel
    if response_channel is not None:
        step_response["response_channel"] = response_channel
    if reference_plane is not None:
        step_response["reference_plane"] = reference_plane
    if plan_hash is not None:
        step_response["plan_hash"] = plan_hash
    (run / "run.json").write_text(
        json.dumps(
            {
                "status": "ok",
                "steps": [
                    {
                        "index": 0,
                        "kind": "sweep.frequency_response",
                        "fields": {"label": label or "response"},
                        "artifact": {"frequency_response": step_response},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    fields = sorted({str(key) for row in rows for key in row})
    # Keep the normal CSV order readable while allowing test-only columns.
    preferred = [
        "index",
        "case_id",
        "requested_frequency_hz",
        "requested_vpp",
        "requested_source_vpp",
        "gain_db",
        "gain_db_corrected",
        "phase_wrapped_deg",
        "phase_unwrapped_deg",
        "status",
        "warnings",
        "error",
    ]
    fields = [field for field in preferred if field in fields] + [
        field for field in fields if field not in preferred
    ]
    lines = [",".join(fields)]
    import csv
    import io

    for row in rows:
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="")
        writer.writerow({field: row.get(field, "") for field in fields})
        lines.append(buffer.getvalue())
    (run / "frequency_response.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return run


class RunCompareTests(unittest.TestCase):
    def test_legacy_rows_align_by_frequency_and_vpp_and_write_json(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = _write_run(
                root,
                "reference",
                [
                    {"index": 0, "requested_frequency_hz": 100, "requested_vpp": 0.1, "gain_db": 1, "phase_wrapped_deg": 179, "status": "ok"},
                    {"index": 1, "requested_frequency_hz": 200, "requested_vpp": 0.1, "gain_db": 2, "phase_wrapped_deg": -179, "status": "ok"},
                ],
            )
            candidate = _write_run(
                root,
                "candidate",
                [
                    {"index": 0, "requested_frequency_hz": 100, "requested_vpp": 0.1, "gain_db": 1.25, "phase_wrapped_deg": -179, "status": "ok"},
                    {"index": 1, "requested_frequency_hz": 300, "requested_vpp": 0.1, "gain_db": 3, "phase_wrapped_deg": 0, "status": "ok"},
                ],
            )
            output = root / "compare.json"

            result = compare_run_packages([reference, candidate], output_path=output)

            self.assertEqual(result["schema_version"], COMPARE_SCHEMA)
            comparison = result["comparisons"][0]
            self.assertEqual(comparison["status"], "partial")
            self.assertEqual(comparison["summary"]["matched"], 1)
            self.assertEqual(comparison["points"][0]["identity"], "frequency_vpp")
            self.assertAlmostEqual(comparison["points"][0]["delta"]["gain_db"], 0.25)
            # Wrapped phase compares by the shortest signed angular distance.
            self.assertAlmostEqual(comparison["points"][0]["delta"]["phase_deg"], 2.0)
            self.assertTrue(output.exists())
            persisted = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(persisted["schema_version"], COMPARE_SCHEMA)

    def test_case_id_is_preferred_over_frequency_fallback(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = _write_run(
                root,
                "reference",
                [{"index": 0, "case_id": "case-shared", "requested_frequency_hz": 100, "requested_vpp": 0.1, "gain_db": 1, "status": "ok"}],
            )
            candidate = _write_run(
                root,
                "candidate",
                [{"index": 0, "case_id": "case-shared", "requested_frequency_hz": 101, "requested_vpp": 0.2, "gain_db": 1.5, "status": "ok"}],
            )

            comparison = compare_run_packages([reference, candidate])["comparisons"][0]

            self.assertEqual(comparison["status"], "ok")
            self.assertEqual(comparison["points"][0]["identity"], "case_id")
            self.assertEqual(comparison["points"][0]["case_id"], "case-shared")

    def test_tolerance_marks_out_of_tolerance_without_changing_delta(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            rows = [{"index": 0, "case_id": "case-1", "requested_frequency_hz": 100, "requested_vpp": 0.1, "gain_db": 1, "status": "ok"}]
            reference = _write_run(root, "reference", rows)
            candidate = _write_run(root, "candidate", [{**rows[0], "gain_db": 1.2}])

            comparison = compare_run_packages(
                [reference, candidate], gain_tolerance_db=0.1
            )["comparisons"][0]

            self.assertEqual(comparison["status"], "out_of_tolerance")
            self.assertEqual(comparison["summary"]["out_of_tolerance"], 1)
            self.assertFalse(comparison["summary"]["within_tolerance"])
            self.assertAlmostEqual(comparison["points"][0]["delta"]["gain_db"], 0.2)

    def test_duplicate_identity_is_reported_and_not_silently_paired(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            duplicate_rows = [
                {"index": 0, "requested_frequency_hz": 100, "requested_vpp": 0.1, "gain_db": 1, "status": "ok"},
                {"index": 1, "requested_frequency_hz": 100, "requested_vpp": 0.1, "gain_db": 2, "status": "ok"},
            ]
            reference = _write_run(root, "reference", duplicate_rows)
            candidate = _write_run(root, "candidate", [duplicate_rows[0]])

            comparison = compare_run_packages([reference, candidate])["comparisons"][0]

            self.assertEqual(comparison["status"], "duplicate")
            self.assertIn("frequency=100|requested_vpp=0.1", comparison["duplicates"]["reference"])
            self.assertEqual(comparison["summary"]["matched"], 0)

    def test_channel_and_plane_mismatch_is_incompatible(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            rows = [{"index": 0, "requested_frequency_hz": 100, "requested_vpp": 0.1, "gain_db": 1, "status": "ok"}]
            reference = _write_run(root, "reference", rows, reference_channel=1, response_channel=2, reference_plane="scope_input", plan_hash="same")
            candidate = _write_run(root, "candidate", rows, reference_channel=2, response_channel=3, reference_plane="load", plan_hash="other")

            comparison = compare_run_packages([reference, candidate])["comparisons"][0]

            self.assertEqual(comparison["status"], "incompatible")
            fields = {item["field"] for item in comparison["incompatible"]}
            self.assertEqual(fields, {"reference_channel", "response_channel", "reference_plane", "plan_hash"})

    def test_multi_response_requires_selector_and_selector_can_be_parallel(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            rows = [{"index": 0, "requested_frequency_hz": 100, "requested_vpp": 0.1, "gain_db": 1, "status": "ok"}]
            first = _write_run(root, "first", rows, label="low")
            second = _write_run(root, "second", rows, label="high")
            for run, label in ((first, "low"), (second, "high")):
                response_dir = run / "frequency_response" / label
                response_dir.mkdir(parents=True)
                (response_dir / "frequency_response.csv").write_text(
                    (run / "frequency_response.csv").read_text(encoding="utf-8"), encoding="utf-8"
                )
                (run / "frequency_response.csv").unlink()
                (run / "frequency_responses.json").write_text(
                    json.dumps({"schema_version": 1, "responses": [{"step_index": 0, "label": label, "directory": f"frequency_response/{label}"}]}),
                    encoding="utf-8",
                )

            result = compare_run_packages([first, second], response_labels=["low", "high"])
            self.assertEqual(result["comparisons"][0]["status"], "ok")

            with self.assertRaises(RunCompareError):
                compare_run_packages([first])


if __name__ == "__main__":
    unittest.main()
