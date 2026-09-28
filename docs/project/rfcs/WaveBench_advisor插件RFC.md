# WaveBench advisor 插件类别 RFC

> 状态：`Draft`
> 目标：为插件体系增加第二个类别 `advisor`，并把它与安全相关的通用能力（数据外发同意门、
> decision artifact）收归 Core
> 动机实现：TypeSafe Jev（System One model）这类"输入应用状态、输出 typed judgment + 概率 + confidence"
> 的模型

## 摘要

现有插件体系只有"仪器插件"一类：`kind`、capability 前缀、descriptor 校验和生命周期后校验全部绑定
仪器语义，因此一个只做判断、没有 SCPI、没有型号的 advisor 无法注册。本 RFC 提出**插件类别
（category）**这一层抽象，并新增第二个类别 `advisor`；同时把两类与安全相关的通用能力收归 Core，
插件只提供实现。Core 不为任何厂商加特例分支。

## 约束

- 运行期离线：不引入必须联网才能工作的默认路径；CI 无网络、无 API key。
- 可审计：任何会让数据离开本机的动作必须显式、留痕、可复核。
- 确定性边界：概率型输出不得影响 `run.json.status`、质量门、`auto_recover`、capability/access policy。
- 归属：Core 只做通用抽象；型号、厂商、私有协议留在插件。
- 测试：全部离线、可确定性复现；插件契约在加载期可校验。
- 兼容：现有仪器插件契约与已安装插件零影响。

## 核心实现现状

| 位置 | 现状 |
| --- | --- |
| `src/wavebench/plugins/api.py:6` | `PluginKind = Literal["scope","source","rf_source","power","dmm","sweep_analyzer"]` |
| `src/wavebench/plugins/api.py:10` | `SUPPORTED_PLUGIN_API_VERSION = "wavebench.instrument.v1"` |
| `src/wavebench/plugins/api.py:36-42` | 仪器插件必须声明非空 `capabilities` 与至少一个 `models` |
| `src/wavebench/plugins/registry.py:28` | `ENTRY_POINT_GROUP = "wavebench.instruments"` |
| `src/wavebench/plugins/registry.py:140-146` | 校验 `kind` 合法，且每个 capability 必须以 `"{kind}."` 开头 |
| `src/wavebench/plugins/package_inspect.py:375-380` | 源包必须声明 `[wavebench.instruments]`，否则拒绝 |
| `src/wavebench/plugins/package_inspect.py:390-391` | wheel 缺该组同样拒绝 |
| `src/wavebench/plugins/lifecycle.py:868-887` | 安装后按该组逐条 `descriptor_from_entry_point` 并 `_validate_descriptor` |

## 当前问题

1. advisor 没有仪器 kind、没有型号、没有 SCPI，无法通过上述任何一条路径注册；
2. 若把 `advisor` 加入 `PluginKind`，`models` 必填与 `"{kind}."` 前缀规则会被迫为它让路，两套语义
   互相污染；
3. 数据外发（state 离开本机）目前没有 Core 级契约：每个插件各自实现，同意与审计无法统一；
4. 判断结果没有统一 artifact 契约，无法与 run 目录、审计流程对齐。

## 公共模型

### 插件类别

```text
category = instrument | advisor
每个类别自带：entry point group、加载对象契约、validator、能力命名规则
```

`instrument` 保持现状；`advisor` 使用独立 entry point group `wavebench.advisor` 与独立 API 版本门
`wavebench.advisor.v1`。首版不引入第三个类别。

### Advisor 插件契约

```python
AdvisorPlugin(
    advisor_id: str,                 # 稳定 id
    display_name: str,
    provider: str,
    capabilities: tuple[str, ...],   # 必须全部以 "advisor." 开头
    summary: str,
    egress: EgressDeclaration,
    api_version: str = "wavebench.advisor.v1",
    package: str = "wavebench",
    origin: PluginOrigin = "builtin",
)

EgressDeclaration(
    transmits_off_machine: bool,
    allowed_state_fields: tuple[str, ...],   # 允许出现的 state.fields 键
    endpoint_hosts: tuple[str, ...],         # transmits=False 时必须为空
    purpose: str,
)
```

不要求 `models` / `manufacturer` / `kind`；不要求 capability 与仪器 kind 对齐。

### 能力命名

| capability | 含义 |
| --- | --- |
| `advisor.route` | 在代码拥有的封闭选项集里选一个入口 |
| `advisor.triage` | 对 run/采集给出有序档位或人工复核 |
| `advisor.cause` | 从封闭原因表给出候选原因与概率 |
| `advisor.rank` | 对候选集重排 |
| `advisor.external_state` | 声明会把 state 发到本机之外（触发同意门） |

Core 拥有这些名字与各自答案契约（Choice / Score / Noul 形状）；插件只提供实现。

### 数据外发同意门（Core 拥有）

- 默认关闭；由配置与 CLI 显式开启；
- 发送前必须能产出完整预览（payload、字节数、sha256、逐条 untrusted span 与来源），预览不联网；
- 同意记录落盘：`granted` / `valid_for` / `run_id` / `granted_by` / `granted_at` /
  `endpoint_hosts` / `allowed_state_fields` / `payload_sha256` / `payload_bytes`；
- 同意**绑定 endpoint 集合与允许字段集**：任一变化即失效并要求重新确认，避免旧配置在插件声明
  变化后继续扩大外发范围；
- 同意范围：默认按次；可登记"本 run 内有效"（`valid_for="run"` 必须携带 `run_id`）；
- 非交互场景（`run plan`、CI、MCP）一律拒绝；
- API key 永不进 artifact / 日志 / 错误信息。

### Decision artifact（Core 拥有）

- schema `wavebench.decision.v1`，写入对应 run 目录的 `decisions/`，附加式；
- 内容：target（run 身份）、advisory（requested/reported model、duration）、consent、state、
  questions、answers（含概率与 confidence）、thresholds、recommendations；
- "概率 → 动作"的阈值策略由 Core 配置拥有（`[advisor] accept/review`），不交给插件；
- 写盘沿用既有 Windows 原子替换与重试约定；文件名含 UTC 时间戳与 advisor id，独占创建；
- 本次不并入 `run report`。

### 内置确定性 advisor

Core 内置 `rule_advisor`：纯规则、零依赖、`transmits_off_machine=False`。用于：让新类别在 CI 与
离线下可测；让"外部 advisor 必须打败确定性基线"成为可执行对比；给第三方最小样例。

### CLI 面

```text
wavebench plugin list --category advisor
wavebench plugin doctor --category advisor
wavebench plugin package check <whl|dir>
wavebench plugin install|installed|remove <...>
wavebench advisor ask --advisor <id> --question route --state <fixture.json> \
        [--preview | --accept-external-state]
```

`advisor ask --preview` 不联网，只打印即将外发的内容；参照 `plugin scpi probe` 的"显式、单次、只读"先例。

## 兼容性

- 现有仪器插件契约、entry point group、账本记录、校验路径零改动；
- 已安装插件不受影响；新增类别不改变 `build_plugin_registry()` 的返回；
- 新类别使用独立版本门；advisor 插件对 Core 的版本门由实施版本决定；
- 一个包首版只允许声明一个类别；从"禁止混装"放宽到"允许"是向后兼容的，反向不是。

## 决策（提案结论）

| # | 决策 | 结论 |
| --- | --- | --- |
| 1 | 是否引入插件类别抽象 | 引入 `instrument` / `advisor` 两个类别，独立 group 与版本门；不把 `advisor` 加入 `PluginKind` |
| 2 | 一个 wheel 能否同时提供两类插件 | 首版禁止；混装给出明确错误 |
| 3 | advisor 能否声明第三方运行时依赖 | 允许声明；依赖只从打包元数据 `Requires-Dist` 读取，`plugin doctor` 报告缺失；`plugin install` 保持离线 `--no-deps` |
| 4 | 同意门粒度 | 默认按次 + 可登记 run 级；绑定 endpoint 集合与允许字段集 |
| 5 | `advisor.external_state` 是否纳入 access policy | 本次不纳入；由 `[advisor]` 配置 + 同意门控制 |
| 6 | decision artifact 是否并入 `run report` | 本次不并入；只落盘，插件自渲染摘要 |

上述结论是本提案的裁决建议，`Draft` 状态下尚不构成对外承诺；第 5、6 条是未来设计，不作为当前能力。

## 验收门

- 离线：全部测试无网络、无 API key、无第三方 SDK；
- 契约：未知 capability、缺 `purpose`、`transmits=False` 却给出 endpoint，均在加载期拒绝；
- 类别边界：一个包同时声明两个 entry point group 必须被拒绝；
- 依赖：只从 `Requires-Dist` 读取；doctor 报缺失但不联网；
- 同意门：未同意 / endpoint 变化 / 字段集变化 / 未注册字段 / 仅预览，五种情况外部调用次数必须为 0；
- 同意范围：按次同意只对本次有效；run 级同意只对该 `run_id` 有效；
- artifact：独占创建、不改 `run.json`/`summary.csv`/`steps/*`、无 run 目录时不落盘；
- 边界：advisor 结果不得影响 `run.json.status`、质量门、`auto_recover`、capability/access policy；
- 文档：新增 Development 页与 Reference 章节，并在 [RFC 索引](../../rfcs/index.md) 登记状态。

## 分期实施

| 阶段 | 内容 |
| --- | --- |
| 1 | 类别抽象 + `AdvisorPlugin` + `EgressDeclaration` + advisor registry + 内置 `rule_advisor` |
| 2 | 同意门 + decision artifact 契约与写入器 + `[advisor]` 配置 |
| 3 | `package_inspect` / `lifecycle` 支持 advisor 包 + `advisor ask` |
| 4 | 文档（Development、Reference、概念页）与 RFC 索引登记 |

阶段 1 与 2 不依赖网络与第三方 SDK，可合并为一个可离线验证的改动；阶段 3 触及生命周期，建议单独评审。

## 不做的事

- advisor 不参与任何写路径、安全门、质量门、capability 判定；
- 不为某个厂商在 Core 内加特例分支；
- 不提供"advisor 自动执行建议"的路径：建议始终由操作者显式执行。

## 风险

- 这是 Core 公共契约扩展，审查成本高，且与既有 MCP/agent 叙事部分重叠；
- 类别抽象是长期承诺：第三个类别出现前不应再泛化；
- 若插件生态长期只有一个 advisor 实现，抽象可能被判定为过度设计；本提案以内置 `rule_advisor`
  作为第二个实现来降低该风险。

## 未核验

- 动机实现的第三方运行时依赖清单与许可证状态；
- 该实现的服务区域与网络可达性对本地实验网络的影响；
- 非英语 state 在该实现上的准确率表现。
