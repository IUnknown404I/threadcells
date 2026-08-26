---
slug: control-plane-artifacts
source: docs/CONTROL_PLANE_ARTIFACTS.md
source_sha256: sha256:bbb3ff2ed634050407d78c0fff79f097ecc2c8f29b783d422a52469c624fd8b7
---
# 控制平面工件与 AI 工作流

ThreadCells 在本地候选版本的 `schemas/v1` 以及 wheel 包的 `cli_agent_orchestrator/public_schemas/v1` 下发布 ProfileDefinition V1、ProviderConfiguration V1、AdapterManifest V1 和 AdapterCapabilities V1 的 JSON Schema Draft 2020-12 文档。

使用 `threadcells profiles schema|example` 或 `threadcells providers schema|example` 获取起始文档。导入前请进行验证。字段失败会以稳定的 JSON pointer 记录呈现，而不会反射原始值。UI、CLI 和 API 导入均调用同一服务，并创建不可变修订版本。

## AI 辅助工件工作流

1. 从 `/api/v1/profiles/ai-prompt` 或 `/api/v1/providers/ai-prompt` 获取相关 schema、示例和安全生成提示。
2. 要求模型只返回一个 JSON 对象。不要提供凭据、私有路径、可执行命令、shell 标志或未经审查的 MCP 命令。
3. 手动检查标识符、提供商引用、权限、工具、超时设置和说明。
4. 运行 `validate`；处理每一项 JSON pointer 问题。
5. 仅在操作员审查后导入。需要通配符工具或其他特权权限的导入，必须走独立的受信任操作员路径。
6. 启动前使用解析后的预览，并在导入后导出，以确认经过脱敏的规范工件。

AI 生成的 JSON 是不受信任的输入。看似合理的文档不会安装适配器代码、注册 MCP 能力、授予所有者授权，或绕过仓库策略。内置配置文件始终不可变，导出内容绝不包含提供商凭据或启动授权。
