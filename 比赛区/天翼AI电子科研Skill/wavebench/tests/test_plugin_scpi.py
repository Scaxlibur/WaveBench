import pytest

from wavebench.errors import ConfigError
from wavebench.plugins.scpi import (
    has_scpi_doctor_errors,
    load_scpi_plugin,
    probe_scpi_plugin,
    scpi_plugin_doctor_records,
)


def write_scpi_plugin(path, text=None):
    path.write_text(
        text
        or """
driver_id = "example.scope"
kind = "scope"
display_name = "Example Scope"
manufacturer = "Example"
models = ["EX1"]
capabilities = ["scope.idn"]
summary = "Example declarative SCPI plugin."
idn_patterns = ["EXAMPLE,EX1"]
config_fields = ["resource"]

[scpi]
idn_query = "*IDN?"
""".strip(),
        encoding="utf-8",
    )


def test_load_scpi_plugin_returns_local_instrument_plugin(tmp_path):
    path = tmp_path / "plugin.toml"
    write_scpi_plugin(path)

    scpi_plugin = load_scpi_plugin(path)

    assert scpi_plugin.plugin.driver_id == "example.scope"
    assert scpi_plugin.plugin.kind == "scope"
    assert scpi_plugin.plugin.origin == "local"
    assert scpi_plugin.plugin.package == "local"
    assert scpi_plugin.idn_query == "*IDN?"


def test_load_scpi_plugin_rejects_missing_required_field(tmp_path):
    path = tmp_path / "plugin.toml"
    write_scpi_plugin(
        path,
        """
kind = "scope"
display_name = "Example Scope"
manufacturer = "Example"
models = ["EX1"]
capabilities = ["scope.idn"]
summary = "Example declarative SCPI plugin."
""".strip(),
    )

    with pytest.raises(ConfigError, match="field 'driver_id' must be a non-empty string"):
        load_scpi_plugin(path)


def test_scpi_plugin_doctor_accepts_valid_plugin(tmp_path):
    path = tmp_path / "plugin.toml"
    write_scpi_plugin(path)

    records = scpi_plugin_doctor_records(path)

    assert records == [("ok", "example.scope", "metadata valid")]
    assert not has_scpi_doctor_errors(records)


def test_scpi_plugin_doctor_probe_appends_idn_records(tmp_path):
    path = tmp_path / "plugin.toml"
    write_scpi_plugin(path)

    class FakeTransport:
        def query(self, command, *, replay=None):
            return "Example,EX1,123"

        def close(self):
            pass

    records = scpi_plugin_doctor_records(
        path,
        probe_resource="TCPIP::192.0.2.10::INSTR",
        transport_factory=lambda config, logger: FakeTransport(),
    )

    assert ("ok", "example.scope", "metadata valid") in records
    assert ("ok", "probe", "idn_response=Example,EX1,123") in records
    assert ("ok", "probe", "idn matched declared patterns") in records
    assert not has_scpi_doctor_errors(records)


def test_scpi_plugin_doctor_probe_reports_idn_mismatch(tmp_path):
    path = tmp_path / "plugin.toml"
    write_scpi_plugin(path)

    class FakeTransport:
        def query(self, command, *, replay=None):
            return "Other,MODEL,123"

        def close(self):
            pass

    records = scpi_plugin_doctor_records(
        path,
        probe_resource="TCPIP::192.0.2.10::INSTR",
        transport_factory=lambda config, logger: FakeTransport(),
    )

    assert ("error", "probe", "idn did not match declared patterns") in records
    assert has_scpi_doctor_errors(records)


def test_scpi_plugin_doctor_probe_skips_when_metadata_invalid(tmp_path):
    path = tmp_path / "plugin.toml"
    write_scpi_plugin(
        path,
        """
driver_id = "example.scope"
kind = "scope"
display_name = "Example Scope"
manufacturer = "Example"
models = ["EX1"]
capabilities = ["source.idn"]
summary = "Example declarative SCPI plugin."
""".strip(),
    )

    records = scpi_plugin_doctor_records(
        path,
        probe_resource="TCPIP::192.0.2.10::INSTR",
        transport_factory=lambda config, logger: None,
    )

    assert ("warning", "example.scope", "probe skipped because metadata validation failed") in records
    assert has_scpi_doctor_errors(records)


def test_scpi_plugin_doctor_rejects_write_command_idn_query(tmp_path):
    path = tmp_path / "plugin.toml"
    write_scpi_plugin(
        path,
        """
driver_id = "example.scope"
kind = "scope"
display_name = "Example Scope"
manufacturer = "Example"
models = ["EX1"]
capabilities = ["scope.idn"]
summary = "Example declarative SCPI plugin."

[scpi]
idn_query = "OUTP ON"
""".strip(),
    )

    records = scpi_plugin_doctor_records(path)

    assert has_scpi_doctor_errors(records)
    assert ("error", "example.scope", "scpi.idn_query must be a query ending with '?'") in records


def test_scpi_plugin_doctor_reports_invalid_toml(tmp_path):
    path = tmp_path / "plugin.toml"
    path.write_text("driver_id = [", encoding="utf-8")

    records = scpi_plugin_doctor_records(path)

    assert has_scpi_doctor_errors(records)
    assert records[0][0] == "error"
    assert str(path) in records[0][1]


def test_probe_scpi_plugin_queries_only_declared_idn_query(tmp_path):
    path = tmp_path / "plugin.toml"
    write_scpi_plugin(path)
    calls = []

    class FakeTransport:
        def query(self, command, *, replay=None):
            calls.append(command)
            return "Example,EX1,123"

        def close(self):
            calls.append("close")

    def fake_factory(config, logger):
        assert config.resource == "TCPIP::192.0.2.10::INSTR"
        assert config.timeout_ms == 250
        assert logger is not None
        return FakeTransport()

    result = probe_scpi_plugin(
        path,
        resource="TCPIP::192.0.2.10::INSTR",
        timeout_ms=250,
        transport_factory=fake_factory,
    )

    assert calls == ["*IDN?", "close"]
    assert result.driver_id == "example.scope"
    assert result.response == "Example,EX1,123"
    assert result.matched


def test_probe_scpi_plugin_reports_idn_mismatch(tmp_path):
    path = tmp_path / "plugin.toml"
    write_scpi_plugin(path)

    class FakeTransport:
        def query(self, command, *, replay=None):
            return "Other,MODEL,123"

        def close(self):
            pass

    result = probe_scpi_plugin(
        path,
        resource="TCPIP::192.0.2.10::INSTR",
        transport_factory=lambda config, logger: FakeTransport(),
    )

    assert not result.matched


def test_probe_scpi_plugin_rejects_unsafe_query_before_opening_transport(tmp_path):
    path = tmp_path / "plugin.toml"
    write_scpi_plugin(
        path,
        """
driver_id = "example.scope"
kind = "scope"
display_name = "Example Scope"
manufacturer = "Example"
models = ["EX1"]
capabilities = ["scope.idn"]
summary = "Example declarative SCPI plugin."

[scpi]
idn_query = "*IDN?;OUTP ON"
""".strip(),
    )

    def fake_factory(config, logger):
        raise AssertionError("transport must not open for unsafe query")

    with pytest.raises(ConfigError, match="must not contain command separators"):
        probe_scpi_plugin(
            path,
            resource="TCPIP::192.0.2.10::INSTR",
            transport_factory=fake_factory,
        )
