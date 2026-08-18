from __future__ import annotations

import ast
from pathlib import Path


_ROOT = Path(__file__).parents[1]
_QUERY_METHODS = frozenset({"query", "query_opc", "query_bin_block", "query_float_list"})


def _direct_transport_call(node: ast.Call, *, plugin: bool = False) -> bool:
    if not isinstance(node.func, ast.Attribute) or node.func.attr not in _QUERY_METHODS:
        return False
    receiver = node.func.value
    if (
        isinstance(receiver, ast.Attribute)
        and isinstance(receiver.value, ast.Name)
        and receiver.value.id == "self"
        and receiver.attr == "transport"
    ):
        return True
    return plugin and isinstance(receiver, ast.Name) and receiver.id == "transport"


def test_core_driver_queries_declare_replay_policy() -> None:
    paths = sorted((_ROOT / "src/wavebench/drivers").glob("*.py"))
    paths.append(_ROOT / "src/wavebench/plugins/scpi.py")
    missing: list[str] = []

    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        plugin = path.name == "scpi.py"
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not _direct_transport_call(node, plugin=plugin):
                continue
            if not any(keyword.arg == "replay" for keyword in node.keywords):
                missing.append(f"{path.relative_to(_ROOT)}:{node.lineno}")

    assert not missing, "unclassified core transport queries: " + ", ".join(missing)
