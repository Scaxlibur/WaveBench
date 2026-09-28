from __future__ import annotations

DMM_FUNCTION_ALIASES = {
    "vdc": "dcv",
    "vac": "acv",
    "idc": "dci",
    "iac": "aci",
    "ohm": "res",
    "r": "res",
    "2wr": "res",
    "4wr": "fres",
    "cont": "continuity",
}


def normalize_dmm_function(function: str) -> str:
    key = function.strip().lower()
    return DMM_FUNCTION_ALIASES.get(key, key)
