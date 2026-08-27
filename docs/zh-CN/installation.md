---
slug: installation
source: docs/INSTALLATION.md
source_sha256: sha256:4e73fbde9d7c7f25fcb1211daa1e2509593f4670d8c9eaadf689905a8a5a810f
---
# 安装

本指南说明受支持的本地安装路径，以及 ThreadCells 为何从经过验证的候选版本安装。若只需要命令，请使用[快速设置](../QUICK_SETUP.md)。

## 受支持的基线

当前技术预览支持单台 Ubuntu/Debian Linux 主机。ThreadCells 假定使用受信任的操作员账户和本地 Git 检出。其他 Linux 发行版可能可用，但不属于受支持的基线；macOS 和 Windows 可以远程访问 Web UI，但不是受支持的 ThreadCells 主机。

## 前置条件

安装或确认以下内容：

- Python 3 和 `venv` 支持；
- Git；
- tmux；
- 用于构建打包 Web UI 的 Node.js 和 npm；
- 发布和服务脚本使用的常见 POSIX 工具；
- 一个受支持的提供商 CLI，已为将要运行 ThreadCells 的账户安装并完成认证。

检查重要命令：

```bash
python3 --version
git --version
tmux -V
node --version
npm --version
```

ThreadCells 可以注册其 CLI 缺失的适配器。这不是安装失败；只有打算启动的提供商必须就绪。请参阅[提供商](PROVIDERS.md)。

## 状态存放位置

默认情况下，运行状态位于：

```text
~/.aws/cli-agent-orchestrator/
```

保留这个历史目录名是为了兼容性。它可包含 SQLite 数据库、日志、管理的 worktree、智能体上下文、附件、提供商产物及其他运行时状态。在首次启动前设置 `CAO_HOME_DIR`，可选择其他绝对位置。

已安装的应用与其运行时状态不同：

- **candidate/install** 包含有版本的代码和静态 Web 资源；
- **state root** 包含数据库、可变的操作员数据，以及可选的由 ThreadCells 管理且访问受限的秘密文件，例如 Telegram bot token；
- 提供商 CLI 可能在其他位置保留各自的凭据和发布历史。

在替换安装前备份可变状态。切勿提交运行时状态或提供商凭据。

## 为什么要使用本地候选版本？

候选版本是从一个精确的源修订构建出的、具备发布形态的目录。其清单和校验和让你能在触及安装之前验证将要运行的内容。暂存和提升随后可以保留旧候选版本用于回滚。

这种方式比直接从持续变化的检出运行更审慎，但它可避免 Web UI、Python 代码、文档和构建标识悄然来自不同修订。

## 构建候选版本

在仓库根目录中运行：

```bash
python3 scripts/build_local_candidate.py --output "$PWD/threadcells-candidate"
candidate="$PWD/threadcells-candidate/threadcells-0.3.3a0-local"
python3 scripts/verify_local_candidate.py --candidate "$candidate"
```

预期结果：验证器接受清单、校验和、打包文档和应用文件。不要安装验证失败的候选版本。

## 预览并安装

选择运行时账户可执行的绝对前缀。下方仓库本地前缀便于评估：

```bash
"$candidate/scripts/install-threadcells.sh" --source "$candidate" --dry-run
"$candidate/scripts/install-threadcells.sh" --source "$candidate" --prefix "$PWD/.threadcells"
```

dry run 有意先执行。审阅其源和目标后，再执行实际安装。

## 验证已安装的 CLI

```bash
"$PWD/.threadcells/venv/bin/threadcells" info
"$PWD/.threadcells/venv/bin/threadcells" doctor
"$PWD/.threadcells/venv/bin/threadcells" providers list
```

`doctor` 是只读的。解决缺失的必需系统工具。提供商输出应能区分已知适配器与已安装且可用的 CLI。

## 本地启动

```bash
"$PWD/.threadcells/venv/bin/threadcells-server" --host 127.0.0.1 --port 9889
```

在另一个 shell 中：

```bash
curl -fsS http://127.0.0.1:9889/health
```

打开 `http://127.0.0.1:9889`。检查 Settings → About，并确认其版本和修订与所验证的候选版本一致。

对于持久安装，请使用仓库在[部署](DEPLOYMENT.md)中描述的规范服务/部署机制。不要临时拼凑一个公共绑定地址。

## 初始失败

- **`python3 -m venv` 失败：** 安装该发行版的 Python venv 软件包。
- **`tmux` 缺失：** 启动智能体前安装它；终端持久化依赖于它。
- **Web 资源构建失败：** 使用受支持的 Node/npm 基线，安装锁定依赖，然后重新构建候选版本。
- **提供商提示 CLI not installed：** 为运行时用户安装该提供商的规范命令，或选择已经就绪的提供商。
- **提供商已安装但未认证：** 以运行时用户身份完成提供商自身的登录流程，然后再次执行预检。
- **端口 9889 被占用：** 停止冲突的本地进程，或选择另一个 loopback 端口并始终一致地使用它。
- **另一台机器上的浏览器无法连接：** 对于 loopback 监听器，这是预期行为。请使用[远程访问](REMOTE_ACCESS.md)。

## 移除边界

移除安装前缀不会安全地移除运行状态、提供商凭据、Git 仓库、worktree、备份或服务定义。停止 ThreadCells，创建经过验证的备份，并分别识别每个类别。对符合条件的运行时产物使用 Housekeeping；不要将递归删除状态根目录作为卸载捷径。

接下来，请按照[你的第一个项目和智能体](FIRST_AGENT.md)操作。
