"""`[advisor]` 配置段的解析与校验测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from wavebench.config import load_config
from wavebench.errors import ConfigError

BASE = """\
[connection]
resource = "TCPIP::192.0.2.40::INSTR"
[scope]
"""


class AdvisorConfigTests(unittest.TestCase):
    def _load(self, advisor_section: str):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wavebench.toml"
            path.write_text(BASE + advisor_section, encoding="utf-8")
            return load_config(path)

    def test_defaults_to_disabled_without_allowlists(self):
        config = self._load("")

        self.assertFalse(config.advisor.enabled)
        self.assertEqual(config.advisor.endpoint_hosts, ())
        self.assertEqual(config.advisor.allowed_state_fields, ())
        self.assertEqual(config.advisor.accept, 0.60)
        self.assertEqual(config.advisor.review, 0.35)

    def test_enabled_config_normalizes_allowlists(self):
        config = self._load(
            """
[advisor]
enabled = true
endpoint_hosts = ["api.typesafe.ai", "api.typesafe.ai"]
allowed_state_fields = ["run_status", "cycles_bucket"]
accept = 0.8
review = 0.4
"""
        )

        self.assertTrue(config.advisor.enabled)
        self.assertEqual(config.advisor.endpoint_hosts, ("api.typesafe.ai",))
        self.assertEqual(config.advisor.allowed_state_fields, ("cycles_bucket", "run_status"))
        self.assertEqual(config.advisor.accept, 0.8)
        self.assertEqual(config.advisor.review, 0.4)

    def test_enabled_requires_allowlists(self):
        with self.assertRaisesRegex(ConfigError, "advisor.allowed_state_fields"):
            self._load("\n[advisor]\nenabled = true\nendpoint_hosts = [\"api.typesafe.ai\"]\n")
        with self.assertRaisesRegex(ConfigError, "advisor.endpoint_hosts"):
            self._load("\n[advisor]\nenabled = true\nallowed_state_fields = [\"run_status\"]\n")

    def test_threshold_order_is_enforced(self):
        with self.assertRaisesRegex(ConfigError, "0 <= review <= accept <= 1"):
            self._load(
                "\n[advisor]\naccept = 0.2\nreview = 0.9\n"
            )

    def test_endpoint_hosts_must_be_an_array_of_strings(self):
        with self.assertRaisesRegex(ConfigError, "endpoint_hosts must be an array of strings"):
            self._load("\n[advisor]\nendpoint_hosts = \"api.typesafe.ai\"\n")

    def test_unknown_boolean_value_is_rejected(self):
        with self.assertRaisesRegex(ConfigError, "advisor.enabled must be a boolean"):
            self._load("\n[advisor]\nenabled = \"yes\"\n")


if __name__ == "__main__":
    unittest.main()
