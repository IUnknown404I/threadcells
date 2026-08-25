---
slug: comparison
source: docs/COMPARISON.md
source_sha256: sha256:43b9f58d4b33db88b6d6271456b89ce9008759f75a7bcf1dbb7ce02657a1ccee
---
# ThreadCells 的定位

ThreadCells 面向已经认可原生编码代理 CLI、但需要更清晰地在一台机器上操作多个此类 CLI 的开发者。

## 与独立终端窗口相比

独立的 tmux shell 很简单，但它们不会自动记录配置文件/提供商身份、托管写入者所有权、容量准入、工作流亲缘关系、持久子项结果或操作员关卡。ThreadCells 保留原生终端，同时添加这些运行记录。

## 与托管代理平台相比

ThreadCells 是自托管且以回环优先的。仓库、终端和协调数据库都保留在操作员主机上。相应地，操作员负责安装、提供商认证、备份、修补、资源配置和远程访问保护。

## 与容器或安全沙箱相比

ThreadCells 并非此类产品。托管工作树和权限策略可减少协调失误，但不会将原生提供商进程与操作系统账户隔离开来。

## 与自主软件工厂相比

ThreadCells 强调有边界的委派、可检查的终端、明确的结果、所有者决策和有证据支持的完成。它并不承诺代理可在未经审查的情况下交付任意软件。

ThreadCells 是 AWS Labs CLI Agent Orchestrator 的独立下游项目，并在需要时保留兼容的 `cao` 内部机制。它不是 OpenHands 或 Hermes 等无关代理产品的直接替代品。应为本地原生 CLI 操作和持久的主管/工作者控制而选择它，而不是为了托管多租户或广泛的平台抽象。
