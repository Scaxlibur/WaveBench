from __future__ import annotations

import pytest

from wavebench.drivers.dg4202 import DG4202Source
from wavebench.drivers.dm3000 import DM3000Dmm
from wavebench.drivers.dp800 import DP800Power
from wavebench.drivers.ds1104 import DS1104Scope
from wavebench.drivers.rtm2032 import RTM2032Scope
from wavebench.errors import SessionHealthError, TransportIOError
from wavebench.instruments.models import PowerStatus
from wavebench.transport.contracts import (
    CommandTransmission,
    ReplayPolicy,
    ResponseProgress,
    Synchronization,
    TransportPhase,
)


def _failure(kind: str, operation: str) -> TransportIOError | SessionHealthError:
    if kind == "session":
        return SessionHealthError(
            "ignored caller text",
            health="uncertain",
            io_kind="query_opc" if operation == "query_opc" else "write",
            epoch_id="epoch-test",
        )
    return TransportIOError(
        "backend exchange failed",
        operation=operation,
        phase=TransportPhase.READING,
        replay_policy=ReplayPolicy.NO_REPLAY,
        command_transmission=CommandTransmission.SENT,
        response_progress=ResponseProgress.UNKNOWN,
        synchronization=Synchronization.LOST,
        attempts=1,
    )


class _FailureTransport:
    """Small keyword-aware fake used to test driver exception boundaries."""

    def __init__(
        self,
        *,
        opc_failure: TransportIOError | SessionHealthError | None = None,
        write_failure: TransportIOError | SessionHealthError | None = None,
    ) -> None:
        self.opc_failure = opc_failure
        self.write_failure = write_failure
        self.writes: list[str] = []
        self.queries: list[str] = []

    def write(self, command: str) -> None:
        self.writes.append(command)
        if self.write_failure is not None:
            raise self.write_failure

    def query(self, command: str, *, replay: ReplayPolicy) -> str:
        del replay
        self.queries.append(command)
        if command == "*IDN?":
            return "RIGOL TECHNOLOGIES,DG4202,SN,FW"
        return '0,"No error"'

    def query_opc(self, *, replay: ReplayPolicy) -> str:
        del replay
        self.queries.append("*OPC?")
        if self.opc_failure is not None:
            raise self.opc_failure
        return "1"

    def close(self) -> None:
        pass


@pytest.mark.parametrize("kind", ["transport", "session"])
def test_scope_opc_preserves_structured_failure(kind: str) -> None:
    failure = _failure(kind, "query_opc")
    transport = _FailureTransport(opc_failure=failure)

    with pytest.raises(type(failure)) as raised:
        DS1104Scope(transport=transport).capture_waveform(
            channel=1,
            points="DEF",
            check_errors=False,
        )

    assert raised.value is failure


@pytest.mark.parametrize("kind", ["transport", "session"])
def test_rtm_opc_preserves_structured_failure(kind: str) -> None:
    failure = _failure(kind, "query_opc")
    transport = _FailureTransport(opc_failure=failure)

    with pytest.raises(type(failure)) as raised:
        RTM2032Scope(transport=transport).capture_waveform(
            channel=1,
            points="DEF",
            check_errors=False,
        )

    assert raised.value is failure


@pytest.mark.parametrize("kind", ["transport", "session"])
def test_dp800_transaction_does_not_recover_over_structured_write_failure(kind: str) -> None:
    failure = _failure(kind, "write")
    transport = _FailureTransport(write_failure=failure)
    driver = DP800Power(transport=transport)
    driver._model = "DP832A"
    driver._channel_count = 3
    previous = PowerStatus(
        channel=1,
        output="ON",
        mode="CV",
        rating="30V/3A",
        set_voltage_v=5.0,
        set_current_a=0.1,
        measured_voltage_v=5.0,
        measured_current_a=0.1,
        measured_power_w=0.5,
    )
    # Avoid unrelated snapshot I/O; this test targets the transaction boundary.
    driver.get_status = lambda channel: previous  # type: ignore[method-assign]

    with pytest.raises(type(failure)) as raised:
        driver.set_output(1, False, check_errors=False)

    assert raised.value is failure
    assert transport.writes == [":OUTP CH1,OFF"]


@pytest.mark.parametrize("kind", ["transport", "session"])
def test_dg4202_write_wrapper_preserves_structured_failure(kind: str) -> None:
    failure = _failure(kind, "write")
    transport = _FailureTransport(write_failure=failure)

    with pytest.raises(type(failure)) as raised:
        DG4202Source(transport=transport).set_output(1, False, check_errors=False)

    assert raised.value is failure
    # No second OFF attempt should be made by recovery after the gate/error.
    assert transport.writes == [":OUTP1 OFF"]


@pytest.mark.parametrize("kind", ["transport", "session"])
def test_dm3000_range_transaction_preserves_structured_failure(kind: str) -> None:
    failure = _failure(kind, "write")
    transport = _FailureTransport(write_failure=failure)
    dmm = DM3000Dmm(transport=transport)
    dmm.function_status = lambda: "dcv"  # type: ignore[method-assign]
    dmm._range_code = lambda function: 0  # type: ignore[method-assign]

    with pytest.raises(type(failure)) as raised:
        dmm.set_voltage_range("dcv", 1)

    assert raised.value is failure
    assert transport.writes == [":MEASure:VOLTage:DC 1"]
