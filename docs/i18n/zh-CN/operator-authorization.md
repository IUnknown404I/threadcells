---
slug: operator-authorization
source: docs/OPERATOR_AUTHORIZATION.md
source_sha256: sha256:55cf518ba75105911c8db6d4618c30b1ee63fa26645e7fc8cca063480385e50c
---
# 操作员授权

操作员授权保护 Settings 中敏感的控制平面变更。它与对普通 Web UI 的访问是分开的：浏览代理、终端、文档和统计信息不需要操作员密钥。

此功能不是远程用户认证。请保持 ThreadCells 仅绑定回环地址；当另一台机器需要访问时，请遵循[远程访问](REMOTE_ACCESS.md)。

## 工作方式

ThreadCells 存储从密钥派生的验证器，绝不存储明文密钥。服务器在启动时加载该验证器。输入正确密钥会创建短时有效且安全的操作员会话；它过期后受保护的变更会再次锁定。

```text
Verifier configured
      ↓
Settings shows Locked
      ↓ enter operator secret
Unlock operator changes
      ↓
Short-lived authenticated session
      ↓ expires
Locked again
```

操作员密钥的最小长度严格为 **5 个字符**。四个字符会被拒绝。强烈建议使用更长、随机生成的密钥。

## 创建验证器

以管理用户身份在任意可读工作目录中运行独立命令：

```bash
threadcells operator create-verifier --output /etc/threadcells/operator-verifier.json
```

该命令在不回显密钥的情况下提示输入，并且仅写入加盐 KDF 验证器。应保护包含目录不被 ThreadCells 服务账户修改，同时允许该账户读取文件。一种合适的布局是：

```bash
sudo chown root:threadcells /etc/threadcells
sudo chmod 0750 /etc/threadcells
sudo chown root:threadcells /etc/threadcells/operator-verifier.json
sudo chmod 0640 /etc/threadcells/operator-verifier.json
```

请将组名调整为你的安装所用服务账户。路径中的每个父目录也必须可信：ThreadCells 会拒绝经由服务拥有或组/全局可写目录访问的验证器。

不要将密钥或验证器 JSON 放入仓库、数据库、日志、浏览器存储、遥测，或解锁操作之外的 API 请求中。

## 配置服务器

在服务器环境中设置绝对验证器引用：

```bash
THREADCELLS_OPERATOR_VERIFIER_FILE=/etc/threadcells/operator-verifier.json
```

仅重启 ThreadCells 服务器，然后检查 Settings → General → Operator authorization。状态应为 **Configured · Locked**，而不是 **Not configured** 或 **Configuration invalid**。

会话端点仅报告安全状态：

```bash
curl -s http://127.0.0.1:9889/operator/session
```

预期结果会在解锁前包含 `"configured": true` 和 `"authenticated": false`。它绝不会返回验证器路径、salt、hash 或密钥。

## 解锁受保护的变更

在 Settings 中输入密钥并选择 **Unlock operator changes**。默认认证窗口为五分钟。UI 显示到期时间，并在会话结束时恢复锁定。

受保护的 Settings 调用在锁定时失败，在已认证会话期间成功。浏览器使用服务器的短时有效安全会话 cookie；它不会持久化操作员密钥。

## 替换密钥

在临时管理路径创建新验证器，验证其所有权和权限，然后原子替换已配置文件并重启 ThreadCells。替换后应将现有操作员会话视为无效。

当前 Web UI 有意不提供未认证的远程重置或基于 Settings 的验证器写入器。CLI 配置使验证器处于操作系统所有权之下，避免创建更广泛的安全子系统。

## 所有者 XHigh 启动

内置 `critical_sol_xhigh_owner` 配置文件可通过 **Create Session & Spawn Agent**、现有会话的 **Add Agent** 和本地 CLI 使用。两个 Web 流程都显示相同的例外权限警告，需要明确确认和已解锁的操作员会话，铸造受修订/范围约束的短时有效一次性能力，并通过正常启动路径消耗它。Add Agent 将该能力绑定到现有会话及其规范解析的工作目录；操作员不能输入任意替代路径。

本地 CLI 路径需要 `--owner-xhigh` 和明确的交互式确认。它会通过回环地址铸造并消耗相同类别的一次性能力。不存在可复用的绕过/header 快捷方式：缺少复选框/确认、缺少或错误的操作员密钥、范围不匹配或重复使用的授权都会闭合失败。已认证 Web 客户端仅接收一次不透明能力，用于执行匹配的启动；操作员密钥绝不会被返回。两者都不会被复制到代理/会话元数据、提供商提示、终端记录、日志或浏览器存储中。这些启动路径不会授权子项，也不会削弱受保护的 Settings 变更。

## 故障排除

- **Not configured：**环境变量不存在或为空。确认它传递给实际服务器进程，然后重启。
- **Configuration invalid：**检查服务器日志中的安全验证原因。检查 JSON schema、绝对路径、可读性、所有者、模式和每个父目录。不要为了掩盖路径或所有权问题而重新创建有效验证器。
- **正确密钥被拒绝：**确认生成器和服务器使用同一验证器文件，且没有旧服务器进程仍在运行。
- **解锁成功后立即锁定：**确认浏览器接受 cookie，且系统时钟正确。
- **本地解锁有效但经 HTTPS 代理的受保护变更失败：**在 ThreadCells 服务环境中将 `THREADCELLS_TRUSTED_PROXY_ORIGINS` 设置为精确的公开 HTTPS 来源（例如 `https://threadcells.example.com`），然后重启。不要添加路径、通配符或未认证来源。
- **在无关目录中创建验证器失败：**请使用当前 ThreadCells 构建。独立命令不得检查工作目录中的 `.env`。

请参阅[安全模型](SECURITY_MODEL.md)了解周边信任假设。
