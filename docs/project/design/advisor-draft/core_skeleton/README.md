# Core 阶段 1 骨架说明

本目录是 [advisor 插件类别 RFC](../../../rfcs/WaveBench_advisor插件RFC.md) 阶段 1 的**离线可跑骨架**，
目的是在动 Core 之前先证明这套契约自洽。**它不是可直接合入的补丁**：没有生命周期、没有 CLI、
没有真实 entry point 解析。

## 文件 → 未来 Core 位置

| 骨架文件 | 落地位置 | 说明 |
| --- | --- | --- |
| `advisor_api.py` | `src/wavebench/plugins/api.py` | 新增 `AdvisorPlugin` / `EgressDeclaration` / `SUPPORTED_ADVISOR_API_VERSION` / `ADVISOR_CAPABILITY_PREFIX`；既有 `InstrumentPlugin` 一行不改 |
| `advisor_registry.py` | `src/wavebench/plugins/registry.py` | 新增 `AdvisorRegistry` / `AdvisorRegistryLoadResult` / `build_advisor_registry` / `load_advisor_entry_points` / `advisor_doctor_records`；既有 `build_plugin_registry` 返回不变 |
| `advisor_packages.py` | `src/wavebench/plugins/package_inspect.py` | 把"entry point 组集合 → 类别"的规则接进 `_source_entry_points` / `_parse_entry_points`，实现裁定 2a |
| `builtin_advisors.py` | `src/wavebench/plugins/builtin.py` | 新增 `rule_advisor_plugin()`；确定性实现作为 client 适配层，**不进 descriptor** |

## 刻意省略（阶段 2/3 才做）

- entry point 由参数注入，真实实现要用 `importlib.metadata` 并受显式开关控制；
- 没有 lifecycle（`install` / `installed` / `remove` / `recover`）、账本、漂移检测；
- 没有 CLI（`plugin list --category advisor`、`advisor ask`）；
- 没有 `[advisor]` 配置段，也没有接入 access policy（裁定 5b：本次不纳入）；
- `advisor_client_factory()` 是占位，真实实现解析放到阶段 3。

## 已验证的契约（对应 RFC 验收门）

1. 未知 capability 前缀、缺 `purpose`、`transmits=False` 却声明 endpoint、`api_version` 不符 —— 全部在加载期拒绝；
2. 一个包同时声明 `[wavebench.instruments]` 与 `[wavebench.advisor]` —— `PackageCategoryError`（裁定 2a）；
3. 传输型 advisor 必须声明 `advisor.external_state`；非传输型不得声明；
4. entry point 加载失败只产出 `PluginLoadError`，不抛出；entry point 名与 `advisor_id` 不一致同样被拒；
5. `rule_advisor` 作为类别内**第二个实现**，走完整 `build_state → questions → answers → thresholds → artifact`
   链路且全程离线；同输入产出逐字节相同的 answers；
6. 基线的保守性：选项重叠为零时给人工复核（概率落在 review 区间）或直接不给建议，绝不凭噪声下结论。

## 运行

```bash
python -m pytest -q core_skeleton/test_advisor_skeleton.py     # 22 passed
python -m pytest -q                                            # 40 passed（含 seam 的 18 个）
ruff check --select E4,E7,E9,F --line-length 100 .             # All checks passed
```

离线：不需要网络、API key 或第三方 SDK。

## 后续

阶段 1 骨架通过后，阶段 2 才把同意门与 decision artifact 的 Core 版本落到 `services/`，
阶段 3 处理 `package_inspect` / `lifecycle` / CLI。是否推进取决于 RFC（PR #20）在上游的反馈。
