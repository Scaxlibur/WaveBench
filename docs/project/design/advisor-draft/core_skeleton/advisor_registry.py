"""Core 阶段 1 骨架：advisor registry。

形状镜像 `src/wavebench/plugins/registry.py`：加载结果 + 查询 + doctor 记录 +
可显式加载 entry point。entry point 通过参数注入，测试无需真实的 importlib 元数据。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Callable

from advisor_api import (
    ADVISOR_CAPABILITY_PREFIX,
    SUPPORTED_ADVISOR_API_VERSION,
    AdvisorPlugin,
    PluginDoctorRecord,
    PluginLoadError,
)
from builtin_advisors import rule_advisor_plugin

ADVISOR_ENTRY_POINT_GROUP = "wavebench.advisor"
EXTERNAL_STATE_CAPABILITY = "advisor.external_state"


def validate_advisor(plugin: AdvisorPlugin) -> list[str]:
    """契约校验；返回错误列表（空表示通过）。"""
    errors: list[str] = []
    if plugin.api_version != SUPPORTED_ADVISOR_API_VERSION:
        errors.append(
            f"unsupported api_version {plugin.api_version!r}; expected {SUPPORTED_ADVISOR_API_VERSION!r}"
        )
    for capability in plugin.capabilities:
        if not capability.startswith(ADVISOR_CAPABILITY_PREFIX):
            errors.append(
                f"capability {capability!r} must start with {ADVISOR_CAPABILITY_PREFIX!r}"
            )
    declares_external = EXTERNAL_STATE_CAPABILITY in plugin.capabilities
    if plugin.transmits_off_machine and not declares_external:
        errors.append(
            f"a transmitting advisor must declare the {EXTERNAL_STATE_CAPABILITY!r} capability"
        )
    if not plugin.transmits_off_machine and declares_external:
        errors.append(
            f"a non-transmitting advisor must not declare {EXTERNAL_STATE_CAPABILITY!r}"
        )
    return errors


@dataclass(frozen=True)
class AdvisorRegistryLoadResult:
    registry: "AdvisorRegistry"
    load_errors: tuple[PluginLoadError, ...] = ()


class AdvisorRegistry:
    def __init__(self, plugins: Iterable[AdvisorPlugin] = ()) -> None:
        by_id: dict[str, AdvisorPlugin] = {}
        for plugin in plugins:
            if plugin.advisor_id in by_id:
                raise ValueError(f"duplicate advisor_id: {plugin.advisor_id}")
            by_id[plugin.advisor_id] = plugin
        self._by_id = by_id

    @property
    def plugins(self) -> tuple[AdvisorPlugin, ...]:
        return tuple(self._by_id[key] for key in sorted(self._by_id))

    def list_advisors(self, *, capability: str | None = None) -> list[AdvisorPlugin]:
        plugins = self.plugins
        if capability is not None:
            plugins = tuple(plugin for plugin in plugins if capability in plugin.capabilities)
        return list(plugins)

    def get(self, advisor_id: str) -> AdvisorPlugin:
        plugin = self._by_id.get(advisor_id)
        if plugin is None:
            raise KeyError(f"advisor not found: {advisor_id}")
        return plugin


def _plugin_from_entry_point(entry_point: Any) -> AdvisorPlugin:
    loaded = entry_point.load()
    plugin = loaded() if callable(loaded) and not isinstance(loaded, AdvisorPlugin) else loaded
    if not isinstance(plugin, AdvisorPlugin):
        raise TypeError(f"entry point {entry_point.name!r} did not return an AdvisorPlugin")
    if plugin.advisor_id != entry_point.name:
        raise ValueError(
            f"entry point name {entry_point.name!r} does not match advisor_id {plugin.advisor_id!r}"
        )
    return plugin


def load_advisor_entry_points(
    entry_points: Iterable[Any],
) -> list[tuple[AdvisorPlugin | None, PluginLoadError | None]]:
    loaded: list[tuple[AdvisorPlugin | None, PluginLoadError | None]] = []
    for entry_point in entry_points:
        try:
            loaded.append((_plugin_from_entry_point(entry_point), None))
        except Exception as exc:  # 加载失败只记录，不抛出
            loaded.append(
                (None, PluginLoadError(source=ADVISOR_ENTRY_POINT_GROUP, message=f"{entry_point.name}: {exc}"))
            )
    return loaded


def advisor_doctor_records(
    registry: AdvisorRegistry,
    load_errors: Sequence[PluginLoadError] = (),
) -> list[PluginDoctorRecord]:
    records: list[PluginDoctorRecord] = []
    for error in load_errors:
        records.append(
            PluginDoctorRecord(severity="error", subject=error.source, message=error.message)
        )
    for plugin in registry.plugins:
        errors = validate_advisor(plugin)
        for message in errors:
            records.append(
                PluginDoctorRecord(severity="error", subject=plugin.advisor_id, message=message)
            )
        if not errors:
            records.append(
                PluginDoctorRecord(
                    severity="ok",
                    subject=plugin.advisor_id,
                    message=(
                        f"capabilities={plugin.capability_text}; "
                        f"transmits_off_machine={plugin.transmits_off_machine}"
                    ),
                )
            )
    return records


def has_advisor_doctor_errors(records: Sequence[PluginDoctorRecord]) -> bool:
    return any(record.severity == "error" for record in records)


def builtin_advisor_registry() -> AdvisorRegistry:
    return AdvisorRegistry((rule_advisor_plugin(),))


def build_advisor_registry(
    *,
    include_entry_points: bool = False,
    entry_points: Iterable[Any] | None = None,
) -> AdvisorRegistryLoadResult:
    plugins: list[AdvisorPlugin] = list(builtin_advisor_registry().plugins)
    load_errors: list[PluginLoadError] = []
    if include_entry_points:
        for plugin, error in load_advisor_entry_points(entry_points or ()):
            if error is not None:
                load_errors.append(error)
            elif plugin is not None:
                plugins.append(plugin)
    return AdvisorRegistryLoadResult(registry=AdvisorRegistry(plugins), load_errors=tuple(load_errors))


def advisor_client_factory(registry: AdvisorRegistry, advisor_id: str) -> Callable[[], Any]:
    """占位：阶段 1 不解析真实实现，只保证类别与契约先落地。"""

    registry.get(advisor_id)
    raise NotImplementedError("advisor implementation resolution lands in phase 3")
