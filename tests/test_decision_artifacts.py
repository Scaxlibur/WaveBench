"""decision artifact（`wavebench.decision.v1`）的离线契约测试。"""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from wavebench.services import decision_artifacts as artifacts


def _artifact(**overrides) -> artifacts.DecisionArtifact:
    kwargs = {
        "status": "refused",
        "requested_model": "jev-1.13.0",
        "reason": "external_state_consent_not_granted",
        "consent": {"schema": "wavebench.advisor_consent.v1", "granted": False},
        "state": {"fields": {"run_status": "failed"}},
        "thresholds": artifacts.ThresholdPolicy().as_record(),
    }
    kwargs.update(overrides)
    return artifacts.DecisionArtifact(**kwargs)


class DecisionArtifactTests(unittest.TestCase):
    def test_threshold_policy_bounds(self):
        self.assertEqual(artifacts.ThresholdPolicy().as_record(), {"accept": 0.60, "review": 0.35})
        with self.assertRaises(artifacts.DecisionArtifactError):
            artifacts.ThresholdPolicy(accept=0.3, review=0.5)
        with self.assertRaises(artifacts.DecisionArtifactError):
            artifacts.ThresholdPolicy(accept=1.5)

    def test_artifact_rejects_unknown_status_and_silent_downgrade(self):
        with self.assertRaises(artifacts.DecisionArtifactError):
            _artifact(status="done")
        with self.assertRaises(artifacts.DecisionArtifactError):
            _artifact(advisory_only=False)
        with self.assertRaises(artifacts.DecisionArtifactError):
            _artifact(duration_ms=-1)

    def test_artifact_payload_keeps_schema_and_recommendations(self):
        payload = _artifact(
            status="ok",
            reason=None,
            reported_model="jev-1.13.0",
            duration_ms=214,
            recommendations=({"action": "run check", "probability": 0.81},),
        ).as_dict()

        self.assertEqual(payload["schema"], "wavebench.decision.v1")
        self.assertTrue(payload["advisory_only"])
        self.assertEqual(payload["advisory"]["requested_model"], "jev-1.13.0")
        self.assertEqual(payload["advisory"]["reported_model"], "jev-1.13.0")
        self.assertEqual(payload["advisory"]["duration_ms"], 214)
        self.assertEqual(payload["recommendations"][0]["action"], "run check")

    def test_artifact_path_layout_and_token_safety(self):
        run_dir = Path("data") / "runs" / "20260929_0740_loop_gain"
        path = artifacts.artifact_path(
            run_dir, advisor_id="typesafe.jev", timestamp="20260929T104827Z"
        )

        self.assertEqual(
            path.as_posix(),
            "data/runs/20260929_0740_loop_gain/decisions/20260929T104827Z-typesafe.jev.json",
        )
        with self.assertRaises(artifacts.DecisionArtifactError):
            artifacts.artifact_path(run_dir, advisor_id="../evil", timestamp="20260929T104827Z")
        with self.assertRaises(artifacts.DecisionArtifactError):
            artifacts.artifact_path(run_dir, advisor_id="typesafe.jev", timestamp="20260929:10:48")

    def test_utc_stamp_is_file_name_safe(self):
        self.assertRegex(artifacts.utc_stamp(), re.compile(r"^\d{8}T\d{6}Z$"))

    def test_write_artifact_is_exclusive_and_leaves_core_files_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "20260929_0740_loop_gain"
            steps_dir = run_dir / "steps"
            steps_dir.mkdir(parents=True)
            run_json = run_dir / "run.json"
            summary = run_dir / "summary.csv"
            step_file = steps_dir / "step1.json"
            run_json.write_text('{"status": "failed"}', encoding="utf-8")
            summary.write_text("step,status\n1,failed\n", encoding="utf-8")
            step_file.write_text('{"step": 1}', encoding="utf-8")
            before = {path: path.read_bytes() for path in (run_json, summary, step_file)}

            written = artifacts.write_artifact(
                _artifact(), run_dir, advisor_id="typesafe.jev", timestamp="20260929T104827Z"
            )

            self.assertEqual(written, run_dir / "decisions" / "20260929T104827Z-typesafe.jev.json")
            payload = json.loads(written.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema"], "wavebench.decision.v1")
            self.assertEqual(payload["status"], "refused")
            for path, content in before.items():
                self.assertEqual(path.read_bytes(), content, f"{path.name} must not change")
            self.assertEqual(sorted(p.name for p in run_dir.iterdir()), ["decisions", "run.json", "steps", "summary.csv"])

            with self.assertRaises(FileExistsError):
                artifacts.write_artifact(
                    _artifact(), run_dir, advisor_id="typesafe.jev", timestamp="20260929T104827Z"
                )

    def test_write_artifact_without_run_dir_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "not_a_run"

            written = artifacts.write_artifact(
                _artifact(), missing, advisor_id="typesafe.jev", timestamp="20260929T104827Z"
            )

            self.assertIsNone(written)
            self.assertFalse(missing.exists())

    def test_decisions_dir_is_relative_to_run(self):
        self.assertEqual(
            artifacts.decisions_dir(Path("data/runs/x")).as_posix(), "data/runs/x/decisions"
        )


if __name__ == "__main__":
    unittest.main()
