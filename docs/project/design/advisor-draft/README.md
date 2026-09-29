# WaveBench × Jev 第 0 批：seam 设计草案（v2）

> 状态：**设计草案**，已随支线 `feat/advisor-plugin-category` 入仓（本目录）。原始草案副本保留在
> `D:\Documents\Code\jev-seam-draft`；RFC 正文见 `docs/project/rfcs/WaveBench_advisor插件RFC.md`（PR #20）。
>
> 相关文件：[findings.md](findings.md)（一手事实核验）、[task_plan.md](task_plan.md)（阶段与决策记录）、
> [progress.md](progress.md)（执行与验证记录）、[core_skeleton/](core_skeleton/README.md)（阶段 1 可跑骨架）。
> v2 相对 v1 的变更：形态改为"插件"后**发现契约阻塞**（见 §1）；新增数据边界的显式确认机制（§5）；
> artifact 落点改为挂在对应 run 目录（§6）。

## 0. 结论摘要

- **形态**：按现有插件契约，advisor **不能**注册为 WaveBench 插件（§1 有源码证据）。要么改 Core 的
  插件模型（路径 A），要么退回独立包（路径 B）。我建议**先 B 验证价值，再决定是否动 Core**。
- **数据边界**：默认关闭 + 显式 opt-in + **发送前预览并要求确认**，同意记录落盘；非交互场景
  一律 fail-closed（§5）。
- **落点**：`data/runs/<run>/decisions/<ts>-<advisor>.json`，**附加式**，不改 Core 拥有的任何文件（§6）。

## 1. 形态：为什么"插件"现在做不到

### 事实（本仓源码，已核对）

| 位置 | 内容 |
| --- | --- |
| `src/wavebench/plugins/package_inspect.py:375-380` | 源包必须声明 `[wavebench.instruments]`，否则 `ConfigError("plugin must declare [wavebench.instruments] entry points / 插件必须声明可执行仪器 entry point")` |
| `src/wavebench/plugins/package_inspect.py:390-391` | wheel 的 `entry_points.txt` 缺该组同样报错 |
| `src/wavebench/plugins/registry.py:28` | `ENTRY_POINT_GROUP = "wavebench.instruments"` |
| `src/wavebench/plugins/api.py:6` | `PluginKind = Literal["scope","source","rf_source","power","dmm","sweep_analyzer"]` |
| `src/wavebench/plugins/registry.py:140-146` | 校验 `kind` 必须在上述集合内，且每个 capability 必须带 `"{kind}."` 前缀 |
| `src/wavebench/plugins/lifecycle.py:868-887` | 安装后按 `wavebench.instruments` 逐条加载 `descriptor_from_entry_point` 并 `_validate_descriptor` |

**结论**：插件系统在设计上是"仪器插件"，`kind`、capability 前缀、descriptor 校验都绑定仪器语义。
一个只做判断、没有仪器 capability、没有 SCPI 的 advisor 无法通过任何一条安装路径。

### 路径 A：扩展 Core 插件模型（"真插件"）

最小 Core delta：

1. `plugins/api.py`：新增插件类别（例如独立 entry point group `wavebench.advisor` + 独立 dataclass），
   而不是塞进 `PluginKind`——advisor 与 `scope/source/...` 不是同一维度，混进 `Literal` 会污染
   capability 前缀规则。
2. `plugins/registry.py`：注册/校验 advisor 插件（id、版本、所需 Core 版本门、声明它会外发哪些字段）。
3. `plugins/package_inspect.py` + `lifecycle.py`：允许该组通过 `package check` / `install` / `installed` /
   `remove`，并像 source/rf_source 那样加依赖校验。
4. CLI：`plugin list/info/doctor` 展示 advisor 类别；文档新增 advisor 插件开发页（含"会外发数据"声明）。
5. 测试：安装/卸载/漂移、无网络时的行为、拒绝系统 Python 等既有门禁全覆盖。

代价与风险：

- 这是对 Core 公共契约的扩展，需要维护者同意；工作量和审查成本明显高于本 PR；
- 与 PR #4 抢同一个"MCP/agent 生态"叙事，维护者可能要求先统一设计；
- `plugin install` 是**离线、`--no-deps`** 的。`typesafe-sdk` 依赖 `httpx2/pydantic/pydantic-core/tenacity`，
  这些必须由操作者预装，或被 advisor 插件显式声明并由新规则校验；
- 插件能力目前默认不导入第三方 entry point（只显式加载），advisor 需要同样的显式开关。

### 路径 B：独立包（不是插件）

作为普通 wheel/受控 venv 安装，通过既有 CLI 与 artifact 交互，**零 Core 改动**。
代价：不能用 `wavebench plugin install` 管理，不走插件账本与漂移检测。

### 建议

**先走 B**：用一个不改变任何 Core 契约的集成包验证 §9 的 kill criteria。只有在"确实打败了确定性基线"
之后，再把 advisor 类别作为**独立的 Core 提案**（独立 PR、独立审查），而不是塞进本 PR。

## 2. 四个纯函数 + 一个门面

```text
build_state(...)        -> DecisionState        # 确定性；数字在代码里分桶
build_questions(...)    -> {qid: QuestionSpec}  # 选项集来自代码拥有的 catalog
preview_request(...)    -> PreviewPayload       # 发送前给操作员看的全文
apply_thresholds(...)   -> [Recommendation]     # 概率 -> 动作；阈值随结果记录
record_decision(...)    -> DecisionArtifact     # wavebench.decision.v1，写进 run 目录
Advisor.advise(...)     -> DecisionArtifact     # 唯一持有 client 的门面，且必须先过同意门
```

契约：seam 内无网络；同输入同字节；模型不产出命令文本；阈值显式落盘；模型版本固定
`jev-1.13.0` 并同时记录服务回报的 `reported_model`。

## 3. 不可信状态规则（不变）

`DecisionState.fields` 只放代码已计算/已分桶的值；`DecisionState.untrusted` 放仪器回读、日志文本、
操作员原话，逐条带 `source`。不变量：`instructions`/`criteria` **不得插值任何 untrusted 文本**（有单测）。

## 4. 选项集的 canonical 来源（不变）

| 用途 | 来源 |
| --- | --- |
| 自然语言路由 | `python -m wavebench --help` 域列表 + `run template --list` + `capability explain` |
| 异常归因 | `docs/how-to/troubleshooting.md` 小节索引；型号相关原因归插件仓，需拆两份 catalog |
| 下一步检查 | 由 catalog 的 `next_check` 派生 |

## 5. 数据边界：显式确认（按你的决定）

三级门，逐级更严；**默认全关**：

1. **默认关闭**：未配置 `[advisor] enabled = true` 或未显式传 `--advisor`，seam 不构造任何请求。
2. **显式 opt-in**：命令行/配置必须明确开启，且配置里不含 API key 值（按本仓既有约定，key 只走环境变量）。
3. **发送前预览 + 确认**：`--advisor-preview` 打印**即将外发的完整 payload**（含每个 untrusted span
   的原文与来源、字段清单、字节数、`payload_sha256`），不联网；确认由 `--accept-external-state`
   或交互式 y/N 给出。

非交互场景（`run plan`、CI、MCP 工具）**一律拒绝**：没有显式同意就返回
`status="refused"`、`reason="external_state_consent_not_granted"`，且**不发起网络调用**。

同意记录必须落盘（放在 artifact 的 `consent` 块）。裁定 4c：默认按次确认，可登记"本 run 内有效"，
且同意**绑定 endpoint 集合与允许字段集**：

```json
"consent": {
  "granted": true,
  "valid_for": "run",
  "run_id": "20260928_0740_loop_gain",
  "granted_by": "operator",
  "granted_at": "2026-09-28T07:40:10Z",
  "endpoint_hosts": ["api.typesafe.ai"],
  "allowed_state_fields": ["run_status", "cycles_bucket", "points_per_cycle_bucket"],
  "payload_sha256": "9f2c...",
  "payload_bytes": 812,
  "accepted_data_leaves_machine": true
}
```

同意失效规则（任一命中即要求重新确认，且**不发起调用**）：

| 情形 | `reason` |
| --- | --- |
| 未同意 | `external_state_consent_not_granted` |
| endpoint 集合变化 | `consent_invalidated_by_endpoint_change` |
| 允许字段集变化 | `consent_invalidated_by_allowed_field_change` |
| run 级同意用于另一次 run | `consent_invalidated_by_run_change` |
| run 级同意但没有 run 目标 | `run_scoped_consent_requires_run_target` |

`valid_for="run"` 时必须携带 `run_id`（构造期即校验）。这样"几个月前的配置"不会在插件声明
悄悄变化后继续把新数据发出去。

其它硬规则：

- **API key 永不进 artifact / 日志 / 错误信息**；
- 字段级白名单：`state.fields` 只允许注册过的键，出现新键即拒绝（避免顺手把新字段送出去）；
- untrusted span 必须在预览里逐条可见，且允许单独剔除（`--exclude-untrusted <source>`）；
- 外发失败/未同意/被剔除，都不影响确定性路径（§7）。

## 6. 落点：挂在对应 run 目录（按你的决定）

```text
data/runs/<timestamp>_<label>/
  run.json          # Core 拥有，advisor 只读
  summary.csv       # Core 拥有，advisor 只读
  steps/            # Core 拥有，advisor 只读
  decisions/        # advisor 拥有
    20260928T074010Z-routing.json
    index.json      # 仅枚举本目录下的 decision artifact
```

规则：

- **附加式**：只新增文件，绝不修改 `run.json` / `summary.csv` / `steps/*`（避免与 Core 的
  artifact 契约和 `run report` 打架）。
- **必须绑定 run**：artifact 里记 `target.run_dir` 与 `target.run_json_sha256`（若存在），便于把
  建议与那一次实验对上。**没有 run 目录时不落盘**，只打印——不引入第二个 artifact 根目录。
- 文件名含 UTC 时间戳与 advisor id；用独占创建（`"xb"`）避免覆盖；Windows 上沿用本仓已有的
  原子替换 + 重试约定（参考 `7adf802 fix: retry atomic analysis file replacement on Windows`）。
- artifact 自带 `schema: "wavebench.decision.v1"` 与 `advisory_only: true`；
- **不进 `run report`**（裁定 6a）：Core 的报告本次不动，插件的摘要由插件自己渲染；待 schema
  稳定后再议是否并入报告。

## 7. Artifact 结构（`wavebench.decision.v1`）

```json
{
  "schema": "wavebench.decision.v1",
  "advisory_only": true,
  "status": "ok",
  "reason": null,
  "target": { "run_dir": "data/runs/20260928_0740_loop_gain", "run_json_sha256": "b41d..." },
  "advisory": { "requested_model": "jev-1.13.0", "reported_model": "jev-1.13.0", "duration_ms": 214 },
  "consent": { "...": "见 §5" },
  "state": { "fields": { "run_status": "partial", "cycles_bucket": "few_cycles" },
             "untrusted": [{ "source": "operator.utterance", "text": "..." }] },
  "questions": { "route": { "type": "choice", "instructions": "...", "criteria": { "...": "..." } } },
  "answers": { "route": { "type": "choice", "choice": "...", "confidence": 0.78,
                          "probabilities": { "...": 0.85 } } },
  "thresholds": { "accept": 0.6, "review": 0.35 },
  "recommendations": [ { "id": "route", "priority": "normal", "action": "...", "reason": "...",
                         "command": "...", "parameters": {},
                         "mutates_instrument_if_applied": false, "raw_scpi": false } ]
}
```

`recommendations[*]` 沿用 `scope.advise` 的推荐对象形状，便于复用渲染与文档。

## 8. 失败与离线语义（不可协商）

| 情形 | 结果 |
| --- | --- |
| 未开启 / 未安装集成包 | `unavailable` / `advisor_not_configured`，零建议，不抛异常 |
| 未给出数据外发同意 | `refused` / `external_state_consent_not_granted`，**不发起调用** |
| 网络失败 / 429 / 认证失败 | `unavailable` / `advisor_call_failed: <ExcType>` |
| 响应结构异常 / 未知选项 id | `invalid`，不使用任何结论（fail-closed，不猜） |

以上四种都不改变退出码，也不被 `run.json.status`、质量门、`auto_recover`、capability/access policy 读取。

## 9. 测试计划（全部离线）

| 测试 | 断言 |
| --- | --- |
| state 确定性 | 两次构造 payload 逐字节相同 |
| untrusted 不入门槛文本 | 原文不出现在任何 instructions/criteria |
| 阈值映射 | 0.85 + accept 0.60 → 一条建议，命令来自 catalog，阈值已记录 |
| 低置信度 | 0.40 → 人工复核项，`command` 为 None |
| 未知选项 id | fail-closed，不产生建议 |
| 无 client / 调用失败 | `unavailable`，不抛异常，零建议 |
| **未同意不外发** | `refused`，且 client 的 `system_one` 计数为 0 |
| **预览不外发** | `preview_request()` 返回完整 payload，client 计数为 0 |
| **同意记录** | artifact 含 `payload_sha256` / `payload_bytes` / `scope` |
| **字段白名单** | `fields` 出现未注册键 → 拒绝，不发送 |
| **artifact 路径** | 落在 `<run>/decisions/<ts>-<id>.json`，无 run 目录时不落盘 |

## 10. 红线（模型永不产出）

1. 命令行字符串、脚本、SCPI；
2. 任何安全开关建议（`--allow-50ohm`、跳过 `run check`、放宽上限）；
3. gate 结论（不得影响 `run.json.status`、质量门、capability/access policy）；
4. 覆写 Core 拥有的 artifact（只新增 `decisions/` 下的文件）。

## 11. kill criteria（先写后做）

| 功能 | 必须打败的确定性基线 |
| --- | --- |
| 自然语言路由 | 关键字匹配（约 15 个域） |
| 异常归因 / 下一步检查 | `doctor` 现有 `suggestion` + troubleshooting 5 个小节 |
| run triage / 质量综合 | `run.json.status` + 质量门 + `ok_by_consistency` |

## 12. 未核验 / 未决

- 真实 Jev 调用（无 key，按官方 HTTP 形状实现）。
- `typesafe-sdk` 的 license、是否有 on-prem、中国大陆实测延迟。
- 中文（CJK）精度实测——自然语言路由能否成立的前置条件。
- 是否值得为 advisor 扩展 Core 插件契约（§1 路径 A），建议先由路径 B 拿数据。
- 外发同意的交互细节：`--accept-external-state` 是每次确认，还是可写进配置并在 artifact 里记录
  有效期？涉及本仓安全语义，需要单独定。
