from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path
import re
import sys

from packaging.utils import canonicalize_name

from wavebench import __version__
from wavebench.errors import ConfigError
from wavebench.plugins.api import PluginKind, PluginLoadError

from .api import (
    EXECUTABLE_PLUGIN_API_VERSION,
    InstrumentDescriptor,
    descriptor_from_entry_point,
)
from .builtin import BUILTIN_INSTRUMENTS
from .capabilities import CAPABILITY_METHODS
from .scope_extension_capabilities import validate_scope_descriptor
from .source_conformance import validate_source_conformance_distribution
from .source_extension_capabilities import validate_source_descriptor
from .rf_source_capabilities import validate_rf_source_descriptor
from .migrations import BUILTIN_MIGRATION_DISTRIBUTIONS

ENTRY_POINT_GROUP = "wavebench.instruments"


@dataclass(frozen=True)
class InstrumentRegistryLoadResult:
    descriptors: tuple[InstrumentDescriptor, ...]
    load_errors: tuple[PluginLoadError, ...] = ()


@dataclass(frozen=True)
class InstrumentRegistry:
    builtins: tuple[InstrumentDescriptor, ...] = BUILTIN_INSTRUMENTS
    external_entry_points: tuple[EntryPoint, ...] = ()

    def __post_init__(self) -> None:
        _assert_unique_descriptors(self.builtins)

    def list_builtin(self, *, kind: PluginKind | None = None) -> list[InstrumentDescriptor]:
        descriptors = self.builtins
        if kind is not None:
            descriptors = tuple(item for item in descriptors if item.kind == kind)
        return sorted(descriptors, key=lambda item: (item.kind, item.driver_id))

    def has_reference(self, reference: str) -> bool:
        normalized = reference.strip()
        if self._find_builtin(normalized) is not None:
            return True
        return any(entry_point.name == normalized for entry_point in self.external_entry_points)

    def resolve(
        self,
        reference: str,
        *,
        expected_kind: PluginKind | None = None,
    ) -> InstrumentDescriptor:
        normalized = reference.strip()
        matching = [
            entry_point
            for entry_point in self.external_entry_points
            if entry_point.name == normalized
        ]
        descriptor = self._find_builtin(normalized)
        if normalized in BUILTIN_MIGRATION_DISTRIBUTIONS and matching:
            if len(matching) > 1:
                raise ConfigError(f"duplicate instrument entry point: {normalized}")
            descriptor = self._load_external(matching[0])
        elif descriptor is None:
            if not matching:
                raise ConfigError(
                    f"instrument driver {reference!r} is not installed; install its package "
                    "in this WaveBench environment / 仪器驱动未安装，请在当前 WaveBench 环境中安装插件包"
                )
            if len(matching) > 1:
                raise ConfigError(f"duplicate instrument entry point: {normalized}")
            descriptor = self._load_external(matching[0])
        _validate_descriptor(descriptor, expected_kind=expected_kind)
        return descriptor

    def load_all(self) -> InstrumentRegistryLoadResult:
        descriptors: list[InstrumentDescriptor] = []
        errors: list[PluginLoadError] = []
        for descriptor in self.builtins:
            try:
                _validate_descriptor(descriptor, expected_kind=None)
            except Exception as exc:
                errors.append(
                    PluginLoadError(
                        source=f"builtin:{descriptor.driver_id}",
                        message=str(exc),
                    )
                )
                continue
            descriptors.append(descriptor)
        for entry_point in self.external_entry_points:
            try:
                descriptor = self._load_external(entry_point)
                _validate_descriptor(descriptor, expected_kind=None)
                remaining = descriptors
                if descriptor.driver_id in BUILTIN_MIGRATION_DISTRIBUTIONS:
                    remaining = [
                        item
                        for item in descriptors
                        if not (
                            item.driver_id == descriptor.driver_id and item.origin == "builtin"
                        )
                    ]
                _assert_no_reference_conflicts(descriptor, tuple(remaining))
            except Exception as exc:
                errors.append(
                    PluginLoadError(
                        source=f"entry_point:{entry_point.name}",
                        message=str(exc),
                    )
                )
                continue
            descriptors = remaining
            descriptors.append(descriptor)
        return InstrumentRegistryLoadResult(tuple(descriptors), tuple(errors))

    def _find_builtin(self, reference: str) -> InstrumentDescriptor | None:
        for descriptor in self.builtins:
            if reference == descriptor.driver_id or reference in descriptor.aliases:
                return descriptor
        return None

    def _load_external(self, entry_point: EntryPoint) -> InstrumentDescriptor:
        try:
            descriptor = descriptor_from_entry_point(entry_point.load())
        except Exception as exc:
            raise ConfigError(
                f"failed to load instrument driver {entry_point.name!r}: {exc}"
            ) from exc
        if descriptor.driver_id != entry_point.name:
            raise ConfigError(
                f"instrument entry point {entry_point.name!r} returned driver_id "
                f"{descriptor.driver_id!r}; names must match"
            )
        if descriptor.aliases:
            raise ConfigError(
                f"external instrument driver {descriptor.driver_id!r} declares aliases; "
                "installable drivers are canonical-ID-only in instrument API v2"
            )
        distribution, version = _entry_point_distribution(entry_point, descriptor)
        descriptor = descriptor.with_distribution(
            distribution=distribution,
            version=version,
            source=f"entry_point:{entry_point.name}",
            origin="entry_point",
        )
        validate_source_conformance_distribution(
            descriptor,
            getattr(entry_point, "dist", None),
        )
        _assert_external_does_not_override_builtins(descriptor, self.builtins)
        return descriptor


def build_instrument_registry(*, include_entry_points: bool = True) -> InstrumentRegistry:
    discovered = _select_instrument_entry_points() if include_entry_points else []
    return InstrumentRegistry(external_entry_points=tuple(discovered))

def resolve_instrument_descriptor(
    reference: str,
    *,
    expected_kind: PluginKind | None = None,
) -> InstrumentDescriptor:
    return build_instrument_registry().resolve(reference, expected_kind=expected_kind)


def validate_instrument_reference(reference: str, *, expected_kind: PluginKind) -> None:
    if not reference.strip():
        raise ConfigError(f"{expected_kind}.driver must not be empty")
    registry = build_instrument_registry()
    builtin = registry._find_builtin(reference.strip())
    if builtin is not None:
        _validate_descriptor(builtin, expected_kind=expected_kind)
        return
    if not registry.has_reference(reference):
        raise ConfigError(
            f"{expected_kind}.driver {reference!r} is not installed; install the matching "
            "wavebench.instruments plugin / 对应仪器插件未安装"
        )


def _validate_descriptor(
    descriptor: InstrumentDescriptor,
    *,
    expected_kind: PluginKind | None,
) -> None:
    if descriptor.api_version != EXECUTABLE_PLUGIN_API_VERSION:
        raise ConfigError(
            f"instrument driver {descriptor.driver_id!r} uses unsupported api_version "
            f"{descriptor.api_version!r}; expected {EXECUTABLE_PLUGIN_API_VERSION!r}"
        )
    if expected_kind is not None and descriptor.kind != expected_kind:
        raise ConfigError(
            f"instrument driver {descriptor.driver_id!r} has kind {descriptor.kind!r}; "
            f"expected {expected_kind!r}"
        )
    unknown_capabilities = sorted(set(descriptor.capabilities) - set(CAPABILITY_METHODS))
    if unknown_capabilities:
        raise ConfigError(
            f"instrument driver {descriptor.driver_id!r} declares unknown capabilities: "
            f"{', '.join(unknown_capabilities)}"
        )
    validate_scope_descriptor(descriptor)
    validate_source_descriptor(descriptor)
    validate_rf_source_descriptor(descriptor)
    current = _version_tuple(__version__)
    if current < _version_tuple(descriptor.wavebench_min_version) or current >= _version_tuple(
        descriptor.wavebench_max_version
    ):
        raise ConfigError(
            f"instrument driver {descriptor.driver_id!r} supports WaveBench "
            f">={descriptor.wavebench_min_version}, <{descriptor.wavebench_max_version}; "
            f"current version is {__version__}"
        )


def _assert_unique_descriptors(descriptors: tuple[InstrumentDescriptor, ...]) -> None:
    seen: dict[str, str] = {}
    for descriptor in descriptors:
        for reference in (descriptor.driver_id, *descriptor.aliases):
            if reference in seen:
                raise ConfigError(
                    f"duplicate instrument driver id or alias {reference!r}: "
                    f"{seen[reference]} and {descriptor.driver_id}"
                )
            seen[reference] = descriptor.driver_id


def _assert_external_does_not_override_builtins(
    descriptor: InstrumentDescriptor,
    builtins: tuple[InstrumentDescriptor, ...],
) -> None:
    builtin_references = {
        reference
        for builtin in builtins
        for reference in (builtin.driver_id, *builtin.aliases)
    }
    conflicts = sorted(set((descriptor.driver_id, *descriptor.aliases)) & builtin_references)
    expected_distribution = BUILTIN_MIGRATION_DISTRIBUTIONS.get(descriptor.driver_id)
    if (
        conflicts == [descriptor.driver_id]
        and expected_distribution is not None
        and canonicalize_name(descriptor.distribution) == canonicalize_name(expected_distribution)
    ):
        return
    if conflicts:
        raise ConfigError(
            f"external instrument driver {descriptor.driver_id!r} conflicts with built-in "
            f"id or alias: {', '.join(conflicts)}"
        )


def _assert_no_reference_conflicts(
    descriptor: InstrumentDescriptor,
    existing: tuple[InstrumentDescriptor, ...],
) -> None:
    references = {
        reference: item.driver_id
        for item in existing
        for reference in (item.driver_id, *item.aliases)
    }
    conflicts = sorted(
        reference
        for reference in (descriptor.driver_id, *descriptor.aliases)
        if reference in references
    )
    if conflicts:
        raise ConfigError(
            f"instrument driver {descriptor.driver_id!r} conflicts with loaded id or alias: "
            f"{', '.join(conflicts)}"
        )


def _entry_point_distribution(
    entry_point: EntryPoint,
    descriptor: InstrumentDescriptor,
) -> tuple[str, str]:
    dist = getattr(entry_point, "dist", None)
    metadata = getattr(dist, "metadata", {}) if dist is not None else {}
    name = metadata.get("Name") if hasattr(metadata, "get") else None
    version = getattr(dist, "version", None) if dist is not None else None
    return str(name or descriptor.distribution), str(version or descriptor.version)


def _select_instrument_entry_points() -> list[EntryPoint]:
    discovered = entry_points()
    if hasattr(discovered, "select"):
        selected = discovered.select(group=ENTRY_POINT_GROUP)
    else:
        selected = discovered.get(ENTRY_POINT_GROUP, ())
    return [item for item in selected if _entry_point_belongs_to_current_environment(item)]


def _entry_point_belongs_to_current_environment(entry_point: EntryPoint) -> bool:
    distribution = getattr(entry_point, "dist", None)
    if distribution is None:
        return True
    try:
        distribution_root = Path(distribution.locate_file("")).resolve()
        environment_root = Path(sys.prefix).resolve()
        distribution_root.relative_to(environment_root)
    except (AttributeError, OSError, TypeError, ValueError):
        return False
    return True


def _version_tuple(value: str) -> tuple[int, ...]:
    numbers = [int(item) for item in re.findall(r"\d+", value)]
    return tuple((numbers + [0, 0, 0])[:3])
