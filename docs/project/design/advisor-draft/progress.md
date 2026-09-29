# Progress Log

## Session: 2026-09-28

### Phase 0：前置（complete）

- **Status:** complete
- Actions taken:
  - PR #4（agent scope observation）复审修复：按复审 6 条重做（MCP 纯只读 + 波形走显式 CLI、
    跨采集跳过时序关系、expectation 严格校验、相位改频域、对称度抗噪、低置信度不给时基建议）
  - rebase 到 `master@44506c4`；`--force-with-lease` 推送（`3d57a60 → e212170`）
  - 在 PR #4 下逐条回复（comment-5865190612）；更新 PR 描述
  - Jev 一手事实核验（官方 blog / docs / models / jaggedness / privacy + PyPI + LangChain）
  - seam 草案 v2：`DecisionState`/`QuestionSpec`/`PreviewPayload`/`ConsentDecision`/
    `ThresholdPolicy`/`DecisionArtifact`/`artifact_path`/`write_artifact`
  - advisor 插件 RFC 草案（源码级阻塞证据 + 类别抽象 + 分期方案）
- Files created/modified:
  - `docs/project/design/advisor-draft/README.md`（草案 v2）
  - `docs/project/design/advisor-draft/decision_seam.py`
  - `docs/project/design/advisor-draft/test_decision_seam.py`
  - `docs/project/rfcs/WaveBench_advisor插件RFC.md`（PR #20）
  - `D:\Documents\Code\Lab_com_pr4\`（PR #4 分支：2 个提交 + 14 个文件改动）
  - `D:\Documents\Code\Lab_com\{task_plan,progress}.md`（PR #4 track 的规划文件）

### Phase 1：RFC 定稿与裁定（complete）

- **Status:** complete
- Started / finished: 2026-09-28
- Actions taken:
  - 建立本 track 的规划文件（task_plan / findings / progress）
  - 六项裁定：1a / 2a / 3a / 4c / 5b / 6a
  - 裁定写回 `WaveBench_advisor插件RFC.md`（新增「已裁定」表 + 扩充验收门）与 `README.md`（§5 同意模型、§6 不进 run report）
  - 按 4c 实现同意失效规则：`ConsentDecision(valid_for/run_id/endpoint_hosts/allowed_state_fields)`
    + `consent_covers()`；`Advisor` 新增 `endpoint_hosts` 并在调用前判定覆盖
  - 补测：run 级同意只覆盖同一 run、无 run 目标被拒、endpoint/字段集变化即失效、
    `valid_for="run"` 缺 `run_id` 构造期报错
- Files created/modified:
  - `decision_seam.py`（同意模型 + `consent_covers`）
  - `test_decision_seam.py`（14 → 18 个用例）
  - `WaveBench_advisor插件RFC.md`、`README.md`
  - `task_plan.md`、`findings.md`、`progress.md`

### Phase 1 收尾：RFC 提交（complete）

- **Status:** complete
- Actions taken:
  - 新建工作树 `D:\Documents\Code\Lab_com_advisor_rfc`，分支 `docs/advisor-plugin-rfc`，基于 `origin/master@44506c4`
  - 仓库版 RFC：`docs/project/rfcs/WaveBench_advisor插件RFC.md`（含状态行、约束、事实表、公共模型、兼容性、决策、验收门、分期、不做、风险、未核验）
  - `docs/rfcs/index.md` 新增「进行中的提案（Draft）」小节并登记该 RFC
  - 修正链接深度（`docs/rfcs/index.md` → `docs/project/rfcs/` 为一层 `../`）；修复后 orphan 警告消除
  - 提交 `aa9cdce docs: add advisor plugin category RFC (Draft)`（2 文件 +201）
  - 推送分支到 fork（SSH 曾被本地代理掐断两次，探测恢复后完成）
  - 开 PR：**https://github.com/Scaxlibur/WaveBench/pull/20**；CI 5/5 通过；`MERGEABLE`
- Files created/modified:
  - `docs/project/rfcs/WaveBench_advisor插件RFC.md`（created, 194 行）
  - `docs/rfcs/index.md`（+7）

### Phase 2：Core 阶段 1 —— 类别抽象骨架（complete）

- **Status:** complete
- Actions taken:
  - 新增 `core_skeleton/`（5 个文件 + 说明）：`advisor_api.py`、`advisor_registry.py`、
    `advisor_packages.py`、`builtin_advisors.py`、`test_advisor_skeleton.py`
  - 新增根 `conftest.py`，让 `decision_seam` 与骨架可同时导入
  - 契约：构造期校验（capability 前缀、purpose、endpoint/字段集、API 版本门）；
    registry 过滤与 get；entry point 加载失败与 id 不匹配只产 `PluginLoadError`
  - 裁定 2a：`select_package_category` / `source_entry_point_group` 拒绝混装与空声明
  - 基线 `rule_advisor`：与真实服务同 client 协议、零依赖、不联网；走完整
    `build_state → questions → answers → thresholds → artifact` 链路
  - 修正两处测试自身的问题（异常对象无 `__module__`；两选项均匀 0.50 属 review 区间而非"无输出"）
- Files created/modified:
  - `core_skeleton/{advisor_api,advisor_registry,advisor_packages,builtin_advisors}.py`（created）
  - `core_skeleton/{test_advisor_skeleton.py,README.md}`（created）
  - `conftest.py`（created）
  - `task_plan.md`、`progress.md`（updated）

### Phase 3：Core 阶段 2 —— 同意门 + decision artifact 落 Core（complete）

- **Status:** complete
- Actions taken:
  - 支线 `feat/advisor-plugin-category` 从 `master` 拉出并 cherry-pick PR #20 的 RFC 提交（`39b9d2e`）
  - 草案入仓：`docs/project/design/advisor-draft/`（README／findings／task_plan／progress／seam + 骨架）
  - `services/advisor_consent.py`：三级门 + 预览 + 同意失效判定；模块内无网络代码（有 AST 导入审计测试）
  - `services/decision_artifacts.py`：artifact 正文、阈值策略、独占创建写入器
  - `config.py`：`AdvisorConfig`（append-only 追加字段）+ `[advisor]` 解析与校验
  - `wavebench.example.toml`、`docs/reference/configuration.md`：`[advisor]` 段与「Advisor 外发边界」
- Files created/modified:
  - `src/wavebench/services/{advisor_consent,decision_artifacts}.py`（created）
  - `src/wavebench/config.py`、`wavebench.example.toml`、`docs/reference/configuration.md`（updated）
  - `tests/test_advisor_consent.py`、`tests/test_decision_artifacts.py`、`tests/test_advisor_config.py`（created）
- Verification: 三个新测试文件 29 个用例通过；`ruff`、`docs audit`（122 页 0 error／11 warning）、`generate_docs --check` 全过；
  全量 `pytest -q`：**2489 passed / 1 failed / 4 skipped**（唯一失败是既有基线 `test_windows_job_rejects_allocation`：
  本机 OpenBLAS 内存耗尽报错形态与断言不符，与本次改动无关）

### Phase 4-6

- **Status:** pending（见 task_plan.md）

## Test Results

| Test | Input | Expected | Actual | Status |
|------|-------|----------|--------|--------|
| PR #4 相关测试 | `pytest -q` 7 个文件（含 test_cli） | 全绿 | 185 passed, 3 subtests | ✓ |
| PR #4 全量 | `pytest -q` | 与基线一致 | 2495 passed / 25 skipped；10 failed 中 8 个在纯净 master 同样失败 | ✓ |
| PR #4 CI（上游） | GitHub Actions | 全过 | 5/5 pass（Ubuntu+Windows × 3.11/3.12 + 文档构建） | ✓ |
| seam 草案 v1 | `pytest -q test_decision_seam.py` | 全绿 | 9 passed | ✓ |
| seam 草案 v2（含同意门） | 同上 | 全绿 | 14 passed | ✓ |
| seam 草案 v3（4c 同意失效规则） | 同上 | 全绿 | 18 passed | ✓ |
| RFC PR 文档门禁 | `ruff check .` | 通过 | All checks passed | ✓ |
| RFC PR 文档门禁 | `audit_docs.py` 全量 | 无新增 | 0 errors / 11 warnings（与 master 一致） | ✓ |
| RFC PR 文档门禁 | `audit_docs.py` 改动页 `--strict` | 0/0 | 0 errors / 0 warnings | ✓ |
| RFC PR 文档门禁 | `scripts/generate_docs.py --check` | current | generated Reference is current | ✓ |
| RFC PR 相关测试 | `pytest tests/test_docs_*.py` | 全绿 | 10 passed | ✓ |
| RFC PR #20 上游 CI | GitHub Actions | 全过 | 5/5 pass（含 Windows 3.11/3.12 + 文档构建） | ✓ |
| 骨架契约测试 | `pytest core_skeleton/` | 全绿 | 22 passed | ✓ |
| draft 全量 | `pytest`（seam + 骨架） | 全绿 | 40 passed | ✓ |
| draft 静态检查 | `ruff check --select E4,E7,E9,F --line-length 100 .` | 通过 | All checks passed | ✓ |
| seam 离线自证 | 无 client 调用 | 降级不抛 | `unavailable / advisor_not_configured / 0 建议` | ✓ |
| seam 依赖面 | import 列表 | 无网络库 | `hashlib/json/dataclasses/pathlib/typing` | ✓ |
| 文档门禁（PR #4） | `audit_docs.py` 等 | 无新增问题 | scoped 0/0；全量 0 errors / 11 warnings（与 master 逐条一致） | ✓ |

## Error Log

| Timestamp | Error | Attempt | Resolution |
|-----------|-------|---------|------------|
| 2026-09-28 | `test_release_artifacts` 失败：hatchling sdist 读到 `.tmp-pytest` | 1 | 移入回收站；2 passed（环境残留，非改动引入） |
| 2026-09-28 | 相位用相关峰 lag：90° 报成 275° | 1 | 改基波 DFT，0/45/90/135/180/270 精确 |
| 2026-09-28 | 滞回极值检测失效（锚点随当前值移动） | 1 | 方向未确认时锚点固定 + 斜率交点还原 |
| 2026-09-28 | 误判"expectation typo 被接受" | 1 | 校验在 payload 函数、早于 `load_config`；用不存在 config 复验 |
| 2026-09-28 | `api.github.com` TLS 握手失败（本地代理） | 1 | 轮询等约 4 分钟恢复；未用 TLS 绕过 |
| 2026-09-28 | advisor 无法注册为插件 | 1 | 提"插件类别"抽象 + 新 entry point group |

## 5-Question Reboot Check

| Question | Answer |
|----------|--------|
| Where am I? | Phase 1（RFC 定稿与裁定） |
| Where am I going? | Phase 2-5（Core 分片实施）→ Phase 6（外部核验 + kill criteria） |
| What's the goal? | 给 WaveBench 加 `advisor` 插件类别（含同意门与 decision artifact），以插件方式接入 Jev |
| What have I learned? | 见 `findings.md`（插件契约事实、Jev 规格与已知缺陷、可复用先例） |
| What have I done? | 见上文 Phase 0/1（PR #4 已交付；seam 与 RFC 草案已就绪） |
