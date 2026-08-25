---
slug: remote-access
source: docs/REMOTE_ACCESS.md
source_sha256: sha256:6d63e2f9473ae0f156d8e8a207c8bedfb00dcc4581f44727aae2ae48b1819d10
---
# 远程访问

ThreadCells 采用 loopback-first：服务器应监听 `127.0.0.1`，而不是公共接口。普通 Web UI 是操作员控制台，并不提供通用登录边界。

> 不要将原始 ThreadCells 端口直接暴露到公共 Internet。

偶尔访问时请选择 SSH 隧道。当你需要永久 URL，且主机所有者已明确批准该认证/代理边界时，请使用经过身份验证的 HTTPS 反向代理。

## 选项 A：SSH 隧道

在你的笔记本电脑上连接 ThreadCells 主机并转发一个本地端口：

```bash
ssh -L 9889:127.0.0.1:9889 user@server
```

保持该 SSH 会话打开，然后访问：

```text
http://127.0.0.1:9889
```

浏览器连接的是笔记本电脑上的端口 9889。SSH 加密流量并将其发送至服务器上的 `127.0.0.1:9889`。ThreadCells 仍仅监听服务器的 loopback 接口。

如果本地端口 9889 被占用，请使用另一个本地端口：

```bash
ssh -L 19889:127.0.0.1:9889 user@server
```

然后打开 `http://127.0.0.1:19889`。SSH 断开连接时隧道即结束；用同一命令重新连接。当前 Linux、macOS 和 Windows 安装中的 OpenSSH 均提供相同的 `-L` 语法。

## 选项 B：Caddy 和 Authelia

若要获得方便的永久 URL，请在 ThreadCells 前部署认证和 HTTPS：

```text
Browser
   ↓ HTTPS
Caddy reverse proxy
   ↓ forward-auth
Authelia login and second factor
   ↓ approved request
ThreadCells at 127.0.0.1:9889
```

Caddy 终止 TLS 并代理 HTTP/WebSocket 流量。Authelia 提供用户认证边界。ThreadCells 仍是仅本地的上游；此设置不会凭空创建第二套 ThreadCells 授权系统。

### 前置条件

- 指向主机的 `threadcells.example.com` 和 `auth.example.com` DNS 记录；
- Caddy 可使用的入站 TCP 端口 80 和 443；
- ThreadCells 在 `127.0.0.1:9889` 处健康运行；
- 按其官方说明安装的 Caddy 和 Authelia；
- 安全配置的 Authelia 存储、会话秘密、通知器和至少一个用户。
- 在现有 ThreadCells 服务环境中设置 `THREADCELLS_TRUSTED_PROXY_ORIGINS=https://threadcells.example.com`。

请使用[官方 Caddy 安装指南](https://caddyserver.com/docs/install)和[官方 Authelia 入门指南](https://www.authelia.com/integration/prologue/get-started/)。Authelia 同时提供[裸机](https://www.authelia.com/integration/deployment/bare-metal/)和[容器](https://www.authelia.com/integration/deployment/docker/)部署文档。

### 将 Caddy 连接到 Authelia

请遵循 Authelia 最新的 [Caddy 集成指南](https://www.authelia.com/integration/proxies/caddy/)。简洁的 Caddyfile 形态如下：

```caddyfile
auth.example.com {
    reverse_proxy 127.0.0.1:9091
}

threadcells.example.com {
    forward_auth 127.0.0.1:9091 {
        uri /api/authz/forward-auth
        copy_headers Remote-User Remote-Groups Remote-Email Remote-Name
    }
    reverse_proxy 127.0.0.1:9889 {
        header_up Host 127.0.0.1:9889
    }
}
```

将此视为服务之间的连接，而非完整的 Authelia 配置。在 Authelia 中，请按其官方指南配置公共 URL、cookie 域、访问控制策略、用户、通知器、存储和第二因素。将生成的秘密存储在仓库外部。添加或更改 `THREADCELLS_TRUSTED_PROXY_ORIGINS` 后重启 ThreadCells；该值是 HTTPS origins 的精确逗号分隔 allowlist，不含路径。它允许经 cookie 认证的操作员变更接受公共浏览器 origin，而不信任任意代理 header。

Caddy 的 [`forward_auth`](https://caddyserver.com/docs/caddyfile/directives/forward_auth) 会在每个请求到达 ThreadCells 前检查它。上游 `Host` 覆盖保留了 ThreadCells 仅 loopback 的 Trusted Host 边界，而 Caddy 拥有外部主机名和认证边界。Caddy 的 [`reverse_proxy`](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy) 支持 WebSocket 升级，这是实时终端所使用的功能。

### 启动并验证

重新加载服务前先验证配置：

```bash
caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
sudo systemctl status caddy authelia --no-pager
```

然后验证下列全部事项：

- `https://auth.example.com` 显示预期的 Authelia 页面；
- 在未登录时访问 `https://threadcells.example.com` 会被拒绝或重定向；
- 登录并完成配置的第二因素后会打开 ThreadCells；
- 智能体终端流式输出，并在浏览器刷新后重新连接；
- 在主机上 `curl http://127.0.0.1:9889/health` 仍然有效；
- 端口 9889 无法从公共网络访问。

### 常见问题

- **重定向循环：** Authelia 公共 URL、cookie 域或访问控制主机与 DNS 不匹配。逐项精确比对。
- **502 Bad Gateway：** Caddy 无法到达本地 ThreadCells 或 Authelia 监听器。检查两个服务及其 loopback 端口。
- **登录成功但终端不流式输出：** 确认请求到达 Caddy 的 `reverse_proxy`，且没有其他代理剥离 WebSocket 升级 header。
- **证书签发失败：** 检查公共 DNS 和入站端口 80/443。Caddy 的[自动 HTTPS 文档](https://caddyserver.com/docs/automatic-https)说明了要求。

请保留 SSH 转发作为应急路径。当 DNS、TLS 或外部认证层正在修复时，它仍很有用。
