from __future__ import annotations

from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest

from wavebench.errors import ConfigError, DataError, SessionHealthError, TransportIOError
from wavebench.instruments import (
    InstrumentDescriptor,
    SCOPE_ACQUISITION_STATUS_V2_FIELD_ORDER,
    ScopeAcquisitionRunState,
    ScopeAcquisitionStatusDriverV2,
    ScopeAcquisitionStatusFieldV2,
    ScopeAcquisitionStatusV2,
    ScopeAverageStatusV2,
    ScopeSegmentedStatusV2,
)
from wavebench.instruments.capabilities import CAPABILITY_METHODS, validate_declared_capabilities
from wavebench.instruments.contracts import ScopeAcquisitionStatusDriver
from wavebench.instruments.factory import open_instrument_driver
from wavebench.instruments.scope_extension_capabilities import (
    SCOPE_CAPABILITY_METHODS,
    SCOPE_STRICT_V2_CAPABILITIES,
    validate_scope_descriptor,
)
from wavebench.instruments.scope_extensions import (
    ScopeAcquisitionStatusProfileV2,
    ScopeDescriptorExtensions,
)
from wavebench.logging import CommandLogger
from wavebench.services.operation_specs import (
    SCOPE_OPERATION_SPECS,
    SCOPE_PORTABILITY_V2_OPERATION_SPECS,
    require_operation_spec,
)
from wavebench.transport.contracts import ReplayPolicy
from wavebench.transport.guarded import GuardedAuditedTransport
from wavebench.services.scope_service import ScopeService


_MINIMAL_FIELDS: tuple[ScopeAcquisitionStatusFieldV2, ...] = ("acquisition_type",)
_AVERAGE_FIELDS: tuple[ScopeAcquisitionStatusFieldV2, ...] = (
    "acquisition_type",
    "sample_rate_hz",
    "memory_depth",
    "average",
    "average.configured_count",
)


def _profile(
    *,
    readable_fields: tuple[ScopeAcquisitionStatusFieldV2, ...] = _MINIMAL_FIELDS,
    max_queries: int = 1,
    conditionally_applicable_fields: tuple[ScopeAcquisitionStatusFieldV2, ...] = (),
) -> ScopeAcquisitionStatusProfileV2:
    return ScopeAcquisitionStatusProfileV2(
        readable_fields=readable_fields,
        max_queries=max_queries,
        conditionally_applicable_fields=conditionally_applicable_fields,
    )


def _minimal_result() -> ScopeAcquisitionStatusV2:
    return ScopeAcquisitionStatusV2(
        acquisition_type="NORMAL",
        unavailable_fields=(
            "run_state",
            "sample_rate_hz",
            "memory_depth",
            "average",
            "segmented",
        ),
    )


def _not_average_result() -> ScopeAcquisitionStatusV2:
    return ScopeAcquisitionStatusV2(
        acquisition_type="NORMAL",
        sample_rate_hz=1_000_000.0,
        memory_depth=1_000,
        unavailable_fields=("run_state", "segmented"),
        not_applicable_fields=("average",),
    )


def _complete_result() -> ScopeAcquisitionStatusV2:
    return ScopeAcquisitionStatusV2(
        acquisition_type="AVERAGES",
        run_state=ScopeAcquisitionRunState("stopped", "normal", "STOP"),
        sample_rate_hz=1_000_000.0,
        memory_depth=1_000,
        average=ScopeAverageStatusV2(configured_count=16, complete=True),
        segmented=ScopeSegmentedStatusV2(
            option_installed=True,
            enabled=False,
            maximum_enabled=False,
            capacity=1_024,
            available=0,
        ),
    )


def _descriptor(
    *,
    profile: ScopeAcquisitionStatusProfileV2 | None = None,
    minimum: str = "0.8.24",
    capabilities: tuple[str, ...] = (),
    extensions: bool = True,
) -> InstrumentDescriptor:
    return InstrumentDescriptor(
        driver_id="example.acquisition-status-v2",
        kind="scope",
        display_name="Example scope",
        manufacturer="Example",
        models=("EX1",),
        aliases=(),
        capabilities=capabilities or ("scope.acquisition_status_v2",),
        idn_patterns=("EXAMPLE,EX1",),
        backends=("pyvisa",),
        option_specs=(),
        permissions=("instrument.io",),
        factory=lambda _context: object(),
        wavebench_min_version=minimum,
        scope_extensions=(
            ScopeDescriptorExtensions(
                acquisition_status_profile_v2=profile or _profile()
            )
            if extensions
            else None
        ),
    )


def test_acquisition_status_v2_models_serialize_complete_and_partition_absence() -> None:
    complete = _complete_result()
    not_average = _not_average_result()

    assert asdict(complete)["average"] == {"configured_count": 16, "complete": True}
    assert asdict(complete)["segmented"]["capacity"] == 1_024
    assert not_average.average is None
    assert not_average.not_applicable_fields == ("average",)
    assert not_average.unavailable_fields == ("run_state", "segmented")
    assert not_average.field_values()["average.complete"] is None
    assert SCOPE_ACQUISITION_STATUS_V2_FIELD_ORDER == (
        "acquisition_type",
        "run_state",
        "sample_rate_hz",
        "memory_depth",
        "average",
        "average.configured_count",
        "average.complete",
        "segmented",
        "segmented.option_installed",
        "segmented.enabled",
        "segmented.maximum_enabled",
        "segmented.capacity",
        "segmented.available",
    )


@pytest.mark.parametrize(
    ("factory", "error_type", "message"),
    (
        (lambda: ScopeAverageStatusV2(True), ValueError, "configured_count"),
        (lambda: ScopeAverageStatusV2(2, complete=1), ValueError, "complete"),
        (lambda: ScopeSegmentedStatusV2(capacity=True), ValueError, "capacity"),
        (lambda: ScopeSegmentedStatusV2(available=-1), ValueError, "available"),
        (
            lambda: ScopeAcquisitionStatusV2(
                acquisition_type="not safe",
                unavailable_fields=(
                    "run_state",
                    "sample_rate_hz",
                    "memory_depth",
                    "average",
                    "segmented",
                ),
            ),
            ValueError,
            "safe token",
        ),
        (
            lambda: ScopeAcquisitionStatusV2(
                acquisition_type="NORMAL",
                sample_rate_hz=0.0,
                unavailable_fields=("run_state", "memory_depth", "average", "segmented"),
            ),
            ValueError,
            "positive",
        ),
        (
            lambda: ScopeAcquisitionStatusV2(
                acquisition_type="NORMAL",
                memory_depth=True,
                unavailable_fields=("run_state", "sample_rate_hz", "average", "segmented"),
            ),
            ValueError,
            "memory_depth",
        ),
    ),
)
def test_acquisition_status_v2_nested_models_reject_invalid_values(
    factory,
    error_type: type[Exception],
    message: str,
) -> None:
    with pytest.raises(error_type, match=message):
        factory()


@pytest.mark.parametrize(
    ("changes", "error_type", "message"),
    (
        (
            {"unavailable_fields": tuple(reversed(_minimal_result().unavailable_fields))},
            ValueError,
            "stable field order",
        ),
        (
            {"unavailable_fields": ("run_state", "run_state", "sample_rate_hz", "memory_depth", "average", "segmented")},
            ValueError,
            "duplicates",
        ),
        (
            {"unavailable_fields": ("run_state", "sample_rate_hz", "memory_depth", "average", "average.complete", "segmented")},
            ValueError,
            "cannot mix partition and leaf",
        ),
        (
            {
                "unavailable_fields": ("run_state", "sample_rate_hz", "memory_depth", "segmented"),
                "not_applicable_fields": ("average.complete",),
            },
            ValueError,
            "exactly describe missing",
        ),
        (
            {
                "unavailable_fields": ("run_state", "sample_rate_hz", "memory_depth", "average", "segmented"),
                "not_applicable_fields": ("average",),
            },
            ValueError,
            "mutually exclusive",
        ),
    ),
)
def test_acquisition_status_v2_requires_exact_availability_coverage(
    changes: dict[str, object],
    error_type: type[Exception],
    message: str,
) -> None:
    values = {
        "acquisition_type": "NORMAL",
        "unavailable_fields": _minimal_result().unavailable_fields,
        "not_applicable_fields": _minimal_result().not_applicable_fields,
    }
    values.update(changes)
    with pytest.raises(error_type, match=message):
        ScopeAcquisitionStatusV2(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    (
        ({"readable_fields": ()}, "must not be empty"),
        ({"readable_fields": ("sample_rate_hz",)}, "include acquisition_type"),
        (
            {"readable_fields": ("acquisition_type", "average.configured_count")},
            "average fields require",
        ),
        (
            {"readable_fields": ("acquisition_type", "average")},
            "average requires",
        ),
        (
            {"readable_fields": ("acquisition_type", "segmented")},
            "segmented requires",
        ),
        ({"conditionally_applicable_fields": ("acquisition_type",)}, "cannot be conditional"),
        ({"max_queries": True}, "integer"),
        ({"max_queries": 33}, "1..32"),
    ),
)
def test_acquisition_status_v2_profile_rejects_invalid_static_contract(
    kwargs: dict[str, object],
    message: str,
) -> None:
    values: dict[str, object] = {
        "readable_fields": _MINIMAL_FIELDS,
        "max_queries": 1,
        "conditionally_applicable_fields": (),
    }
    values.update(kwargs)
    with pytest.raises((TypeError, ValueError), match=message):
        ScopeAcquisitionStatusProfileV2(**values)  # type: ignore[arg-type]


def test_acquisition_status_v2_profile_enforces_parent_conditional_result_contract() -> None:
    profile = _profile(
        readable_fields=_AVERAGE_FIELDS,
        max_queries=3,
        conditionally_applicable_fields=("average",),
    )

    profile.validate_result(_not_average_result())
    profile.validate_result(
        ScopeAcquisitionStatusV2(
            acquisition_type="AVERAGES",
            sample_rate_hz=1_000_000.0,
            memory_depth=1_000,
            average=ScopeAverageStatusV2(configured_count=16),
            unavailable_fields=("run_state", "average.complete", "segmented"),
        )
    )
    with pytest.raises(ValueError, match="non-conditional readable fields"):
        profile.validate_result(_minimal_result())


def test_acquisition_status_v2_is_additive_and_registered_without_r13_membership() -> None:
    class Driver:
        def idn(self) -> str:
            return "EXAMPLE,EX1,SN-1,1.0"

        def close(self) -> None:
            pass

        def get_acquisition_status_v2(
            self,
            *,
            fields: tuple[ScopeAcquisitionStatusFieldV2, ...],
        ) -> ScopeAcquisitionStatusV2:
            assert fields == _MINIMAL_FIELDS
            return _minimal_result()

    assert isinstance(Driver(), ScopeAcquisitionStatusDriverV2)
    assert "get_acquisition_status_v2" not in ScopeAcquisitionStatusDriver.__dict__
    assert SCOPE_CAPABILITY_METHODS["scope.acquisition_status_v2"] == (
        "get_acquisition_status_v2",
    )
    assert CAPABILITY_METHODS["scope.acquisition_status_v2"] == (
        "get_acquisition_status_v2",
    )
    assert "scope.acquisition_status_v2" in SCOPE_STRICT_V2_CAPABILITIES
    assert "scope.acquisition_status_v2" not in SCOPE_OPERATION_SPECS
    spec = SCOPE_PORTABILITY_V2_OPERATION_SPECS["scope.acquisition_status_v2"]
    assert require_operation_spec("scope.acquisition_status_v2") is spec
    assert spec.effect == "stateful_read"
    assert spec.lease_mode == "exclusive"
    assert spec.required_verified_fields == ()
    assert spec.error_check_minimum == "disabled"


def test_acquisition_status_v2_descriptor_requires_profile_floor_method_and_run_state_dependency() -> None:
    with pytest.raises(ConfigError, match="scope portability V2 capabilities require.*0.8.24"):
        validate_scope_descriptor(_descriptor(minimum="0.8.23"))
    with pytest.raises(ConfigError, match="acquisition_status_profile_v2"):
        validate_scope_descriptor(_descriptor(extensions=False))

    class MissingMethod:
        def close(self) -> None:
            pass

    with pytest.raises(TypeError, match="get_acquisition_status_v2"):
        validate_declared_capabilities(_descriptor(), MissingMethod())

    run_state_profile = _profile(
        readable_fields=("acquisition_type", "run_state"),
    )
    with pytest.raises(ConfigError, match="requires scope.acquisition_run_state"):
        validate_scope_descriptor(_descriptor(profile=run_state_profile))
    validate_scope_descriptor(
        _descriptor(
            profile=run_state_profile,
            capabilities=("scope.acquisition_status_v2", "scope.acquisition_run_state"),
        )
    )


def test_acquisition_status_v2_method_does_not_create_an_implicit_capability() -> None:
    descriptor = _descriptor(capabilities=("scope.idn",), extensions=False)

    class Driver:
        def idn(self) -> str:
            return "EXAMPLE,EX1,SN-1,1.0"

        def close(self) -> None:
            pass

        def get_acquisition_status_v2(
            self,
            *,
            fields: tuple[ScopeAcquisitionStatusFieldV2, ...],
        ) -> ScopeAcquisitionStatusV2:
            raise AssertionError(f"undeclared status V2 must not be called for {fields!r}")

    validate_declared_capabilities(descriptor, Driver())
    assert "scope.acquisition_status_v2" not in descriptor.capabilities


def _service(
    *,
    profile: ScopeAcquisitionStatusProfileV2,
    driver: object,
    transport: GuardedAuditedTransport | None = None,
) -> ScopeService:
    return ScopeService(
        config=SimpleNamespace(
            scope=SimpleNamespace(driver="example.acquisition-status-v2"),
            connection=SimpleNamespace(timeout_ms=1_000),
        ),
        logger=SimpleNamespace(),
        session=driver,
        descriptor=SimpleNamespace(
            driver_id="example.acquisition-status-v2",
            capabilities=("scope.acquisition_status_v2",),
            scope_extensions=ScopeDescriptorExtensions(
                acquisition_status_profile_v2=profile,
            ),
        ),
        transport=transport,
        session_state=None if transport is None else transport.session_state,
    )


def test_acquisition_status_v2_service_only_calls_v2_and_keeps_legacy_route_separate() -> None:
    calls: list[tuple[str, tuple[ScopeAcquisitionStatusFieldV2, ...] | None]] = []
    driver = SimpleNamespace(
        get_acquisition_status_v2=lambda *, fields: calls.append(("v2", fields))
        or _minimal_result(),
        get_acquisition_status=lambda: calls.append(("legacy", None))
        or (_ for _ in ()).throw(AssertionError("legacy status must stay separate")),
    )
    service = _service(profile=_profile(), driver=driver)

    assert service.acquisition_status_v2() == _minimal_result()
    assert calls == [("v2", _MINIMAL_FIELDS)]

    legacy = object()
    driver.get_acquisition_status = lambda: calls.append(("legacy", None)) or legacy
    service.descriptor = SimpleNamespace(
        driver_id="example.acquisition-status-v2",
        capabilities=("scope.acquisition_status", "scope.acquisition_status_v2"),
        scope_extensions=ScopeDescriptorExtensions(
            acquisition_status_profile_v2=_profile(),
        ),
    )
    assert service.acquisition_status() is legacy
    assert calls == [("v2", _MINIMAL_FIELDS), ("legacy", None)]


def test_acquisition_status_v2_service_rejects_invalid_result_without_fallback() -> None:
    service = _service(
        profile=_profile(),
        driver=SimpleNamespace(get_acquisition_status_v2=lambda *, fields: object()),
    )

    with pytest.raises(DataError, match="invalid result"):
        service.acquisition_status_v2()


def test_acquisition_status_v2_service_rechecks_run_state_profile_dependency() -> None:
    run_state_profile = _profile(
        readable_fields=("acquisition_type", "run_state"),
    )
    service = _service(
        profile=run_state_profile,
        driver=SimpleNamespace(get_acquisition_status_v2=lambda *, fields: _minimal_result()),
    )

    with pytest.raises(ConfigError, match="does not declare scope.acquisition_run_state"):
        service.acquisition_status_v2()


class _InnerTransport:
    resource = "TCPIP::example::INSTR"

    def __init__(self) -> None:
        self.queries: list[str] = []
        self.writes: list[str] = []

    def record_event(self, _direction: str, _text: str) -> None:
        pass

    def query(self, command: str, *, replay: ReplayPolicy = ReplayPolicy.NO_REPLAY) -> str:
        assert replay is ReplayPolicy.NO_REPLAY
        self.queries.append(command)
        return "NORMAL"

    def write(self, command: str) -> None:
        self.writes.append(command)

    def close(self) -> None:
        pass


def test_acquisition_status_v2_uses_only_budgeted_text_queries_without_identity_preflight() -> None:
    inner = _InnerTransport()
    transport = GuardedAuditedTransport(inner)

    class Driver:
        def idn(self) -> str:
            raise AssertionError("acquisition status V2 must not call legacy identity preflight")

        def get_acquisition_status_v2(
            self,
            *,
            fields: tuple[ScopeAcquisitionStatusFieldV2, ...],
        ) -> ScopeAcquisitionStatusV2:
            assert fields == _MINIMAL_FIELDS
            transport.query("ACQ:TYPE?")
            return _minimal_result()

    service = _service(profile=_profile(max_queries=1), driver=Driver(), transport=transport)

    assert service.acquisition_status_v2() == _minimal_result()
    assert inner.queries == ["ACQ:TYPE?"]
    assert inner.writes == []
    assert transport.counters.query_calls == 1


@pytest.mark.parametrize("mode", ("overrun", "write"))
def test_acquisition_status_v2_rejects_overrun_and_non_query_io_before_backend_send(
    mode: str,
) -> None:
    inner = _InnerTransport()
    transport = GuardedAuditedTransport(inner)

    class Driver:
        def get_acquisition_status_v2(
            self,
            *,
            fields: tuple[ScopeAcquisitionStatusFieldV2, ...],
        ) -> ScopeAcquisitionStatusV2:
            assert fields == _MINIMAL_FIELDS
            transport.query("ACQ:TYPE?")
            if mode == "overrun":
                transport.query("ACQ:EXTRA?")
            else:
                transport.write("ACQ:BAD")
            return _minimal_result()

    service = _service(profile=_profile(max_queries=1), driver=Driver(), transport=transport)

    with pytest.raises(SessionHealthError):
        service.acquisition_status_v2()
    assert inner.queries == ["ACQ:TYPE?"]
    assert inner.writes == []


class _FactoryTransport:
    resource = "TCPIP::example::INSTR"

    def __init__(self) -> None:
        self.queries: list[str] = []
        self.closed = 0

    def record_event(self, _direction: str, _text: str) -> None:
        pass

    def query(self, command: str, *, replay: ReplayPolicy = ReplayPolicy.NO_REPLAY) -> str:
        assert replay is ReplayPolicy.NO_REPLAY
        self.queries.append(command)
        return "ok"

    def close(self) -> None:
        self.closed += 1


def _open_factory_descriptor() -> object:
    return open_instrument_driver(
        driver_reference="example.acquisition-status-v2",
        expected_kind="scope",
        resource="TCPIP::example::INSTR",
        configured_backend="pyvisa",
        timeout_ms=1_000,
        opc_timeout_ms=2_000,
        read_retry_attempts=1,
        read_retry_delay_ms=0,
        logger=CommandLogger(),
    )


def test_acquisition_status_v2_factory_latch_blocks_construction_io_until_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inner = _FactoryTransport()
    errors: list[TransportIOError] = []

    class Driver:
        def idn(self) -> str:
            return "EXAMPLE,EX1,SN-1,1.0"

        def close(self) -> None:
            pass

        def get_acquisition_status_v2(
            self,
            *,
            fields: tuple[ScopeAcquisitionStatusFieldV2, ...],
        ) -> ScopeAcquisitionStatusV2:
            assert fields == _MINIMAL_FIELDS
            return _minimal_result()

    def factory(context):
        transport = context.open_transport()
        with pytest.raises(TransportIOError) as raised:
            transport.query("*IDN?")
        errors.append(raised.value)
        return Driver()

    descriptor = replace(_descriptor(), factory=factory)
    monkeypatch.setattr(
        "wavebench.instruments.factory.resolve_instrument_descriptor",
        lambda _reference, expected_kind: descriptor,
    )
    monkeypatch.setattr("wavebench.instruments.factory._open_transport", lambda **_kwargs: inner)

    opened = _open_factory_descriptor()

    assert [error.reason_code for error in errors] == ["factory_construction_pending"]
    assert errors[0].attempts == 0
    assert inner.queries == []
    assert opened.transport is not None
    assert opened.transport.query("*IDN?") == "ok"
    assert inner.queries == ["*IDN?"]


def test_acquisition_status_v2_factory_closes_missing_method_without_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inner = _FactoryTransport()

    class MissingMethod:
        def close(self) -> None:
            pass

    descriptor = replace(
        _descriptor(),
        factory=lambda context: (context.open_transport(), MissingMethod())[1],
    )
    monkeypatch.setattr(
        "wavebench.instruments.factory.resolve_instrument_descriptor",
        lambda _reference, expected_kind: descriptor,
    )
    monkeypatch.setattr("wavebench.instruments.factory._open_transport", lambda **_kwargs: inner)

    with pytest.raises(ConfigError, match="get_acquisition_status_v2"):
        _open_factory_descriptor()

    assert inner.queries == []
    assert inner.closed == 1


def test_undeclared_acquisition_status_v2_method_does_not_latch_legacy_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inner = _FactoryTransport()

    class LegacyDriver:
        def idn(self) -> str:
            return "EXAMPLE,EX1,SN-1,1.0"

        def close(self) -> None:
            pass

        def get_acquisition_status_v2(
            self,
            *,
            fields: tuple[ScopeAcquisitionStatusFieldV2, ...],
        ) -> ScopeAcquisitionStatusV2:
            raise AssertionError(f"undeclared status V2 must not be called for {fields!r}")

    descriptor = replace(
        _descriptor(capabilities=("scope.idn",), extensions=False),
        factory=lambda context: (context.open_transport().query("*IDN?"), LegacyDriver())[1],
    )
    monkeypatch.setattr(
        "wavebench.instruments.factory.resolve_instrument_descriptor",
        lambda _reference, expected_kind: descriptor,
    )
    monkeypatch.setattr("wavebench.instruments.factory._open_transport", lambda **_kwargs: inner)

    _open_factory_descriptor()

    assert inner.queries == ["*IDN?"]
