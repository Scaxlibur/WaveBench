# Task Plan: WaveBench advisor 插件类别（接入 Jev）

## Goal

在不往 Core 塞厂商特例的前提下，为 WaveBench 增加 `advisor` 插件类别（含 Core 拥有的数据外发同意门
与 `wavebench.decision.v1` artifact），使 Jev 这类 System One 模型可作为插件接入；按本仓 RFC 流程推进。

## Next Step

Phase 3（同意门 + decision artifact + `[advisor]` 配置）已落地并通过测试。下一步：Phase 4 —— 按骨架映射
把插件类别抽象落进 `plugins/{api,registry,package_inspect,builtin}.py`，并补 `plugin list --category advisor`
与 `advisor ask --preview`；完成后把本支线提交 cherry-pick 到 `competition`。

## Current Phase

Phase 4（Core 阶段 3 —— 类别抽象 + lifecycle + CLI）

## Phases

### Phase 0：前置（complete）

- PR #4 复审修复 → rebase 到 `44506c4` → force-with-lease 推送 → 逐条回复（已完成）
- Jev 一手事实核验（官方 blog/docs/models/jaggedness/privacy + PyPI + LangChain）（已完成）
- seam 草案 v2：同意门 + 字段白名单 + run 目录 artifact（14 个离线测试通过）
- advisor 插件 RFC 草案（含源码级阻塞证据与分期方案）
- **Status:** complete

### Phase 1：RFC 定稿与裁定（complete）

- [x] 6 个决策点裁定：1a 类别抽象 / 2a 首版禁止混装 / 3a 允许声明依赖且 install 不变 /
  4c 同意见混合粒度并绑定 endpoint+字段集 / 5b 不纳入 access policy / 6a 不进 run report
- [x] 裁定结果写回 RFC、README 与规划文件
- [x] 按 4c 实现同意失效规则并补测（seam 18 个离线测试通过）
- [ ] 决定上游路径（提 RFC / 先做 Core 阶段 1 骨架）
- **Status:** complete

### Phase 2：Core 阶段 1 —— 类别抽象骨架（complete，draft 内）

- [x] `advisor_api.py`：`AdvisorPlugin` / `EgressDeclaration` / API 版本门 / capability 前缀
- [x] `advisor_packages.py`：裁定 2a（一个包只能一个类别）
- [x] `advisor_registry.py`：registry + 加载（可注入 entry point）+ doctor 记录
- [x] `builtin_advisors.py`：确定性 `rule_advisor`（与真实服务同 client 协议，离线）
- [x] 契约测试 22 个（含端到端离线链路与基线保守性）
- [x] 骨架说明 `core_skeleton/README.md`（含到真实 Core 的映射与刻意省略清单）
- **Status:** complete（仍不碰仓库；是否推进取决于 PR #20 的上游反馈）

### Phase 3：Core 阶段 2 —— 同意门 + decision artifact 落 Core（complete）

- [x] `src/wavebench/services/advisor_consent.py`：三级门（默认关闭 / 显式 opt-in / 预览 + 确认）、预览（payload、字节数、sha256、逐条 untrusted 来源）、`consent_covers` 失效判定（endpoint／字段集／run 变化）与五种"外部调用次数为 0"的拒绝原因
- [x] `src/wavebench/services/decision_artifacts.py`：`wavebench.decision.v1` 正文、`ThresholdPolicy`、独占创建写入器（无 run 目录不落盘；不改 `run.json`／`summary.csv`／`steps/*`）
- [x] `config.py`：`AdvisorConfig` 与 `[advisor]` 解析/校验（开启必须给出两份白名单；`0 <= review <= accept <= 1`）
- [x] `wavebench.example.toml` 与 `docs/reference/configuration.md`：`[advisor]` 段与外发边界
- [x] 测试：`tests/test_advisor_consent.py`、`tests/test_decision_artifacts.py`、`tests/test_advisor_config.py`
- **Status:** complete

### Phase 4：Core 阶段 3 —— lifecycle + CLI（pending）

- 文件：`plugins/package_inspect.py`（组→validator 表）、`plugins/lifecycle.py`（战后校验按类别、ledger
  加 `category`）、`cli_parser.py` + `cli.py`（`plugin *` 支持 advisor、新增 `advisor ask`）
- 验收：安装/卸载/漂移/恢复；`advisor ask --preview` 不联网
- **Status:** pending

### Phase 5：Core 阶段 4 —— 文档与门禁（pending）

- 文件：`docs/concepts/plugin-model.md`、新 `docs/development/advisor-plugin-development.md`、
  `docs/reference/plugins/index.md`、`docs/rfcs/index.md`、`mkdocs.yml`
- 验收：`audit_docs.py` / `scripts/generate_docs.py --check` / `scripts/docs_impact.py` 全过
- **Status:** pending

### Phase 6：外部事实核验 + kill criteria（pending）

- [ ] `typesafe-sdk` license、是否有 on-prem、中国大陆实测延迟、中文精度实测
- [ ] 三个候选功能各自对照确定性基线（打不过就不做）
- **Status:** pending

## Key Questions

1. 是否接受 `advisor` 作为第二个插件类别（而不是塞进 `PluginKind`）？
2. 一个 wheel 能否同时提供 instrument 与 advisor 插件？（建议首版禁止）
3. advisor 插件能否声明第三方运行时依赖？`plugin install` 目前离线 `--no-deps`
4. 数据外发同意的粒度：每次确认，还是可写入配置并记录有效期？
5. `advisor.external_state` 是否纳入 access policy（用现有 capability/权限模型拒绝）？
6. decision artifact 是否进入 `run report`（Core 改动），还是先只落盘、由插件自渲染？

## Decisions Made

| Decision | Rationale |
|---|---|
| 形态走插件，而非独立包 | 用户决定（2026-09-28） |
| 新增"插件类别"抽象，而不是给 `PluginKind` 加 `advisor` | 仪器语义（`models` 必填、capability 前缀 `{kind}.`）不适用于 advisor，混入会污染两套规则 |
| 数据外发必须由用户显式确认 | 用户决定（2026-09-28）；Core 拥有同意门，插件只声明 egress |
| decision artifact 挂在对应 run 目录下 | 用户决定（2026-09-28）；附加式，不改 Core 拥有的 `run.json`/`summary.csv`/`steps/*` |
| Core 内置确定性 baseline advisor | 让新类别在 CI/离线下可测，并让"必须打败确定性基线"可执行 |
| 模型版本固定 `jev-1.13.0`，artifact 同时记 `reported_model` | 官方文档说明别名会漂移 |
| 规划文件随草案入仓（`docs/project/design/advisor-draft/`），支线 `feat/advisor-plugin-category` 按 PR 流程推进 | 用户要求把草案落到本仓并同步 competition；PR #4 track 的规划文件仍在 `D:\Documents\Code\Lab_com\` |
| （1a）引入类别抽象，不给 `PluginKind` 加 `advisor` | 已裁定 |
| （2a）首版禁止一个 wheel 同时提供两类插件 | 放宽是向后兼容的，收紧不是；混装会显著增加账本与所有权检测复杂度 |
| （3a）advisor 可声明第三方依赖，install 保持离线 `--no-deps` | 保留项目离线承诺；依赖只从 `Requires-Dist` 读，`plugin doctor` 报缺失 |
| （4c）默认按次同意 + 可登记 run 级；同意绑定 endpoint 集合与允许字段集 | 兼顾可操作性与"配置悄然扩大外发范围"的防护 |
| （5b）`advisor.external_state` 不纳入 access policy | 权限模型本次不动，治理面留在 `[advisor]` + 同意门 |
| （6a）decision artifact 不进 `run report` | 避免把概率语义焊进 Core 报告契约；待 schema 稳定再议 |

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|
| advisor 装不进现有插件契约（`plugin must declare [wavebench.instruments]`） | 1 | 提出"插件类别"抽象 + 新组 `wavebench.advisor` |
| `test_release_artifacts` 失败（hatchling 打 sdist 读到 `.tmp-pytest`） | 1 | 移入回收站后 2 passed；与改动无关，属环境残留 |
| 相位用相关峰 lag，90° 报成 275° | 1 | 改用基波频点 DFT，0/45/90/135/180/270 全部精确 |
| 三角波对称度滞回判据失效（锚点跟着当前值走） | 1 | 方向未确认时锚点固定；再加相邻斜率交点还原折点 |
| 错把 `_load_scope_expectations` 当作校验入口，误判"typo 被接受" | 1 | 严格校验在 payload 函数里、早于 `load_config`；用不存在 config 复验 |
| `api.github.com` TLS 握手失败（本地代理），gh/curl/python 全挂 | 1 | 等待约 4 分钟恢复；未使用 TLS 校验绕过 |
| 控制台 GBK 把中文路径/输出显示成乱码 | 1 | 只影响可读性，改用文件读取与 ASCII 标识判断 |

## Notes

- 本 track 的规划文件：`task_plan.md` / `findings.md` / `progress.md`（均在 draft 目录）
- PR #4 track 的规划文件：`D:\Documents\Code\Lab_com\task_plan.md` 与 `progress.md`
- 红线：advisor 不参与任何写路径、安全门、质量门、capability 判定
- 每完成一个 Phase 更新状态；重大决策前重读本文件
