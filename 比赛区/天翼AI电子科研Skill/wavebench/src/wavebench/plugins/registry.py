from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
from importlib.metadata import EntryPoint, entry_points
import re
from typing import Any

from wavebench.errors import ConfigError

from .api import (
    SUPPORTED_PLUGIN_API_VERSION,
    VALID_PLUGIN_KINDS,
    InstrumentPlugin,
    PluginDoctorRecord,
    PluginKind,
    PluginLoadError,
)
from .builtin import BUILTIN_PLUGINS

ENTRY_POINT_GROUP = "wavebench.drivers"
_CAPABILITY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


@dataclass(frozen=True)
class PluginRegistryLoadResult:
    registry: PluginRegistry
    load_errors: tuple[PluginLoadError, ...] = ()


@dataclass(frozen=True)
class PluginRegistry:
    plugins: tuple[InstrumentPlugin, ...]

    def __post_init__(self) -> None:
        seen: set[str] = set()
        duplicates: list[str] = []
        for plugin in self.plugins:
            if plugin.driver_id in seen:
                duplicates.append(plugin.driver_id)
            seen.add(plugin.driver_id)
        if duplicates:
            joined = ", ".join(sorted(set(duplicates)))
            raise ConfigError(f"duplicate plugin driver_id: {joined}")

    def list_plugins(self, *, kind: PluginKind | None = None) -> list[InstrumentPlugin]:
        plugins = self.plugins
        if kind is not None:
            plugins = tuple(plugin for plugin in plugins if plugin.kind == kind)
        return sorted(plugins, key=lambda item: (item.kind, item.driver_id))

    def get(self, driver_id: str) -> InstrumentPlugin:
        normalized = driver_id.strip()
        for plugin in self.plugins:
            if plugin.driver_id == normalized:
                return plugin
        raise ConfigError(f"unknown plugin driver_id: {driver_id}")


def builtin_plugin_registry() -> PluginRegistry:
    return PluginRegistry(BUILTIN_PLUGINS)


def build_plugin_registry(*, include_entry_points: bool = False) -> PluginRegistryLoadResult:
    plugins = list(BUILTIN_PLUGINS)
    load_errors: list[PluginLoadError] = []
    seen = {plugin.driver_id for plugin in plugins}

    if include_entry_points:
        for plugin, load_error in load_entry_point_plugins():
            if load_error is not None:
                load_errors.append(load_error)
                continue
            if plugin is None:
                continue
            if plugin.driver_id in seen:
                load_errors.append(
                    PluginLoadError(
                        source=f"entry_point:{plugin.driver_id}",
                        message=f"duplicate plugin driver_id: {plugin.driver_id}",
                    )
                )
                continue
            seen.add(plugin.driver_id)
            plugins.append(plugin)

    return PluginRegistryLoadResult(
        registry=PluginRegistry(tuple(plugins)),
        load_errors=tuple(load_errors),
    )


def load_entry_point_plugins() -> list[tuple[InstrumentPlugin | None, PluginLoadError | None]]:
    results: list[tuple[InstrumentPlugin | None, PluginLoadError | None]] = []
    for entry_point in _select_driver_entry_points():
        try:
            loaded = entry_point.load()
            plugin = _plugin_from_loaded_entry_point(entry_point, loaded)
        except Exception as exc:
            results.append((None, PluginLoadError(source=_entry_point_source(entry_point), message=str(exc))))
            continue
        results.append((plugin, None))
    return results


def plugin_doctor_records(
    registry: PluginRegistry,
    *,
    load_errors: tuple[PluginLoadError, ...] = (),
) -> list[PluginDoctorRecord]:
    records: list[PluginDoctorRecord] = []
    seen: set[str] = set()
    for error in load_errors:
        records.append(PluginDoctorRecord("error", error.source, error.message))
    for plugin in registry.plugins:
        plugin_errors = _validate_plugin(plugin)
        if plugin.driver_id in seen:
            plugin_errors.append(f"duplicate plugin driver_id: {plugin.driver_id}")
        seen.add(plugin.driver_id)
        if plugin_errors:
            for message in plugin_errors:
                records.append(PluginDoctorRecord("error", plugin.driver_id, message))
        else:
            records.append(PluginDoctorRecord("ok", plugin.driver_id, "metadata valid"))
    if not records:
        records.append(PluginDoctorRecord("warning", "registry", "no plugins found"))
    return records


def has_doctor_errors(records: list[PluginDoctorRecord]) -> bool:
    return any(record.severity == "error" for record in records)


def _validate_plugin(plugin: InstrumentPlugin) -> list[str]:
    errors: list[str] = []
    if plugin.api_version != SUPPORTED_PLUGIN_API_VERSION:
        errors.append(
            f"unsupported api_version {plugin.api_version!r}; expected {SUPPORTED_PLUGIN_API_VERSION!r}"
        )
    if plugin.kind not in VALID_PLUGIN_KINDS:
        errors.append(f"unsupported kind {plugin.kind!r}; expected one of {', '.join(VALID_PLUGIN_KINDS)}")
    for capability in plugin.capabilities:
        if not _CAPABILITY_PATTERN.match(capability):
            errors.append(f"invalid capability name: {capability!r}")
        elif not capability.startswith(f"{plugin.kind}."):
            errors.append(f"capability {capability!r} must start with {plugin.kind!r} prefix")
    return errors


def _select_driver_entry_points() -> list[EntryPoint]:
    discovered = entry_points()
    if hasattr(discovered, "select"):
        return list(discovered.select(group=ENTRY_POINT_GROUP))
    return list(discovered.get(ENTRY_POINT_GROUP, ()))


def _plugin_from_loaded_entry_point(entry_point: EntryPoint, loaded: Any) -> InstrumentPlugin:
    candidate = loaded() if callable(loaded) and not isinstance(loaded, InstrumentPlugin) else loaded
    if not isinstance(candidate, InstrumentPlugin):
        raise TypeError(
            f"entry point {entry_point.name!r} did not return an InstrumentPlugin "
            f"(got {type(candidate).__name__})"
        )
    return replace(candidate, origin="entry_point", package=_entry_point_package(entry_point, candidate))


def _entry_point_package(entry_point: EntryPoint, plugin: InstrumentPlugin) -> str:
    dist = getattr(entry_point, "dist", None)
    if dist is not None:
        metadata = getattr(dist, "metadata", {})
        name = metadata.get("Name") if hasattr(metadata, "get") else None
        if name:
            return str(name)
    return plugin.package


def _entry_point_source(entry_point: EntryPoint) -> str:
    return f"entry_point:{entry_point.name}"
