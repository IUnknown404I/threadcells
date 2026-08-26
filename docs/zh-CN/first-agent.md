---
slug: first-agent
source: docs/FIRST_AGENT.md
source_sha256: sha256:0695738b5c690bf05b93bbd5a0afd0e1ab38857a7f488141af3244ce66dae948
---
# 你的第一个项目和智能体

本教程启动一个刻意保持小规模的智能体，并说明在哪里找到它的终端和结果。请先完成[快速设置](../QUICK_SETUP.md)，并让 ThreadCells 服务器保持运行。

## 1. 准备安全的仓库

首次运行时使用可丢弃或干净的 Git 仓库。ThreadCells 以仓库识别项目，并可在其旁创建管理的 worktree。

```bash
mkdir -p /tmp/threadcells-first-project
cd /tmp/threadcells-first-project
git init
printf '# First project\n' > README.md
git add README.md
git commit -m 'Create first project'
```

预期结果：`git status --short` 不输出任何内容。从干净状态开始，可更容易检查智能体的变更。

## 2. 打开 ThreadCells

在运行 ThreadCells 的机器上打开 `http://127.0.0.1:9889`。若主机是远程的，请先建立[远程访问](REMOTE_ACCESS.md)所述的 SSH 隧道。

打开 **Spawn Agent**，选择仓库作为项目，并选择一个已安装的提供商。标为 **CLI not installed** 的提供商无法启动；如果预期的提供商不可用，请参阅[提供商](PROVIDERS.md)。

为这个第一个任务选择通用工作者配置文件。输入如下有界提示：

```text
Add a short Usage section to README.md. Do not change any other file.
Run git diff --check and report the changed file.
```

启动智能体。

## 3. 观察终端

新智能体会出现在 **Agents** 下。它的终端是真实的 tmux 会话，因此提供商原生输出保持可见且可重新连接。ThreadCells 围绕该终端记录项目、配置文件、提供商和会话标识。

预期结果：状态从 starting 变为 running，出现提供商输出，并且在模型生成一轮输出时，容量会反映一个活跃的提供商执行。

若智能体始终无法启动，请检查提供商可用性标签和容量卡片。[故障排除](TROUBLESHOOTING.md)提供按症状分类的检查。

## 4. 检查工作

智能体结束后，检查它的持久化结果和仓库 diff。终端到达最终提供商消息是证据，但不是合并、发布或部署的许可。

```bash
cd /tmp/threadcells-first-project
git status --short
git diff -- README.md
```

若智能体在管理的 worktree 中工作，请使用 ThreadCells 显示的 worktree 路径，而非原始仓库路径。worktree 会将并发写入者隔离开，直到有意协调其提交。

## 5. 尝试监督

理解单个工作者后，在另一个小任务上启动主管配置文件。要求它委派一个实现任务和一个独立审查。关系应如下所示：

```text
Owner
  └── Supervisor
        ├── Developer
        └── Reviewer
              ↓
        Durable results return to the supervisor
```

主管智能体仍负责整合这些结果并完成顶层工作流。一个工作者完成并不会关闭主管智能体的任务。

## 后续步骤

- 学习 UI 中使用的名称：[核心概念](CONCEPTS.md)。
- 创建自定义配置文件前先了解配置文件：[配置文件](PROFILES.md)。
- 了解委派如何在终端完成后继续存在：[工作流与持久化结果](WORKFLOWS_AND_RESULTS.md)。
- 保守地规划机器容量：[容量与资源模型](RESOURCE_MODEL.md)。
