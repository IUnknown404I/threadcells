---
slug: projects-and-worktrees
source: docs/PROJECTS_AND_WORKTREES.md
source_sha256: sha256:c296e8fec6654451a29dbef47bde79d19fda28f7bbfdf926c857e2cc8508ad3a
---
# 项目与管理的 worktree

ThreadCells 项目是已注册的 Git 仓库和规范源代码权限。它为会话、配置文件、统计和工作流提供稳定的归属位置，但不是新 supervisor 的常规可写目录。仅注册仓库绝不会让它变得安全，因此请从干净状态开始，并了解你授予的写入边界。

## 注册项目

在 Spawn Agent 中使用项目选择器选择现有仓库，或通过受支持的项目控件添加仓库。使用绝对规范路径，并确认 ThreadCells 运行时用户可以读取它。

在第一个智能体之前：

```bash
git -C /path/to/project status --short
git -C /path/to/project worktree list
```

预期结果：你可以将已有的变更和 worktree 与之后 ThreadCells 创建的内容区分开。已有的未提交工作属于操作员；智能体不得丢弃它。

## 为什么存在管理的 worktree

即使提示彼此无关，同一检出中的两个写入者也可能覆盖对方的编辑。管理的 Git worktree 为每个有界写入者提供自己的检出和分支，同时共享仓库的对象数据库。

```text
Canonical repository
  ├── operator checkout
  ├── Session A supervisor worktree
  ├── Session B supervisor worktree
  ├── developer worktree
  └── reviewer worktree or read-only context
```

ThreadCells 会记录这种关系，而不是将临时目录当作匿名目录。这样做能使清理和结果归属更安全。

每个与项目关联的新 supervisor 会话（包括第一个）都会在准确记录的基础修订版上获得唯一的管理 worktree 和分支。同一项目中的第二个会话会获得另一个 worktree；驻留容量仍是全局的。一个会话仍只有一个主 supervisor，一个可写上下文/worktree 仍最多只有一个写入者租约。替换相同上下文中不可用的 supervisor 时，必须使用显式 recovery takeover 并保留该上下文的 worktree，而不是创建独立 worktree。

早于此契约的活跃旧版会话保留在现有工作区中。升级期间，ThreadCells 不会移动、重置、清理、stash 或复制其脏状态；新会话使用管理 worktree。

## 写入者权限

只有持有写入者权限的上下文才应修改管理的 worktree。审查者可以检查 diff 并运行安全检查，而不会成为未跟踪的第二个写入者。

智能体活跃时，请勿手动编辑其管理的 worktree。若必须紧急介入，请先停止或协调写入者，并记录发生的变更。

## 将工作带回

持久化结果应列出变更文件和检查，但 Git 仍是代码的事实来源。按照你的正常仓库流程，在合并或 cherry-pick 前审阅 worktree 的状态、diff 和提交。

ThreadCells 不授予发布权限。成功的工作者结果不授权推送、打标签、部署或重写历史。

## 清理

Housekeeping 仅在能够证明一个管理的 worktree 不再受活跃终端、工作流、写入者租约或未整合结果保护时，才会移除它。未知所有权会默认拒绝。

若磁盘使用率高，请先规划 Housekeeping。不要直接删除 worktree 目录；这可能导致 Git 元数据和 ThreadCells 状态不一致。

## 常见错误

- 从未记录现有变更的脏仓库开始。
- 为两个智能体授予同一检出的写入者权限。
- 将 worktree 当作安全沙箱。
- 在其结果和提交整合前删除 worktree。
- 假定管理的分支会自动合并或推送。

请参阅[工作流与持久化结果](WORKFLOWS_AND_RESULTS.md)，了解 worktree 结果如何到达主管智能体。
