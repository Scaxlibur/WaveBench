"""裁定 2a：一个包只允许声明一个插件类别。

镜像 `src/wavebench/plugins/package_inspect.py` 的 entry point 组校验位置，
只把"组集合 → 类别"这条规则单独抽出来，便于离线验证。
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any, Literal

INSTRUMENT_ENTRY_POINT_GROUP = "wavebench.instruments"
ADVISOR_ENTRY_POINT_GROUP = "wavebench.advisor"
KNOWN_ENTRY_POINT_GROUPS: tuple[str, ...] = (
    INSTRUMENT_ENTRY_POINT_GROUP,
    ADVISOR_ENTRY_POINT_GROUP,
)

PackageCategory = Literal["instrument", "advisor"]


class PackageCategoryError(ValueError):
    """包声明的类别不合法（缺失或混装）。"""


def select_package_category(groups: Collection[str]) -> PackageCategory:
    declared = {group for group in groups if group in KNOWN_ENTRY_POINT_GROUPS}
    if not declared:
        raise PackageCategoryError(
            "plugin must declare exactly one of "
            f"{', '.join(KNOWN_ENTRY_POINT_GROUPS)} entry points"
        )
    if len(declared) > 1:
        raise PackageCategoryError(
            "a plugin package must declare only one category; found "
            f"{', '.join(sorted(declared))}"
        )
    if INSTRUMENT_ENTRY_POINT_GROUP in declared:
        return "instrument"
    return "advisor"


def source_entry_point_group(project: Mapping[str, Any]) -> PackageCategory:
    """从源包 pyproject 的 `[project.entry-points]` 判定类别。"""
    groups = project.get("entry-points")
    names = list(groups) if isinstance(groups, Mapping) else []
    return select_package_category(names)
