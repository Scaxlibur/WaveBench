"""Terminal UI support for WaveBench."""

__all__ = ["run"]


def run(*args: object, **kwargs: object) -> int:
    from .app import run

    return run(*args, **kwargs)
