import pytest

from wavebench.errors import ConfigError
from wavebench.plugins.api import InstrumentPlugin
from wavebench.plugins.registry import (
    PluginRegistry,
    build_plugin_registry,
    builtin_plugin_registry,
    has_doctor_errors,
    plugin_doctor_records,
)


class FakeEntryPoint:
    def __init__(self, name, loaded, *, group="wavebench.drivers"):
        self.name = name
        self.group = group
        self._loaded = loaded
        self.dist = None

    def load(self):
        if isinstance(self._loaded, Exception):
            raise self._loaded
        return self._loaded


class FakeEntryPoints(list):
    def select(self, *, group):
        return [entry_point for entry_point in self if entry_point.group == group]


def make_plugin(driver_id="example.scope", *, kind="scope", capabilities=("scope.idn",)):
    return InstrumentPlugin(
        driver_id=driver_id,
        kind=kind,
        display_name="Example Scope",
        manufacturer="Example",
        models=("EX1",),
        capabilities=capabilities,
        summary="Example plugin.",
    )


def test_builtin_registry_lists_expected_drivers():
    registry = builtin_plugin_registry()
    driver_ids = [plugin.driver_id for plugin in registry.list_plugins()]

    assert driver_ids == [
        "rigol.dm3000",
        "rigol.dp800",
        "rigol.ds1104",
        "rohde-schwarz.rtm2032",
        "rigol.dg4202",
    ]


def test_builtin_registry_filters_by_kind():
    registry = builtin_plugin_registry()

    assert [plugin.driver_id for plugin in registry.list_plugins(kind="source")] == ["rigol.dg4202"]
    assert [plugin.driver_id for plugin in registry.list_plugins(kind="scope")] == [
        "rigol.ds1104",
        "rohde-schwarz.rtm2032"
    ]


def test_builtin_registry_get_returns_metadata():
    plugin = builtin_plugin_registry().get("rigol.dg4202")

    assert plugin.kind == "source"
    assert "source.set_frequency" in plugin.capabilities
    assert "DG4202" in plugin.models


def test_registry_rejects_duplicate_driver_ids():
    plugin = InstrumentPlugin(
        driver_id="example.driver",
        kind="scope",
        display_name="Example Scope",
        manufacturer="Example",
        models=("EX1",),
        capabilities=("scope.idn",),
        summary="Example plugin.",
    )

    with pytest.raises(ConfigError, match="duplicate plugin driver_id"):
        PluginRegistry((plugin, plugin))


def test_registry_unknown_driver_raises_config_error():
    with pytest.raises(ConfigError, match="unknown plugin driver_id"):
        builtin_plugin_registry().get("missing.driver")


def test_build_registry_does_not_load_entry_points_by_default(monkeypatch):
    entry_point = FakeEntryPoint("example", RuntimeError("should not load"))
    monkeypatch.setattr("wavebench.plugins.registry.entry_points", lambda: FakeEntryPoints([entry_point]))

    result = build_plugin_registry()

    assert not result.load_errors
    assert "example.scope" not in [plugin.driver_id for plugin in result.registry.list_plugins()]


def test_build_registry_loads_entry_points_when_requested(monkeypatch):
    entry_point = FakeEntryPoint("example", make_plugin())
    monkeypatch.setattr("wavebench.plugins.registry.entry_points", lambda: FakeEntryPoints([entry_point]))

    result = build_plugin_registry(include_entry_points=True)

    plugin = result.registry.get("example.scope")
    assert plugin.origin == "entry_point"
    assert plugin.package == "wavebench"
    assert not result.load_errors


def test_build_registry_records_bad_entry_point(monkeypatch):
    entry_point = FakeEntryPoint("broken", RuntimeError("boom"))
    monkeypatch.setattr("wavebench.plugins.registry.entry_points", lambda: FakeEntryPoints([entry_point]))

    result = build_plugin_registry(include_entry_points=True)

    assert result.load_errors[0].source == "entry_point:broken"
    assert "boom" in result.load_errors[0].message


def test_build_registry_records_duplicate_entry_point_driver_id(monkeypatch):
    entry_point = FakeEntryPoint("duplicate", make_plugin(driver_id="rigol.dg4202"))
    monkeypatch.setattr("wavebench.plugins.registry.entry_points", lambda: FakeEntryPoints([entry_point]))

    result = build_plugin_registry(include_entry_points=True)

    assert result.load_errors[0].source == "entry_point:rigol.dg4202"
    assert "duplicate plugin driver_id" in result.load_errors[0].message


def test_plugin_doctor_reports_builtin_plugins_ok():
    records = plugin_doctor_records(builtin_plugin_registry())

    assert records
    assert not has_doctor_errors(records)
    assert all(record.severity == "ok" for record in records)


def test_plugin_doctor_reports_invalid_metadata_and_load_errors():
    plugin = make_plugin(kind="scope", capabilities=("source.set_frequency", "bad capability"))
    records = plugin_doctor_records(
        PluginRegistry((plugin,)),
        load_errors=(),
    )

    messages = [record.message for record in records]
    assert has_doctor_errors(records)
    assert any("must start with 'scope' prefix" in message for message in messages)
    assert any("invalid capability name" in message for message in messages)
