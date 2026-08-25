---
slug: architecture
source: docs/ARCHITECTURE.md
source_sha256: sha256:0fa43fdddc696e3203367cd85ab6b0ca6ec9d4c03753ebdfea3fc1d336507447
---
# 架构

ThreadCells 是围绕原生编码代理进程构建的本地控制平面。它有意将提供商终端、Git 仓库、持久化协调状态和浏览器 UI 保持为具有明确边界的独立组件。

如果以下术语对你而言陌生，请先阅读[核心概念](CONCEPTS.md)。

## 系统视图

```text
Browser or installed PWA
        ↓ HTTP / WebSocket on loopback
FastAPI ThreadCells server
  ├── SQLite durable state
  ├── provider/profile registries
  ├── workflow and result service
  ├── capacity and Housekeeping service
  └── tmux/provider adapter control
               ↓
        Native provider CLIs
               ↓
      Git repositories/worktrees
```

## 服务器与 Web UI

FastAPI 服务器公开应用/API，并提供一个生产 Web 构建。React UI 读取实时运行状态，并通过 WebSocket 连接到终端流。

基础 PWA worker 仅缓存带指纹的静态资源。HTML、API、授权、会话、工作流、Statistics、终端、变更操作和 WebSocket 仍依赖网络，因此 UI 无法虚构离线控制平面状态。

Docs bundle 在构建时从 `DOCS_MANIFEST.json` 生成。只有 allowlist 中的公开 Markdown 会进入运行时。

## 持久化状态

SQLite 保存会话、终端、项目、配置文件/提供商修订版本、资源租约、工作流、结果、用量记录、审计事件和调度收据。必须做到恰好一次或可安全重放的操作使用稳定标识和数据库事务，而不依赖短暂的终端输出。

提供商进程和 tmux 会话是外部运行时事实。启动/恢复会将它们与数据库协调；不得假定任一侧的存在就能证明另一侧仍是当前状态。

## 提供商执行

适配器将规范化的 ThreadCells 启动转换为经过审查的原生 CLI 调用。提供商仍渲染自己的终端 UI，并维护自己的认证。适配器报告能力和预检事实，而不是模拟未受支持的行为。

结构化提供商遥测会规范化为持久化用量记录。累计计数器使用稳定检查点，因此轮询和重启不会重复累计总数。

## Git 工作上下文

受管 worktree 共享仓库对象数据库，但隔离检出路径和分支。写入者权限使变更所有权保持明确。worktree 是并发工具，而不是操作系统沙箱。

## 工作流与结果

工作流状态可跨越单个提供商回合持续存在。委派结果会被记录、至少投递一次、由父项纳入，并在符合条件的子项退役前得到确认。只有显式完成（而非模型最终回复）才能关闭顶层任务。

## 准入与压力

常驻主管、提供商执行、工作上下文和重型执行各有独立的租约和限制。磁盘压力和 Housekeeping 保护是额外的运行时约束。跨进程围栏可确保两个进程不会都认为自己获得了最后一个槽位。

## 安全边界

ThreadCells 假定存在一个受信任的主机和操作员环境。常规 UI 访问由回环/SSH 或经过认证的反向代理在外部保护。敏感的 Settings 变更使用独立的操作员验证器/会话边界，但这不是通用登录系统。

提供商包和原生 CLI 是受信任的可执行代码。导入的配置是受约束的声明性数据。请参阅[安全模型](SECURITY_MODEL.md)。
