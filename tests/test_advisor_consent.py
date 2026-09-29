"""advisor 同意门的离线契约测试（无网络、无 API key、无第三方 SDK）。"""

from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

from wavebench.services import advisor_consent as consent

MODULE_PATH = Path(consent.__file__)
ALLOWED_IMPORTS = {"__future__", "dataclasses", "hashlib", "json", "typing"}

STATE = {"fields": {"run_status": "failed", "cycles_bucket": "few_cycles"}}
QUESTIONS = {"route": {"kind": "choice", "options": ["run check", "run verify"]}}


class AdvisorConsentTests(unittest.TestCase):
    def _preview(self, *, state=None, questions=None):
        return consent.build_preview(
            model="jev-1.13.0",
            state=state if state is not None else STATE,
            questions=questions if questions is not None else QUESTIONS,
        )

    def _resolve(self, *, preview=None, **overrides):
        kwargs = {
            "preview": preview if preview is not None else self._preview(),
            "endpoint_hosts": ("api.typesafe.ai",),
            "state_fields": ("run_status", "cycles_bucket"),
            "enabled": True,
            "registered_endpoint_hosts": ("api.typesafe.ai",),
            "registered_state_fields": ("run_status", "cycles_bucket"),
            "accepted": True,
        }
        kwargs.update(overrides)
        return consent.resolve_consent(**kwargs)

    def test_preview_is_deterministic_and_counts_untrusted_sources(self):
        state = {
            "fields": {"run_status": "failed"},
            "untrusted": [
                {"source": "instrument.response", "text": "IDN: ..."},
                {"source": "operator.utterance", "text": "信号不对"},
                {"source": "instrument.response", "text": "dup"},
            ],
        }
        first = self._preview(state=state)
        second = self._preview(state=state)

        self.assertEqual(first.payload_sha256, second.payload_sha256)
        self.assertEqual(first.payload_bytes, len(json.dumps(first.payload, ensure_ascii=False,
                                                             sort_keys=True).encode("utf-8")))
        self.assertEqual(first.untrusted_sources, ("instrument.response", "operator.utterance"))

    def test_preview_changes_when_payload_changes(self):
        self.assertNotEqual(
            self._preview().payload_sha256,
            self._preview(state={"fields": {"run_status": "ok"}}).payload_sha256,
        )

    def test_disabled_gate_refuses(self):
        outcome = self._resolve(enabled=False)

        self.assertEqual(outcome.status, consent.STATUS_REFUSED)
        self.assertEqual(outcome.reason, consent.REASON_DISABLED)
        self.assertIsNone(outcome.decision)
        self.assertFalse(outcome.granted)

    def test_unregistered_state_field_refuses_with_field_name(self):
        outcome = self._resolve(state_fields=("run_status", "cycles_bucket", "raw_voltage_v"))

        self.assertEqual(outcome.reason, "state_field_not_registered: raw_voltage_v")
        self.assertIsNone(outcome.decision)

    def test_unregistered_endpoint_refuses_with_host_name(self):
        outcome = self._resolve(endpoint_hosts=("api.typesafe.ai", "evil.example"))

        self.assertEqual(outcome.reason, "endpoint_not_registered: evil.example")
        self.assertIsNone(outcome.decision)

    def test_preview_only_never_grants_consent(self):
        outcome = self._resolve(preview_only=True, accepted=True)

        self.assertEqual(outcome.status, consent.STATUS_PREVIEW_ONLY)
        self.assertEqual(outcome.reason, consent.REASON_PREVIEW_ONLY)
        self.assertIsNone(outcome.decision)
        self.assertFalse(outcome.granted)

    def test_missing_confirmation_refuses(self):
        outcome = self._resolve(accepted=False)

        self.assertEqual(outcome.reason, consent.REASON_NOT_GRANTED)
        self.assertIsNone(outcome.decision)

    def test_granted_consent_records_scope_and_allowlists(self):
        outcome = self._resolve(granted_by="operator", granted_at="2026-09-29T10:00:00Z")

        self.assertTrue(outcome.granted)
        self.assertIsNotNone(outcome.decision)
        assert outcome.decision is not None
        self.assertEqual(outcome.decision.valid_for, "invocation")
        self.assertIsNone(outcome.decision.run_id)
        self.assertEqual(outcome.decision.endpoint_hosts, ("api.typesafe.ai",))
        self.assertEqual(
            outcome.decision.allowed_state_fields, ("cycles_bucket", "run_status")
        )
        self.assertEqual(outcome.as_payload()["granted_by"], "operator")

    def test_run_scoped_consent_requires_run_target(self):
        outcome = self._resolve(valid_for="run", run_id=None)

        self.assertEqual(outcome.reason, consent.REASON_RUN_REQUIRED)
        self.assertIsNone(outcome.decision)

    def test_run_scoped_consent_is_bound_to_run_id(self):
        granted = self._resolve(valid_for="run", run_id="20260929_0740_loop_gain")
        assert granted.decision is not None
        self.assertEqual(granted.decision.run_id, "20260929_0740_loop_gain")

        covered, reason = consent.consent_covers(
            granted.decision,
            endpoint_hosts=("api.typesafe.ai",),
            allowed_state_fields=("cycles_bucket", "run_status"),
            run_id="20260929_0800_other_run",
        )
        self.assertFalse(covered)
        self.assertEqual(reason, consent.REASON_RUN_CHANGED)

    def test_consent_invalidated_by_endpoint_or_field_change(self):
        granted = self._resolve()
        assert granted.decision is not None

        covered, reason = consent.consent_covers(
            granted.decision,
            endpoint_hosts=("api.typesafe.ai", "cdn.typesafe.ai"),
            allowed_state_fields=("cycles_bucket", "run_status"),
            run_id=None,
        )
        self.assertFalse(covered)
        self.assertEqual(reason, consent.REASON_ENDPOINT_CHANGED)

        covered, reason = consent.consent_covers(
            granted.decision,
            endpoint_hosts=("api.typesafe.ai",),
            allowed_state_fields=("run_status",),
            run_id=None,
        )
        self.assertFalse(covered)
        self.assertEqual(reason, consent.REASON_FIELD_CHANGED)

    def test_invocation_consent_covers_repeated_calls(self):
        granted = self._resolve()
        assert granted.decision is not None

        covered, reason = consent.consent_covers(
            granted.decision,
            endpoint_hosts=("api.typesafe.ai",),
            allowed_state_fields=("cycles_bucket", "run_status"),
            run_id=None,
        )
        self.assertTrue(covered)
        self.assertIsNone(reason)

    def test_ungranted_decision_is_not_reusable(self):
        decision = consent.ConsentDecision(granted=False, endpoint_hosts=("api.typesafe.ai",))

        covered, reason = consent.consent_covers(
            decision,
            endpoint_hosts=("api.typesafe.ai",),
            allowed_state_fields=(),
            run_id=None,
        )
        self.assertFalse(covered)
        self.assertEqual(reason, consent.REASON_NOT_GRANTED)

    def test_consent_record_contains_required_keys_without_secrets(self):
        record = self._resolve().as_payload()

        for key in (
            "granted",
            "valid_for",
            "run_id",
            "granted_by",
            "granted_at",
            "endpoint_hosts",
            "allowed_state_fields",
            "payload_sha256",
            "payload_bytes",
            "accepted_data_leaves_machine",
        ):
            self.assertIn(key, record)
        self.assertNotIn("api_key", json.dumps(record).lower())

    def test_module_has_no_network_or_third_party_imports(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])

        self.assertTrue(
            imported <= ALLOWED_IMPORTS,
            f"unexpected imports in advisor_consent: {sorted(imported - ALLOWED_IMPORTS)}",
        )
        for banned in ("socket", "urllib", "http", "requests", "httpx", "typesafe_sdk"):
            self.assertNotIn(banned, imported)


if __name__ == "__main__":
    unittest.main()
