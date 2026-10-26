# 发布版本

发布以一个确定提交和一组已经验证的发行物为单位。`pyproject.toml` 是唯一可编辑版本源；安装后的版本来自发行包 metadata。保留 Hatchling 构建后端。

## 准备发布提交

1. 选择正式版本，在无动态仪器插件的开发环境中更新 `pyproject.toml`、`uv.lock` 和 editable 安装元数据。
2. 将发布说明写入 `docs/releases/v<version>.md`，首行为 `# WaveBench v<version> 发布说明`；从 CHANGELOG 链接到它。
3. 同步安装示例、相关功能状态和固定版本链接。稳定版文档取自发布 tag，开发线使用独立入口。
4. 运行源码检查、生成 Reference 检查、文档审计及严格构建。PR 和 `Dev` push 的 CI 还会构建发行物，并验证 Linux／Windows × Python 3.11／3.12 的独立 wheel 安装。

本地可在新的输出目录执行与 CI 相同的发行物检查：

```bash
uv build --no-sources --out-dir /tmp/wavebench-release
python scripts/check_release.py --artifacts /tmp/wavebench-release --tag v0.8.28
python scripts/smoke_wheel.py --artifacts /tmp/wavebench-release --version 0.8.28
```

替换示例版本和输出目录。独立 smoke 会创建临时虚拟环境并解析 wheel 的运行依赖，可能访问软件源；不连接仪器，不复用开发环境依赖。现有离线 wheel/sdist 测试仍用于检查包布局。

## tag 验证与 Release 草稿

在获得发布授权、目标提交检查通过后，创建并推送与项目版本一致的 `v<version>` tag。`Release candidate` 工作流复用 CI 和 Documentation 工作流，对该 tag 的提交重新验证：

- 项目版本、锁文件、wheel/sdist metadata、tag 及发布说明一致。
- 源码测试、静态检查、独立 wheel smoke 通过。
- 生成 Reference、文档审计与 MkDocs 严格构建通过。

全部通过后，工作流下载同一次运行的 `distributions` artifact，附加到 GitHub Release 草稿，正文直接取自仓库中的发布说明，不重新构建发行包。站点保存在 `documentation-site` artifact 中；当前流程不部署 GitHub Pages。

维护者检查草稿正文、目标 tag 和附件后，再执行最终发布。工作流失败时不发布，修复后重新验证最终提交；不能借用旧提交的成功结果。已公开 tag 和发行物不覆盖，修复通过新版本交付。

发布后开发线改为下一版本的 `.devN`，重新同步锁文件与安装元数据。稳定使用说明继续指向固定 tag。

## 分支检查

`CI` 覆盖 PR 和 `master`、`main`、`Dev` 的 push。Documentation 在普通分支事件中保留路径筛选；tag 发布工作流直接调用它，不受路径筛选影响。将检查设为分支必需项时，避免选择可能因路径筛选而不运行的工作流。
