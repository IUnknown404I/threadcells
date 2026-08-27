---
slug: telegram-notifications
source: docs/TELEGRAM_NOTIFICATIONS.md
source_sha256: sha256:c1c50ae5d9e7937dff2794e49e2914929e7d02e4adb3de0c2f04c7cd5d656735
---
# Telegram 通知

ThreadCells 可以向一个 Telegram 目标发送低噪声的生命周期通知。这是安装级别全局的 ThreadCells 能力：它不属于、不从中读取配置，也不依赖当前选定的项目。

![实时 Telegram 通知设置，其中目标和凭据字段明确已脱敏](/media/screenshots/threadcells-telegram.webp)

## 配置目标

1. 使用 Telegram 支持的机器人管理流程创建或选择一个 Telegram 机器人。
2. 获取目标聊天 ID。对于论坛主题，还要获取其正数消息线程 ID。
3. 打开 **Settings → Telegram** 并解锁操作员变更。
4. 输入机器人 token、聊天 ID，以及可选的主题/线程 ID。
5. 在通知禁用时保存。
6. 使用 **Check connection** 验证机器人凭据，然后使用 **Send test notification** 验证目标。
7. 启用通知并再次保存。

测试操作是显式的；打开 Settings 绝不会联系 Telegram。禁用通知会保留已配置的目标和 token，以便之后重新启用。**Clear bot token** 是单独需要确认的操作员操作：它移除凭据、禁用通知，并保留非密钥目标字段。

## 密钥处理

Web UI 只会在受保护的更新中发送新 token，之后会清空其密码字段。读取 API 只报告 `Configured`、`Not configured` 或 `Invalid`；绝不会返回 token。ThreadCells 不会将 token 放入浏览器存储、终端提示、会话或代理元数据、常规日志或 SQLite settings 行中。

服务器将 token 存储在：

```text
$CAO_HOME_DIR/secrets/telegram-bot-token
```

父目录仅限运行时账户访问，token 文件使用 `0600` 模式。替换使用原子文件系统重命名；清除操作在不跟随它的情况下取消链接凭据，并同步密钥目录。`CAO_HOME_DIR` 是安装的私有可变状态根，而不是公开仓库路径。

请将该文件视为凭据。不要将它复制到源代码控制、普通支持包、数据库导出、shell 历史记录或截图中。若怀疑泄露，请通过 Telegram 轮换它。

## 通知策略

首个发行版的策略会为每个持久的顶层工作流事件最多发送一次尝试：

- 顶层成功完成；
- 顶层需要所有者关注的关卡；
- 顶层终端在其工作流保持打开时意外失败。

ThreadCells 不会针对子项完成、委派、轮询、进度更新、内部重试周期或每个模型/工具回合发送通知。持久事件键可防止重复观察或重启重复已声明的投递。

消息只包含简洁的安全上下文：ThreadCells 标识、会话、存在时的项目显示名称、生命周期状态、固定摘要和 UTC 时间戳。它们不包含提示、模型输出、文件系统转储、异常正文、操作员密钥或机器人 token。

## 失败行为

Telegram 投递对代理工作采用故障开放。超时、被拒绝的凭据或 Telegram 服务不可用会记录安全结果代码，但不能使工作流失败或重新打开。投递只有一次有边界的尝试；ThreadCells 不会在启用通知后无限重试或重放历史事件。

**Check connection** 会通过 Telegram 验证机器人 token。**Send test notification** 还会验证已配置的聊天/主题路由。成功的连接检查并不能证明机器人可以写入所选目标，因此配置新目标时应同时使用两项操作。

## 备份和恢复

非密钥的启用/目标状态和投递账本位于 ThreadCells 数据库中。机器人 token 独立存放。若通知必须在灾难恢复后存续，请将 token 作为单独加密的凭据备份，并保留所有权和模式；不要将它加入常规明文数据库归档。

恢复后，验证密钥路径和权限，先保持通知禁用，运行两项显式检查，再启用投递。没有 token 地恢复数据库会安全地报告 `Not configured`。

## 故障排除

- **Not configured：**在启用前同时提供有效机器人 token 和聊天 ID。
- **无效的 token 存储：**确认 token 是运行时账户拥有的普通、非符号链接文件，且没有组或其他用户权限。
- **连接失败：**检查出站 HTTPS/DNS，并轮换或替换被拒绝的机器人 token；安全 UI 错误会刻意省略 Telegram 响应详情。
- **连接成功但测试失败：**确认机器人属于目标并可在其中发帖；检查聊天和可选主题 ID。
- **没有生命周期消息：**确认已启用，并记住只有顶层完成、所有者关注和意外的顶层失败才会通知。禁用期间发生的事件不会重放。
