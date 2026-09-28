from __future__ import annotations

from types import MappingProxyType
from typing import Final, Mapping


BUILTIN_MIGRATION_DISTRIBUTIONS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "rohde-schwarz.rtm2032": "wavebench-rohde-schwarz-rtm2000",
        "rigol.dg4202": "wavebench-rigol-dg4000",
        "rigol.dm3000": "wavebench-rigol-dm3000",
        "rigol.dp800": "wavebench-rigol-dp800",
    }
)
