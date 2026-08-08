from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from wavebench.config import load_config
from wavebench.errors import AccessDeniedError, ConfigError
from wavebench.services.access_policy import AccessPolicy, normalize_access_mode
from wavebench.services.operation_specs import require_operation_spec
from wavebench.services.source_service import SourceService
from wavebench.logging import CommandLogger


def test_access_policy_allows_observation_but_blocks_mutation_in_read_only() -> None:
    policy = AccessPolicy("read_only")

    assert policy.allows(require_operation_spec("source.status"))
    assert not policy.allows(require_operation_spec("source.output"))
    with pytest.raises(AccessDeniedError, match="read_only"):
        policy.require(require_operation_spec("source.output"))


def test_disabled_policy_blocks_instrument_operations_but_not_offline() -> None:
    policy = AccessPolicy("disabled")

    assert policy.allows(require_operation_spec("run.check"))
    assert not policy.allows(require_operation_spec("scope.idn"))


def test_invalid_access_mode_is_actionable() -> None:
    with pytest.raises(ConfigError, match="must be one of"):
        normalize_access_mode("write_only", "source.access")


def test_access_policy_normalizes_direct_constructor_values() -> None:
    assert AccessPolicy(" READ_ONLY ").mode == "read_only"


def test_config_parses_access_modes_and_resource_overrides_preserve_them() -> None:
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "wavebench.toml"
        path.write_text(
            """
[connection]
resource = "TCPIP::scope::INSTR"

[scope]
access = "read_only"

[source]
resource = "TCPIP::source::INSTR"
access = "disabled"

[power]
resource = "TCPIP::power::INSTR"
access = "read_only"

[dmm]
resource = "TCPIP::dmm::INSTR"
backend = "lan"
access = "read_only"
""",
            encoding="utf-8",
        )
        config = load_config(path)

        assert config.scope.access == "read_only"
        assert config.source is not None and config.source.access == "disabled"
        assert config.power is not None and config.power.access == "read_only"
        assert config.dmm is not None and config.dmm.access == "read_only"
        assert config.with_source_resource("TCPIP::new-source::INSTR").source.access == "disabled"
        assert config.with_power_resource("TCPIP::new-power::INSTR").power.access == "read_only"
        assert config.with_dmm_resource("TCPIP::new-dmm::INSTR").dmm.access == "read_only"


def test_source_service_enforces_access_before_driver_call() -> None:
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "wavebench.toml"
        path.write_text(
            """
[connection]
resource = "TCPIP::scope::INSTR"

[scope]

[source]
resource = "TCPIP::source::INSTR"
access = "read_only"
""",
            encoding="utf-8",
        )
        config = load_config(path)
        session = Mock()
        session.get_status.return_value = SimpleNamespace(channel=1, output="OFF")
        service = SourceService(config=config, logger=CommandLogger(), session=session)

        service.status(channel=1)
        session.get_status.assert_called_once_with(1)
        with pytest.raises(AccessDeniedError, match="source.output"):
            service.set_output(channel=1, enabled=False)
        session.set_output.assert_not_called()
