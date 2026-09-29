# Findings & Decisions

## Requirements

- 目标：让 WaveBench 支持 "advisor 插件类别"，以便接入 TypeSafe Jev 这类 System One 模型
- 用户已定：① 形态 = 插件；② 数据外发必须由用户显式确认；③ decision artifact 挂在对应 run 目录下
- 硬约束（来自 WaveBench）：运行期离线、Python ≥3.11、Linux+Windows CI、写路径显式可审计、
  Core 只做通用抽象、测试确定性且不联网
- 红线：advisor 不得影响 `run.json.status`、质量门、`auto_recover`、capability/access policy

## Research Findings

### A. WaveBench 插件契约（源码事实）

| 位置 | 事实 |
| --- | --- |
| `plugins/api.py:6` | `PluginKind = Literal["scope","source","rf_source","power","dmm","sweep_analyzer"]` |
| `plugins/api.py:10` | `SUPPORTED_PLUGIN_API_VERSION = "wavebench.instrument.v1"` |
| `plugins/api.py:22-42` | `InstrumentPlugin` 必填 `driver_id/kind/models/capabilities/manufacturer`；`models` 至少一个 |
| `plugins/registry.py:28` | `ENTRY_POINT_GROUP = "wavebench.instruments"` |
| `plugins/registry.py:134-146` | 校验 `kind` 合法 + capability 必须以 `"{kind}."` 开头 |
| `plugins/package_inspect.py:375-380` | 源包必须声明 `[wavebench.instruments]`，否则 `ConfigError` |
| `plugins/package_inspect.py:390-391` | wheel 缺该组同样拒绝 |
| `plugins/lifecycle.py:868-887` | 安装后按该组逐条 `descriptor_from_entry_point` + `_validate_descriptor` |
| `plugins/package_inspect.py:82-110` | `WheelEntryPoint(driver_id, value)`、`PluginPackage(... entry_points ...)` |
| `plugins/lifecycle.py:161-340` | `installed/info/install/upgrade/downgrade/remove/recover` |
| `cli_parser.py:180-355` | plugin 子命令：list/info/package check/install/installed/upgrade/downgrade/remove/recover/doctor/market/scpi |

结论：现有插件体系与"仪器"绑定，advisor 无任何注册路径。

### B. Jev（TypeSafe AI）一手事实

- 形态：`POST https://api.typesafe.ai/v1/systemone`，Bearer key，`state` + `questions`
- 三种原语：`noul`（P(真)）、`choice`（封闭集概率 + confidence，基数 ≤255）、`score`（有序档位）
- 一次请求内**并行**评估所有问题；不生成文本；schema 匹配保证无类型错误
- 模型：`jev-1.13.0`（别名 `jev-latest`/`jev-preview` 会漂移，官方建议固定版本 ID）
- 规格：64k 上下文（`state` + 最长问题 ≤32k）；**仅文本输入**；限流 250k tok/s、1200 req/min
  且官方标注"动态调整、可能无预警变化"；$0.042/Mtok 输入、输出免费；延迟 70–500 ms（美西本机测得）
- Python SDK：`typesafe-sdk` 0.7.2，`requires-python>=3.10`，依赖 `httpx2/pydantic/pydantic-core/tenacity`；
  **PyPI license 字段为空**
- 数据：托管美国、不用输入训练、企业档可谈 ZDR；英文最强，**CJK 可用但不等同英文**
- 官方已知缺陷（`jev-1.13 jaggedness`，2026-09-17 复核）：数字/计数/日期比较不可靠（要求放回代码）；
  间接层数越多越差；**context rot**；`state` 对抗性内容能推动答案；**不保证结构不变式**
  （`P(x)+P(¬x)` 可为 1.19）；Score 数值标定弱

### C. 可复用的本仓既有先例

- 显式风险开关：`scope fetch --allow-50ohm`、`scope observe --fetch-waveform`
- 显式单次只读探针：`plugin scpi probe --resource ...`
- 离线受管安装：`plugin install` 只装本地 wheel/目录、`--no-deps`、拒绝系统 Python、有账本与漂移检测
- artifact 与原子写：`_write_scope_artifact` 用 `"xb"`；`7adf802 fix: retry atomic analysis file
  replacement on Windows`
- 概率型建议对象形状：`scope.advise` 的 `{id,priority,action,reason,command,parameters,
  mutates_instrument_if_applied,raw_scpi}`
- 文档流程：`docs/rfcs/index.md` 状态体系（Draft/Accepted/Implemented（未发布）/Implemented/
  Superseded）+ 三个文档门禁脚本

## Technical Decisions

| Decision | Rationale |
| --- | --- |
| 引入插件类别抽象（instrument / advisor） | 仪器语义与 advisor 语义不同，混入 `PluginKind` 会污染 capability 前缀与必填字段规则 |
| 新 entry point group `wavebench.advisor` + API 版本门 `wavebench.advisor.v1` | 与仪器组解耦，独立演进 |
| 数据外发同意门归 Core | 否则每个插件各写一套审计；第三方不敢写 advisor |
| decision artifact 契约与"概率→动作"阈值归 Core | 产物统一、可审计，防插件自造格式 |
| Core 内置 `rule_advisor`（确定性、不联网） | 让新类别离线可测；让 kill criteria 可执行；防"过度设计"质疑 |
| `advisor ask --preview` 默认不联网 | 可玩性来自"能安全地试"，沿用 `plugin scpi probe` 先例 |
| 首版禁止一个 wheel 同时提供两类插件（建议） | 降低账本与文件所有权冲突检测复杂度 |
| 【已裁定 2026-09-28】1a 类别抽象 / 2a 禁止混装 / 3a 依赖只从 `Requires-Dist` 读且 install 不变 / 4c 同意绑定 endpoint+字段集 / 5b 不动 access policy / 6a 不进 run report | 用户裁定；落地后果与验收门已写回 RFC 与 README |

## Issues Encountered

| Issue | Resolution |
| --- | --- |
| advisor 无法注册为插件 | 提"插件类别"抽象 + 新组；不硬塞 `PluginKind` |
| `plugin install` 离线 `--no-deps` 与 SDK 需要 `httpx2/pydantic/tenacity` | RFC 列为裁定项：允许声明依赖、`plugin doctor` 报缺失、install 行为不变 |
| 本机与上游 Windows CI 结果不一致（8 个 analysis 测试失败） | 上游 Windows 3.11/3.12 job 全绿，确认是本机环境问题 |
| `api.github.com` TLS 瞬时故障导致 gh 全挂 | 轮询等恢复；拒绝使用 `-k`/`--insecure` 绕过 |

## Resources

- Jev：`typesafe.ai/blog/introducing-system-one-models-and-jev`、`docs.typesafe.ai/{introduction/quickstart,models,model-jaggedness/jev-1.13,primitives}`
- SDK：`pypi.org/pypi/typesafe-sdk/json`；LangChain：`langchain.com/blog/building-a-harness-with-jev`
- 本仓：`plugins/{api,registry,package_inspect,lifecycle}.py`、`docs/rfcs/index.md`、
  `docs/development/plugin-development.md`、`docs/concepts/plugin-model.md`
- 草案与本规划文件：本目录（`docs/project/design/advisor-draft/`）；原始副本在 `D:\Documents\Code\jev-seam-draft\`

## 未核验

- `typesafe-sdk` license、是否有 on-prem、中国大陆实测延迟、中文精度实测值
- 真实 Jev 调用（无 API key；seam 按官方 HTTP 形状实现，未实调）
