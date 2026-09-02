---
slug: getting-started
source: QUICK_SETUP.md
source_sha256: sha256:d464f0e7b472def3633b2727e488d7e9148da9fb28f12976f232c7a4cfb74678
---
# ThreadCells 快速设置

这是从源代码检出到本地 ThreadCells 服务器最快的受支持路径。它会构建不可变的本地候选版本，验证其内容，将它安装在当前仓库下，并且仅监听 loopback。

有关前置条件、失败说明和服务安装，请使用完整的[安装指南](docs/INSTALLATION.md)。

## 1. 检查主机

ThreadCells 当前面向 Ubuntu/Debian Linux，需要 Python 3、Git、tmux、用于 Web 构建的 Node.js/npm，以及至少一个受支持的提供商 CLI。Codex 是主要测试的提供商。

在仓库根目录中运行：

```bash
python3 --version
git --version
tmux -V
node --version
npm --version
```

## 2. 构建并验证候选版本

```bash
python3 scripts/build_local_candidate.py --output "$PWD/threadcells-candidate"
candidate="$PWD/threadcells-candidate/threadcells-0.3.4a0-local"
python3 scripts/verify_local_candidate.py --candidate "$candidate"
```

预期结果：候选版本清单、文件、校验和以及打包的 Web UI 均验证成功。候选版本是一个自包含的、具备发布形态的目录；保持其不可变可识别正在运行的构建，也便于回滚。

## 3. 先预览，再安装

```bash
"$candidate/scripts/install-threadcells.sh" --source "$candidate" --dry-run
"$candidate/scripts/install-threadcells.sh" --source "$candidate" --prefix "$PWD/.threadcells"
```

预期结果：dry run 说明目标且不作改动，随后安装会创建带有 Python 环境和 ThreadCells 命令的 `.threadcells`。

## 4. 运行诊断

```bash
"$PWD/.threadcells/venv/bin/threadcells" doctor
```

启动智能体前请解决失败的必需检查。可选提供商可以缺失；其会在 UI 中显示为 **CLI not installed**。

## 5. 启动服务器

```bash
"$PWD/.threadcells/venv/bin/threadcells-server" --host 127.0.0.1 --port 9889
```

打开 `http://127.0.0.1:9889`。

预期结果：Home 加载，Settings → About 显示正在运行的构建标识，此文档在 Docs 下可用。

首次运行时，请让主机和端口严格保持仅 loopback。若需从另一台计算机访问，不要将监听地址改为 `0.0.0.0`；请使用[远程访问](docs/REMOTE_ACCESS.md)。

运行模型刻意保持简短：创建会话，选择智能体或主管智能体，交付工作，观察工作流，并且只在需要明确的所有者决定或最终审查时介入。仅凭提供商完成并不会关闭一个开放工作流。

## 6. 开始有用的工作

请按照[你的第一个项目和智能体](docs/FIRST_AGENT.md)操作。附带的[安全入门示例](examples/threadcells-starter/README.md)也是一个有界的主管/开发/审查练习，不会发布或变更服务。

## 停止与恢复

使用 `Ctrl-C` 停止前台服务器。智能体终端由 tmux 支撑，可能比浏览器连接存活得更久，但不要假定被中断的服务器已经完成其工作流。重启同一已安装的 `threadcells-server`，打开 Agents，并检查当前状态和持久化结果。

## 接下来阅读

- [核心概念](docs/CONCEPTS.md)
- [提供商](docs/PROVIDERS.md)和[配置文件](docs/PROFILES.md)
- [容量与资源模型](docs/RESOURCE_MODEL.md)
- [Housekeeping](docs/HOUSEKEEPING.md)
- [Telegram 通知](docs/TELEGRAM_NOTIFICATIONS.md)
- [备份与恢复](docs/BACKUP_AND_RESTORE.md)
- [安全模型](docs/SECURITY_MODEL.md)
