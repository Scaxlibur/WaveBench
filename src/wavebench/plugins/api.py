from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

PluginKind = Literal["scope", "source", "rf_source", "power", "dmm", "sweep_analyzer"]
PluginOrigin = Literal["builtin", "entry_point", "local"]
DiagnosticSeverity = Literal["ok", "warning", "error"]

SUPPORTED_PLUGIN_API_VERSION = "wavebench.instrument.v1"
VALID_PLUGIN_KINDS: tuple[str, ...] = (
    "scope",
    "source",
    "rf_source",
    "power",
    "dmm",
    "sweep_analyzer",
)


@dataclass(frozen=True)
class InstrumentPlugin:
    driver_id: str
    kind: PluginKind
    display_name: str
    manufacturer: str
    models: tuple[str, ...]
    capabilities: tuple[str, ...]
    summary: str
    api_version: str = SUPPORTED_PLUGIN_API_VERSION
    package: str = "wavebench"
    origin: PluginOrigin = "builtin"
    idn_patterns: tuple[str, ...] = field(default_factory=tuple)
    config_fields: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.driver_id or self.driver_id.strip() != self.driver_id:
            raise ValueError("plugin driver_id must be non-empty and trimmed")
        if not self.capabilities:
            raise ValueError(f"plugin {self.driver_id!r} must declare at least one capability")
        if not self.models:
            raise ValueError(f"plugin {self.driver_id!r} must declare at least one model")

    @property
    def model_text(self) -> str:
        return ", ".join(self.models)

    @property
    def capability_text(self) -> str:
        return ", ".join(self.capabilities)


@dataclass(frozen=True)
class PluginLoadError:
    source: str
    message: str


@dataclass(frozen=True)
class PluginDoctorRecord:
    severity: DiagnosticSeverity
    subject: str
    message: str
