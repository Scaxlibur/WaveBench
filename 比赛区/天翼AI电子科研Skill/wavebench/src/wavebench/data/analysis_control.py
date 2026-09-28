"""Cooperative checks installed only inside a supervised analysis worker."""
from contextvars import ContextVar

from wavebench.errors import DataError


cancel_signal = ContextVar('analysis_cancel_signal', default=None)


class AnalysisCancelled(DataError):
    code = 'analysis_cancelled'


def checkpoint():
    event = cancel_signal.get()
    if event is not None and event.is_set():
        raise AnalysisCancelled('analysis cancelled by supervisor')
