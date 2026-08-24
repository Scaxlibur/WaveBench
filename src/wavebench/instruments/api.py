from __future__ import annotations

from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any, Callable, Literal, Mapping

from wavebench.logging import CommandLogger
from wavebench.plugins.api import InstrumentPlugin, PluginKind, PluginOrigin
from wavebench.services.access_policy import AccessMode, normalize_access_mode
from wavebench.transport.base import InstrumentTransport

from .scope_extensions import ScopeDescriptorExtensions
from .source_extensions import SourceDescriptorExtensions

EXECUTABLE_PLUGIN_API_VERSION = "wavebench.instrument.v2"
ScopeCouplingPolicy = Literal["fixed-high-impedance", "switchable-termination", "unknown"]
TransportFactory = Callable[[], InstrumentTransport]
DriverFactory = Callable[["DriverContext"], object]


@dataclass(frozen=True)
class OptionSpec:
    name: str
    value_type: type
    default: object | None = None
    required: bool = False
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[object, ...] = ()

    def validate(self, value: object) -> object:
        if not isinstance(value, self.value_type):
            raise TypeError(f"option {self.name!r} must be {self.value_type.__name__}")
        if self.choices and value not in self.choices:
            choices = ", ".join(repr(item) for item in self.choices)
            raise ValueError(f"option {self.name!r} must be one of: {choices}")
        if self.minimum is not None and float(value) < self.minimum:
            raise ValueError(f"option {self.name!r} must be >= {self.minimum}")
        if self.maximum is not None and float(value) > self.maximum:
            raise ValueError(f"option {self.name!r} must be <= {self.maximum}")
        return value


@dataclass(frozen=True)
class DriverContext:
    driver_id: str
    kind: PluginKind
    resource: str
    backend: str
    timeout_ms: int
    opc_timeout_ms: int
    logger: CommandLogger
    _transport_factory: TransportFactory = field(repr=False, compare=False)
    settings: Mapping[str, object] = field(default_factory=dict)
    options: Mapping[str, object] = field(default_factory=dict)
    access: AccessMode = "read_write"

    def __post_init__(self) -> None:
        object.__setattr__(self, "settings", MappingProxyType(dict(self.settings)))
        object.__setattr__(self, "options", MappingProxyType(dict(self.options)))
        object.__setattr__(self, "access", normalize_access_mode(self.access, "access"))

    def open_transport(self) -> InstrumentTransport:
        return self._transport_factory()


@dataclass(frozen=True)
class InstrumentDescriptor:
    driver_id: str
    kind: PluginKind
    display_name: str
    manufacturer: str
    models: tuple[str, ...]
    aliases: tuple[str, ...]
    capabilities: tuple[str, ...]
    idn_patterns: tuple[str, ...]
    backends: tuple[str, ...]
    option_specs: tuple[OptionSpec, ...]
    permissions: tuple[str, ...]
    factory: DriverFactory = field(repr=False, compare=False)
    summary: str = ""
    api_version: str = EXECUTABLE_PLUGIN_API_VERSION
    wavebench_min_version: str = "0.8.0"
    wavebench_max_version: str = "0.9.0"
    distribution: str = "wavebench"
    version: str = "0.8.0"
    source: str = "builtin"
    origin: PluginOrigin = "builtin"
    scope_coupling_policy: ScopeCouplingPolicy = "unknown"
    config_fields: tuple[str, ...] = ()
    resource_schemes: tuple[str, ...] = ()
    # Append-only to preserve the positional layout accepted by instrument API v2.
    scope_extensions: ScopeDescriptorExtensions | None = None
    source_extensions: SourceDescriptorExtensions | None = None

    def __post_init__(self) -> None:
        if not self.driver_id or self.driver_id.strip() != self.driver_id:
            raise ValueError("instrument driver_id must be non-empty and trimmed")
        if self.driver_id in self.aliases or len(set(self.aliases)) != len(self.aliases):
            raise ValueError(f"instrument {self.driver_id!r} has duplicate aliases")
        if any(not alias or alias.strip() != alias for alias in self.aliases):
            raise ValueError(f"instrument {self.driver_id!r} aliases must be non-empty and trimmed")
        if not self.models:
            raise ValueError(f"instrument {self.driver_id!r} must declare at least one model")
        if not self.capabilities:
            raise ValueError(f"instrument {self.driver_id!r} must declare capabilities")
        if not self.backends:
            raise ValueError(f"instrument {self.driver_id!r} must declare at least one backend")
        if any(
            not scheme
            or scheme != scheme.strip().lower()
            or not scheme.replace("-", "").isalnum()
            for scheme in self.resource_schemes
        ):
            raise ValueError(
                f"instrument {self.driver_id!r} resource schemes must be lowercase tokens"
            )
        if len(set(self.resource_schemes)) != len(self.resource_schemes):
            raise ValueError(f"instrument {self.driver_id!r} has duplicate resource schemes")
        if not callable(self.factory):
            raise TypeError(f"instrument {self.driver_id!r} factory must be callable")
        if self.scope_extensions is not None:
            if self.kind != "scope":
                raise ValueError("scope_extensions can only be declared by scope descriptors")
            if not isinstance(self.scope_extensions, ScopeDescriptorExtensions):
                raise TypeError("scope_extensions has an invalid type")
        if self.source_extensions is not None:
            if self.kind != "source":
                raise ValueError("source_extensions can only be declared by source descriptors")
            if not isinstance(self.source_extensions, SourceDescriptorExtensions):
                raise TypeError("source_extensions has an invalid type")

    def with_distribution(
        self,
        *,
        distribution: str,
        version: str,
        source: str,
        origin: PluginOrigin,
    ) -> "InstrumentDescriptor":
        return replace(
            self,
            distribution=distribution,
            version=version,
            source=source,
            origin=origin,
        )

    def validate_options(self, values: Mapping[str, object]) -> dict[str, object]:
        specs = {spec.name: spec for spec in self.option_specs}
        unknown = sorted(set(values) - set(specs))
        if unknown:
            raise ValueError(
                f"unknown option(s) for {self.driver_id}: {', '.join(unknown)}"
            )
        validated: dict[str, object] = {}
        for name, spec in specs.items():
            if name in values:
                validated[name] = spec.validate(values[name])
            elif spec.required:
                raise ValueError(f"missing required option for {self.driver_id}: {name}")
            elif spec.default is not None:
                validated[name] = spec.validate(spec.default)
        return validated

    def to_metadata(self) -> InstrumentPlugin:
        config_fields = self.config_fields or tuple(
            f"options.{spec.name}" for spec in self.option_specs
        )
        return InstrumentPlugin(
            driver_id=self.driver_id,
            kind=self.kind,
            display_name=self.display_name,
            manufacturer=self.manufacturer,
            models=self.models,
            capabilities=self.capabilities,
            summary=self.summary,
            package=self.distribution,
            origin=self.origin,
            idn_patterns=self.idn_patterns,
            config_fields=config_fields,
        )


def descriptor_from_entry_point(loaded: Any) -> InstrumentDescriptor:
    candidate = loaded if isinstance(loaded, InstrumentDescriptor) else loaded()
    if not isinstance(candidate, InstrumentDescriptor):
        raise TypeError(
            "wavebench.instruments entry point did not return an InstrumentDescriptor "
            f"(got {type(candidate).__name__})"
        )
    return candidate
