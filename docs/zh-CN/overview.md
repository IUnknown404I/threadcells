---
slug: overview
source: docs/OVERVIEW.md
source_sha256: sha256:c4082c5da946df3936a8eb1c711b4701ba75b6dfa5000d82bc5f5416d8322f3e
---
# 从这里开始：ThreadCells 是什么？

ThreadCells 是一个自托管系统，用于在 Linux 机器上将多个编码智能体作为一个协调的工作流运行。它为智能体提供真实终端和 Git worktree，让开放任务能够跨模型轮次持续推进，同时让操作员掌控容量、写入权限、受保护的变更以及最终结果。

如果你会用 Git、SSH 和命令行编码智能体，就已经具备开始所需的背景知识。在启动有用的工作前，无需了解 ThreadCells 的内部架构。

## 为什么要使用它？

单个编码智能体终端很容易理解。多个终端则更难：两个智能体可能编辑同一分支，构建可能耗尽内存，主管智能体可能在收集审查前消失，而一个已完成的终端也不一定意味着请求的任务已经完成。

ThreadCells 明确呈现这些关系，并维护自己的运行环境。它在以下场景尤其有用：

- 让长时间运行的智能体保持可见且可重新连接；
- 为并行工作者提供独立管理的 worktree；
- 让主管智能体委派实现和审查；
- 让结果和 Inbox 消息无需在终端之间手动复制即可返回；
- 在提供商轮次和正常重启之间延续一个逻辑任务；
- 分别限制模型轮次、活跃工作和繁重主机任务；
- 即使终端退出后仍保留结果；
- 监测主机压力，并安全清理可处置的 ThreadCells 运行时、日志、缓存、构建和发布残留；
- 在敏感或含糊的步骤前要求所有者作出决定。

ThreadCells 面向一位受信任的操作员，或一支在自己控制的主机上工作的受信任小团队。它不是对抗性多租户沙箱。

## 基本循环

```text
Create a session and choose a project and agent
        ↓
Give the agent or supervisor the job
        ↓
Watch the coordinated workflow and host state
        ↓
ThreadCells continues eligible work across model turns
        ↓
Step in only for an explicit owner decision or final review
```

智能体仍通过其原生提供商 CLI 运行。ThreadCells 协调周边工作；它并不替代提供商。Housekeeping 在仅回收能够证明所有权和资格的候选项时，会保护活跃工作、持久化状态、备份以及当前/恢复发布版本。这减少了手动看管 ThreadCells 残留物的需要，但并不保证物理主机永远不会故障。

## 有效的第一个小时

1. 按照[快速设置](../QUICK_SETUP.md)构建并验证本地候选版本。
2. 如需了解每一步的原因或需要前置条件帮助，请使用[安装](INSTALLATION.md)。
3. 按照[你的第一个项目和智能体](FIRST_AGENT.md)操作。
4. 在看到一次智能体运行后，阅读[核心概念](CONCEPTS.md)。
5. 在使用另一台机器前，从[远程访问](REMOTE_ACCESS.md)中选择一种安全方法。

之后，[提供商](PROVIDERS.md)、[配置文件](PROFILES.md)和[工作流与持久化结果](WORKFLOWS_AND_RESULTS.md)会说明主要运行模型。[运维](OPERATIONS.md)涵盖保持安装健康的日常检查。

## ThreadCells 不做什么

ThreadCells worktree 组织写入；它们不会将智能体与主机隔离。ThreadCells 也不会为 Web UI 增加通用登录保护。请保持服务器仅监听 loopback，并通过 SSH 转发或经过身份验证的反向代理进行远程访问。

当前版本是技术预览。在将有价值的仓库交由智能体控制前，请阅读[安全模型](SECURITY_MODEL.md)和[限制](LIMITATIONS.md)。

## 创建者与维护者

ThreadCells 由 [Subaev Ruslan](https://github.com/IUnknown404I) 创建并维护，ThreadCells 社区也参与贡献。它源于一项实际需求：以更强的运行控制、持久化结果和资源安全，运行多个原生 CLI 编码智能体。
