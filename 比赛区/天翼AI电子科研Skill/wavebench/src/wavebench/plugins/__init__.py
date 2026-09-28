"""Plugin registry support for WaveBench."""

from .api import InstrumentPlugin
from .registry import PluginRegistry, build_plugin_registry, builtin_plugin_registry

__all__ = ["InstrumentPlugin", "PluginRegistry", "build_plugin_registry", "builtin_plugin_registry"]
