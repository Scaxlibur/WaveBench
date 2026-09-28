from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from wavebench.errors import ConfigError

DEFAULT_MARKET_INDEX_PATH = Path(__file__).resolve().parent / "market.example.json"


@dataclass(frozen=True)
class MarketPlugin:
    plugin_id: str
    driver_id: str
    name: str
    package: str
    version: str
    kind: str
    summary: str
    homepage: str = ""
    capabilities: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()

    @property
    def capability_text(self) -> str:
        return ", ".join(self.capabilities)

    @property
    def tag_text(self) -> str:
        return ", ".join(self.tags)


@dataclass(frozen=True)
class MarketIndex:
    source: Path
    plugins: tuple[MarketPlugin, ...]

    def search(self, query: str | None = None) -> list[MarketPlugin]:
        normalized = (query or "").strip().casefold()
        plugins = self.plugins
        if normalized:
            plugins = tuple(plugin for plugin in plugins if _matches_plugin(plugin, normalized))
        return sorted(plugins, key=lambda item: (item.kind, item.plugin_id))

    def get(self, plugin_id: str) -> MarketPlugin:
        normalized = plugin_id.strip()
        for plugin in self.plugins:
            if plugin.plugin_id == normalized:
                return plugin
        raise ConfigError(f"unknown market plugin_id: {plugin_id}")


def load_market_index(path: str | Path | None = None) -> MarketIndex:
    index_path = Path(path) if path is not None else DEFAULT_MARKET_INDEX_PATH
    try:
        raw = json.loads(index_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"unable to read market index {index_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"invalid market index JSON {index_path}: {exc}") from exc
    return _parse_market_index(index_path, raw)


def _parse_market_index(path: Path, raw: Any) -> MarketIndex:
    if not isinstance(raw, dict):
        raise ConfigError("market index must be a JSON object")
    entries = raw.get("plugins")
    if not isinstance(entries, list):
        raise ConfigError("market index must contain a plugins list")
    plugins = tuple(_parse_market_plugin(item, index) for index, item in enumerate(entries))
    _reject_duplicate_plugin_ids(plugins)
    return MarketIndex(source=path, plugins=plugins)


def _parse_market_plugin(raw: Any, index: int) -> MarketPlugin:
    if not isinstance(raw, dict):
        raise ConfigError(f"market plugin #{index} must be an object")
    return MarketPlugin(
        plugin_id=_required_text(raw, "plugin_id", index),
        driver_id=_required_text(raw, "driver_id", index),
        name=_required_text(raw, "name", index),
        package=_required_text(raw, "package", index),
        version=_required_text(raw, "version", index),
        kind=_required_text(raw, "kind", index),
        summary=_required_text(raw, "summary", index),
        homepage=_optional_text(raw, "homepage", index),
        capabilities=_text_tuple(raw, "capabilities", index),
        tags=_text_tuple(raw, "tags", index),
    )


def _required_text(raw: dict[str, Any], key: str, index: int) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"market plugin #{index} field {key!r} must be a non-empty string")
    return value.strip()


def _optional_text(raw: dict[str, Any], key: str, index: int) -> str:
    value = raw.get(key, "")
    if not isinstance(value, str):
        raise ConfigError(f"market plugin #{index} field {key!r} must be a string")
    return value.strip()


def _text_tuple(raw: dict[str, Any], key: str, index: int) -> tuple[str, ...]:
    values = raw.get(key, [])
    if not isinstance(values, list):
        raise ConfigError(f"market plugin #{index} field {key!r} must be a list")
    result: list[str] = []
    for item in values:
        if not isinstance(item, str) or not item.strip():
            raise ConfigError(f"market plugin #{index} field {key!r} must contain strings")
        result.append(item.strip())
    return tuple(result)


def _reject_duplicate_plugin_ids(plugins: tuple[MarketPlugin, ...]) -> None:
    seen: set[str] = set()
    duplicates: list[str] = []
    for plugin in plugins:
        if plugin.plugin_id in seen:
            duplicates.append(plugin.plugin_id)
        seen.add(plugin.plugin_id)
    if duplicates:
        joined = ", ".join(sorted(set(duplicates)))
        raise ConfigError(f"duplicate market plugin_id: {joined}")


def _matches_plugin(plugin: MarketPlugin, query: str) -> bool:
    haystack = " ".join(
        (
            plugin.plugin_id,
            plugin.driver_id,
            plugin.name,
            plugin.package,
            plugin.kind,
            plugin.summary,
            " ".join(plugin.capabilities),
            " ".join(plugin.tags),
        )
    ).casefold()
    return query in haystack
