---
slug: profiles
source: docs/PROFILES.md
source_sha256: sha256:c378cfce9445d9171027ab61113863019c5478942bbdf218c8cdd1a6a608c552
---
# 配置文件

配置文件是代理可复用的启动策略。它回答：应运行哪个提供商和模型、应使用多少推理强度、应接收什么角色和指令，以及允许哪些能力或权限？

大多数用户应从内置配置文件开始，并查看其解析后的预览。正常使用无需编写原始 JSON。

## 配置文件控制什么

解析后的配置文件可以包括：

- 提供商配置、模型和推理强度；
- 主管、开发者、审查者或专家等角色；
- 指令和技能引用；
- 允许的工具和 MCP 能力；
- 超时和执行行为；
- 写入者或所有者级别的权限约束；
- 它是否旨在常驻，还是完成有边界的工作。

模型能力和编排角色彼此独立。强大的模型不会自动成为主管，配置文件的名称也不决定如何计入容量。

## 内置配置文件

ThreadCells 为常见角色提供不可变配置文件，包括日常和更强的主管、开发者、审查者、架构和战略工作、前端/UI 工作，以及一个经过严格所有者授权的 XHigh 执行器。

示例：

- `supervisor_terra_medium`：普通及中等风险工作流的默认常驻编排者；负责分解、委派、审查、验收和集成。
- `supervisor_sol_medium`：面向风险较高、跨模块、架构敏感或生命周期敏感工作流的编排优先主管。
- `developer_terra_medium`：常规、有边界且歧义较低的实现工作。
- `developer_terra_high`：重要产品工作、困难但有边界的缺陷与重构，以及公开语义质量工作。
- `developer_sol_medium`：需要深入推理、涉及跨子系统和细微不变量的工作。
- `reviewer_sol_high`：用于有风险或集成变更的独立审查。
- `critical_sol_xhigh_owner`：具有独立授权边界的例外所有者执行器配置文件。

内置配置文件不可变，因此熟悉的 ID 不会悄然改变含义。若要自定义，请复制它；副本会获得自定义标识。

## 选择配置文件

使用能够可靠负责该任务的最少专门化配置文件：

| 任务 | 起点 |
| --- | --- |
| 小型有边界代码改动 | developer |
| 独立验收审查 | reviewer |
| 多条相互依赖的工作流 | supervisor |
| 架构或迁移设计 | architecture/strategy specialist |
| 产品 UI 实现 | frontend 或 UI/UX specialist |
| 关键前沿所有者执行 | 仅限经所有者授权的 XHigh |

更高的推理强度和更广的权限会消耗容量并增加后果。它们应反映任务本身，而不应成为默认选择。

使用 Sol 主管并不意味着必须使用 Sol 开发者。它仍应将常规实现路由给 Terra 开发者，只为正确性依赖细微跨系统推理的工作保留 `developer_sol_medium`。

## 重试与升级

ThreadCells 会先对失败的实现尝试分类，再选择下一个智能体：

| 失败类别 | 规范响应 |
| --- | --- |
| `OPERATIONAL_FAILURE` | 可以考虑同级重试。 |
| `MECHANICAL_INCOMPLETE` | 允许一次有边界的同级修正。 |
| `SEMANTIC_QUALITY_FAILURE` | 提升实现层级；绝不进行第三次同级语义尝试。 |
| `BOUNDARY_COMPLEXITY_UNDERESTIMATED` | 选择能力更强的开发者。 |
| `CRITICAL_SYSTEMIC_BOUNDARY` | 使用经所有者授权的 `critical_sol_xhigh_owner`。 |

常规升级路径为 `developer_terra_medium` → `developer_terra_high` → `developer_sol_medium`。XHigh 仅用于真正关键的系统权限，例如安全、exactly-once 并发、破坏性 Housekeeping、迁移或危险恢复。测试通过是必要证据，但其本身不能证明语义质量。

## 解析后的预览

Settings → Profiles 同时显示保存的工件和其**解析后的预览**。启动前请使用该预览，确认在应用默认值和引用后实际使用的提供商、模型、推理、角色、工具、权限、超时和指令。

新的启动会原子地捕获该解析后的修订版本。之后编辑自定义配置文件会创建另一个不可变修订版本，不会重写已有会话的历史含义。

在修订快照出现前创建的旧会话可能显示 `legacy/unavailable snapshot`。ThreadCells 不会虚构过去的配置。

## 创建自定义配置文件

最安全的路径是：

1. 打开 Settings → Profiles。
2. 选择最接近的内置配置文件。
3. 复制它。
4. 为副本赋予清晰、基于角色的名称。
5. 只更改必要的最小字段。
6. 检查解析后的预览。
7. 在更广泛使用前，以它进行一次有边界的测试启动。

自定义编辑会创建修订版本。被历史记录引用的配置文件会被禁用，而不会被破坏性删除。

## 专用权限和所有者权限

不受信任的导入无法创建所有者执行器、XHigh、无限制或 `danger-full-access` 权限。已认证的操作员只能通过受保护的控制平面创建特权自定义修订；服务器仍要求在启动时使用相应的一次性所有者授权。

内置 `critical_sol_xhigh_owner` 配置文件可在两个 Web 启动流程中选择：创建会话或向现有会话添加代理。两者都会显示例外权限提示，要求明确确认及短时有效的操作员解锁，然后才签发并消耗一份正常启动能力。Add Agent 会将该能力限定到现有会话及规范继承/项目工作目录。本地 CLI 通过 `--owner-xhigh` 和交互式确认提供相同的权限类别。这些路径均不会创建可复用的 API 绕过，也不会授权其他配置文件、子终端或无关的 Settings 更改。

## 配置文件和容量

顶层主管或所有者会话消耗常驻主管容量。委派的子项消耗工作上下文槽位。提供商执行和重型执行会根据活动分别计费，而不仅仅因为配置文件含有 `supervisor` 或 `reviewer`。

在为强大配置文件提高并发前，请参阅[容量和资源模型](RESOURCE_MODEL.md)。

## 高级导入和导出

CLI 提供当前 schema 和示例：

```bash
threadcells profiles schema
threadcells profiles example
threadcells profiles export
threadcells profiles validate /path/to/profile.json
threadcells profiles import /path/to/profile.json
```

导入前请验证。导入使用与 UI 相同的服务端验证，且不能引入可执行 MCP 命令。它们可以引用已安装的提供商配置和已注册的能力标识符。

不要手动编辑数据库行，也不要将私有指令、文件系统路径、凭据或内部所有者状态复制到公开的配置文件工件中。

## 常见错误

- 仅根据模型名称选择配置文件。
- 向日常工作者授予所有者级权限。
- 编辑自定义配置文件后未检查解析后的预览。
- 期望编辑会改变已运行会话。
- 导入原始密钥值而非批准的引用。
- 将配置文件当作提供商安装；所选 CLI 仍必须就绪。

接下来请参阅[工作流和持久结果](WORKFLOWS_AND_RESULTS.md)，了解主管和工作者配置文件如何协作。
