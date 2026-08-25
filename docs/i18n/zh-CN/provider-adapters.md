---
slug: provider-adapters
source: docs/PROVIDER_ADAPTERS.md
source_sha256: sha256:1b3bda3574765fd4b540f7460e14a1677a3d3dd58be8bf9d07f5fba0c53df1d9
---
# 提供商适配器编写

这是面向维护者的高级指南，用于添加受信任的提供商集成。在内置提供商之间进行选择的操作员应先阅读[提供商](PROVIDERS.md)。

ThreadCells Provider Adapter API V1 是一个受信任代码扩展边界，与观察者插件不同。请将适配器作为经审查的 Python 包安装，这些包在 `threadcells.provider_adapters.v1` entry-point group 下注册 `ProviderAdapterDefinition` 对象。安装后重启本地 candidate/runtime，以便重新发现 entry point。

## 契约

适配器定义提供：

- 一个 `AdapterManifest`，包含稳定的 `adapter_id`、插件 API `1.0`、实现版本、描述、能力和 JSON 配置 schema；
- 用于声明式设置的 `AdapterSettings` Pydantic 模型；
- 一个接受 `ProviderLaunchContext` 和已验证设置的工厂；
- 一个预检函数，返回规范化状态、安装、认证、版本、兼容性、模型、原因代码和不含密钥的消息。

返回的提供商通过现有 `BaseProvider` 生命周期实现规范化的启动/恢复/取消、终端状态/结果、用量和健康语义。请如实声明不支持和有条件支持的能力。绝不要虚构 CLI 未报告的用量。

## 信任和配置

适配器包是可执行的，因此只能由受信任的主机操作员安装。注册表 JSON 不能选择二进制文件或注入命令。ThreadCells 会递归拒绝可执行文件、命令、shell、参数、标志、环境、凭据、密码、token 和密钥键。原始密钥绝不属于 `settings`；请使用语义化的不透明 `secret_refs`，并仅在受信任的适配器代码中按照安装的密钥策略解析它们。

保持错误以稳定原因代码和可安全公开的消息规范化。预检不得修改提供商设置或代替操作员认证。

## 示例

已安装的 source/candidate 包含 `examples/provider-adapters/threadcells-echo`，这是一个确定性包和 manifest，演示 entry point、schema、配置验证、生命周期、预检及不受支持的用量。它不是模型提供商，且默认禁用。请在安装前独立构建/测试它。

位于 `schemas/v1/adapter-manifest.schema.json` 和 `schemas/v1/capabilities.schema.json` 的打包 schema 是可移植工件引用。已安装代码仍以 Python 契约验证为准。

## 就绪状态必须真实

使用提供商的规范可执行文件名和有边界、非变更性的探测。预检回答安装、兼容性、可安全检测时的认证，以及可安全公开的失败原因。它不得声称适配器注册会使 CLI 可用。

注册表 API、Settings 和 Spawn Agent 均投影同一结果。请添加覆盖，证明未安装命令会被禁用、认证失败可与命令缺失区分，以及认证确实不可知的已安装提供商仍被标为未验证。

## 用量必须真实

优先使用提供商原生的结构化事件，而不是终端文本解析。只记录提供商发出的字段，保留累计检查点身份，并使重启/重放具有幂等性。绝不要将不可用指标变成零，或在没有明确提供商契约时根据 token 估算成本。

## 审查清单

- 稳定的适配器 ID、版本、显示名称和配置 schema。
- 不允许调用方选择可执行文件、shell、参数、环境或原始密钥字段。
- 有边界的预检，不修改设置或认证状态。
- 如实描述支持/有条件支持/不支持的能力。
- 启动、状态、取消和可恢复失败的生命周期测试。
- 遥测受支持时的精确用量测试。
- 注册表/Settings/Spawn 一致性测试。
- 不含凭据或私有路径的可安全公开错误。
