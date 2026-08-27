---
slug: web-ui
source: docs/WEB_UI.md
source_sha256: sha256:dc45952406ae34d9be16d78c4e5b4a6f73d8862fe8b5fe6a557528cecaf45928
---
# 使用 Web UI

Web UI 是操作员查看 ThreadCells 实时状态的界面。它面向 loopback 监听器设计，既可在浏览器中正常使用，也可作为已安装的基础 PWA 使用。安装它不会增加离线运行行为或新的认证边界。

![实时 ThreadCells Home，显示密集的会话、智能体和工作流摘要](/media/screenshots/threadcells-home.webp)

## 主要区域

- **Home** 汇总持久化的会话和智能体历史、当前活动、所有者关注事项，以及 First/Last/Total 状态计数，无需加载每个终端。
- **Agents** 提供 Sessions、Statuses 和 Profiles 视图，用于查看终端、配置文件/提供商标识、执行状态、工作流状态和持久化结果。
- **Flows** 用于创建、启用、停用、检查和手动运行循环智能体计划。产生的智能体和工作流生命周期显示在 Agents 下。
- **Statistics** 显示提供商报告的用量，不会编造指标。
- **Settings** 包含 General、Orchestration Capacity、Profiles、Providers、Housekeeping、安装全局的 Telegram 通知和 About。
- **Docs** 提供随运行构建打包的公开 allowlisted 文档。
- **Spawn Agent** 从项目、提供商和配置文件启动新会话。
- **Add Agent** 在精确选定的会话生命周期内启动另一个终端；它不会加入一个仅因同名而出现的不同历史会话。

支持直接 URL。浏览器历史应保留选定的 Settings 和 Docs 页面。

## 常规运行循环

1. 在 Home 中检查当前会话/工作流活动，在 Settings 中检查主机健康、磁盘压力和可用容量。
2. 使用 Spawn Agent，并确认所选提供商已就绪。
3. 在 Agents 下观察新会话。
4. 对循环计划使用 Flows。在 Agents 下跟踪它们启动的智能体。
5. 在退役子级之前阅读并整合持久化结果。
6. 使用 Statistics 了解提供商报告的用量。

状态标签来自持久化控制平面的真实状态。**Processing** 表示一轮正在进行；**Ready** 表示提供商运行时存活且确实空闲。排队标签区分提供商容量耗尽、子级退役障碍和一般工作流继续。所有者关卡徽章始终具有明确类别，而展开的 Owner Decision 面板显示具体的持久化原因。

活跃和历史会话是彼此独立的持久化生命周期。删除一个历史会话只会移除该精确且符合条件的生命周期。删除已退出的终端同样会检查其精确运行时标识、写入者租约、工作流/结果保护和会话关系，然后才清理；含糊或活跃的状态会保持受保护。保留的清理资源不会造成虚假的执行阻塞：在保留受保护文件系统权限以供后续退役的同时，可以为精确生命周期写入墓碑；重复执行同一删除也是安全的。

会话内的智能体始终采用持久化的创建顺序。Home 和 Agents 在 List、Grid、展开、轮询、重连、重启和生命周期变化期间都保持同一顺序。状态、ID、提供商、配置文件、活动和更新时间都不是界面排序键；新智能体会追加到会话末尾。

![实时 Agents 状态视图，已从公开截图中移除本地 worktree 路径](/media/screenshots/threadcells-agents.webp)

## 受保护的设置

敏感变更共用一个 **Unlock operator changes** 控件。缺失、无效、锁定、已解锁和已过期状态彼此不同。最小秘密长度精确为五个字符，默认认证会话持续五分钟。

UI 仅在解锁时发送秘密，随后立即清除，且绝不将其放入浏览器持久化存储或导出。容量、特权配置文件/提供商变更、Telegram 配置/测试、Housekeeping 执行、Full Cleanup 执行和适用的所有者启动，在没有服务器会话时均保持锁定。

Settings → Housekeeping 最底部是 **Delete all system files — Full Cleanup** 危险区块。只读预览会按类别显示可回收空间估算、保护原因、空闲状态、发布版本/worktree，并警告最终只保留活跃发布版本。解锁后仍必须使用现有确认对话框。任何智能体或会改变文件系统的执行处于活跃状态时都无法运行；服务器会在删除前再次检查。结果会报告计划/实际回收量、跳过项、磁盘状态、活跃发布版本和回滚可用性。

请遵循[操作员授权](OPERATOR_AUTHORIZATION.md)安全配置验证器。

## 提供商和配置文件选择

提供商标签区分 **Built-in adapter**、**CLI ready**、**CLI not installed**、**Authentication required**、**Installed but unhealthy** 或 **Readiness unverified**。Spawn 仅禁用已证实不可用的提供商，并使用与 Settings 相同的服务器预检。

配置文件优先提供可搜索的内置/自定义发现和解析后的预览。原始产物导入/导出有意置于 Advanced 下。选择例外的所有者 XHigh 配置文件会显示权限警告，并要求走其单独的授予路径。

## Telegram 通知

Settings → Telegram 独立于项目配置一个安装全局的目标位置。bot token 在 UI 中仅可写；连接和测试消息操作均为显式操作，另有单独确认的清除操作会在移除凭据时禁用投递。启用后的投递仅覆盖顶层完成、需要所有者关注的关卡和意外的顶层终端失败，具有持久化的重复抑制和 fail-open 投递。请参阅[Telegram 通知](TELEGRAM_NOTIFICATIONS.md)。

## Statistics

只要持久化的提供商遥测可用，Statistics 就会包含活跃、完成和保留的未删除会话。缓存输入和推理输出仍彼此分开；不可用字段显示为 **Not reported**。请参阅[Statistics 和提供商用量](STATISTICS.md)。

## Docs 阅读器

Docs 导航按学习历程分组、可搜索，并在宽屏上附带页内大纲。上一个/下一个链接遵循已发布的清单顺序。阅读器仅暴露打包的 allowlisted Markdown；它没有任意文件系统浏览器或编辑端点。

## 完整输出

完整输出在剥离 ANSI/VT 控制序列和终端光标操作后，呈现保留的提供商文本供人工检查。净化会阻止表示控制重写可见历史；它不会重新解释、执行或认证提供商文本。如果 Full Cleanup 在保留已退出智能体元数据的同时安全移除了其旧日志，查看器会报告持久化输出不可用，而不是报错或显示伪造内容。

## 安装为应用

受支持的 Chromium 浏览器可通过浏览器的安装操作安装 ThreadCells。manifest 使用 ThreadCells 品牌，并以独立显示模式打开。iOS 可使用 **Add to Home Screen**。

当操作员访问受浏览器凭据保护时，manifest 及相关同源请求使用相同的凭据边界。跨域访问仍限于明确受信任的 origins；PWA 元数据不会绕过操作员或远程访问控制。

保守的 service worker 仅缓存不可变且有指纹的静态资源。它绝不缓存 HTML 导航、API、操作员授权、智能体、会话、工作流、结果、Statistics、终端、WebSockets 或变更。若服务器不可用，已安装的应用会报告真实的网络失败，而不是呈现过期的运行状态。

新的不可变构建会通过正常的浏览器 service-worker 更新生命周期替换旧的有指纹资源。ThreadCells 不会让操作员停留在过期的离线壳中。

## 响应式与键盘使用

主导航、Docs、Settings、表格和终端控件支持手机、平板和桌面宽度。宽的运行表格会在窄屏上水平滚动，而不会将值缩小到无法阅读的文字。

在手机上，每个 Home 会话标题使用专用名称行和独立的元数据/操作行。智能体卡片始终使用规范的单列列表；List/Grid 选择器隐藏。平板和桌面布局保留其 List/Grid 选择。

使用正常的 Tab/Shift-Tab 导航和可见的焦点指示器。Docs 中的代码块可水平滚动并提供复制控件。终端键盘行为保持提供商原生；触摸滚动不应注入终端输入。

## 访问边界

普通 UI 和 Docs 不提供通用用户登录。请保持 ThreadCells 在 loopback 上。通过[远程访问](REMOTE_ACCESS.md)使用 SSH 隧道或经过认证的 Caddy/Authelia 代理；绝不要直接发布端口 9889。
