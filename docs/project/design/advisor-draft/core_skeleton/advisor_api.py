"""Core 阶段 1 骨架：advisor 插件类别的对象契约。

写法镜像 `src/wavebench/plugins/api.py`：数据类 + 构造期校验 + doctor 记录类型，
不引入网络、不依赖第三方库。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PluginOrigin = Literal["builtin", "entry_point", "local"]
DiagnosticSeverity = Literal["ok", "warning", "error"]

SUPPORTED_ADVISOR_API_VERSION = "wavebench.advisor.v1"
ADVISOR_CAPABILITY_PREFIX = "advisor."
VALID_ADVISOR_CAPABILITIES: tuple[str, ...] = (
    "advisor.route",
    "advisor.triage",
    "advisor.cause",
    "advisor.rank",
    "advisor.external_state",
)


@dataclass(frozen=True)
class EgressDeclaration:
    """插件自己声明"会不会把 state 发到本机之外"。

    同意门归 Core；插件只能声明事实，不能自己决定是否已获得授权。
    """

    transmits_off_machine: bool
    purpose: str
    endpoint_hosts: tuple[str, ...] = ()
    allowed_state_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.purpose or self.purpose.strip() != self.purpose:
            raise ValueError("egress purpose must be non-empty and trimmed")
        if self.transmits_off_machine:
            if not self.endpoint_hosts:
                raise ValueError("a transmitting advisor must declare at least one endpoint host")
            if not self.allowed_state_fields:
                raise ValueError("a transmitting advisor must declare allowed_state_fields")
        elif self.endpoint_hosts:
            raise ValueError("a non-transmitting advisor must not declare endpoint hosts")
        for host in self.endpoint_hosts:
            if not host or host.strip() != host or "/" in host:
                raise ValueError(f"invalid endpoint host: {host!r}")
        for name in self.allowed_state_fields:
            if not name or name.strip() != name:
                raise ValueError(f"invalid allowed state field: {name!r}")


@dataclass(frozen=True)
class AdvisorPlugin:
    """advisor 插件的元数据与契约。不携带实现：实现由 client 适配层提供。"""

    advisor_id: str
    display_name: str
    provider: str
    capabilities: tuple[str, ...]
    summary: str
    egress: EgressDeclaration
    api_version: str = SUPPORTED_ADVISOR_API_VERSION
    package: str = "wavebench"
    origin: PluginOrigin = "builtin"

    def __post_init__(self) -> None:
        if not self.advisor_id or self.advisor_id.strip() != self.advisor_id:
            raise ValueError("advisor_id must be non-empty and trimmed")
        if not self.display_name or not self.display_name.strip():
            raise ValueError(f"advisor {self.advisor_id!r} must declare display_name")
        if not self.provider or not self.provider.strip():
            raise ValueError(f"advisor {self.advisor_id!r} must declare provider")
        if not self.capabilities:
            raise ValueError(f"advisor {self.advisor_id!r} must declare at least one capability")
        if len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError(f"advisor {self.advisor_id!r} has duplicate capabilities")

    @property
    def capability_text(self) -> str:
        return ", ".join(self.capabilities)

    @property
    def transmits_off_machine(self) -> bool:
        return self.egress.transmits_off_machine


@dataclass(frozen=True)
class PluginLoadError:
    source: str
    message: str


@dataclass(frozen=True)
class PluginDoctorRecord:
    severity: DiagnosticSeverity
    subject: str
    message: str
