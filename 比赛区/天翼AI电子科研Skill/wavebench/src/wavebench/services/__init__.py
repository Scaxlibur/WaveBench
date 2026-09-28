"""Stable Service exports without eager dependency initialization."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .scope_extension_service import (
        ScopeExtensionOperationResult,
        ScopeExtensionService,
    )

__all__ = ["ScopeExtensionOperationResult", "ScopeExtensionService"]


def __getattr__(name: str):
    if name in __all__:
        from .scope_extension_service import (
            ScopeExtensionOperationResult,
            ScopeExtensionService,
        )

        return {
            "ScopeExtensionOperationResult": ScopeExtensionOperationResult,
            "ScopeExtensionService": ScopeExtensionService,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
