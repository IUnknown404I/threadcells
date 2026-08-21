# Remote access

ThreadCells is loopback-first: the server should listen on `127.0.0.1`, not on a public interface. The ordinary Web UI is an operator console and does not provide a general login boundary.

> Do not expose the raw ThreadCells port directly to the public Internet.

Choose an SSH tunnel for occasional access. Use an authenticated HTTPS reverse proxy when you need a permanent URL and the host owner has explicitly approved that authentication/proxy boundary.

## Option A: SSH tunnel

From your laptop, connect to the ThreadCells host and forward a local port:

```bash
ssh -L 9889:127.0.0.1:9889 user@server
```

Keep that SSH session open, then visit:

```text
http://127.0.0.1:9889
```

The browser connects to port 9889 on your laptop. SSH encrypts the traffic and sends it to `127.0.0.1:9889` on the server. ThreadCells still listens only on the server's loopback interface.

If local port 9889 is busy, use another local port:

```bash
ssh -L 19889:127.0.0.1:9889 user@server
```

Then open `http://127.0.0.1:19889`. The tunnel ends when SSH disconnects; reconnect with the same command. OpenSSH provides the same `-L` syntax on current Linux, macOS, and Windows installations.

## Option B: Caddy and Authelia

For a convenient permanent URL, put authentication and HTTPS in front of ThreadCells:

```text
Browser
   ↓ HTTPS
Caddy reverse proxy
   ↓ forward-auth
Authelia login and second factor
   ↓ approved request
ThreadCells at 127.0.0.1:9889
```

Caddy terminates TLS and proxies HTTP/WebSocket traffic. Authelia supplies the user authentication boundary. ThreadCells remains a local-only upstream; this setup does not invent a second ThreadCells authorization system.

### Prerequisites

- DNS records for `threadcells.example.com` and `auth.example.com` pointing to the host;
- inbound TCP ports 80 and 443 available to Caddy;
- ThreadCells healthy at `127.0.0.1:9889`;
- Caddy and Authelia installed from their official instructions;
- Authelia storage, session secrets, notifier, and at least one user configured securely.
- `THREADCELLS_TRUSTED_PROXY_ORIGINS=https://threadcells.example.com` set in the existing ThreadCells service environment.

Use the [official Caddy installation guide](https://caddyserver.com/docs/install) and the [official Authelia getting-started guide](https://www.authelia.com/integration/prologue/get-started/). Authelia documents both [bare-metal](https://www.authelia.com/integration/deployment/bare-metal/) and [container](https://www.authelia.com/integration/deployment/docker/) deployments.

### Connect Caddy to Authelia

Follow Authelia's current [Caddy integration guide](https://www.authelia.com/integration/proxies/caddy/). A compact Caddyfile shape is:

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

Treat this as the connection between the services, not a complete Authelia configuration. In Authelia, configure the public URLs, cookie domain, access-control policy, users, notifier, storage, and a second factor using its official guides. Store generated secrets outside the repository. Restart ThreadCells after adding or changing `THREADCELLS_TRUSTED_PROXY_ORIGINS`; the value is an exact comma-separated allowlist of HTTPS origins, with no path. It lets cookie-authenticated operator mutations accept the public browser origin without trusting arbitrary proxy headers.

Caddy's [`forward_auth`](https://caddyserver.com/docs/caddyfile/directives/forward_auth) checks each request before it reaches ThreadCells. The upstream `Host` override preserves ThreadCells's loopback-only Trusted Host boundary while Caddy owns the external hostname and authentication boundary. Caddy's [`reverse_proxy`](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy) supports WebSocket upgrades, which the live terminal uses.

### Start and validate

Validate configuration before reloading services:

```bash
caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
sudo systemctl status caddy authelia --no-pager
```

Then verify all of the following:

- `https://auth.example.com` presents the expected Authelia page;
- visiting `https://threadcells.example.com` while signed out is denied or redirected;
- signing in and completing the configured second factor opens ThreadCells;
- an agent terminal streams output and reconnects after a browser refresh;
- `curl http://127.0.0.1:9889/health` still works on the host;
- port 9889 is not publicly reachable.

### Common problems

- **Redirect loop:** the Authelia public URL, cookie domain, or access-control host does not match DNS. Compare them exactly.
- **502 Bad Gateway:** Caddy cannot reach the local ThreadCells or Authelia listener. Check both services and their loopback ports.
- **Login works but the terminal does not stream:** confirm the request reaches Caddy's `reverse_proxy` without another proxy stripping WebSocket upgrade headers.
- **Certificate issuance fails:** check public DNS and inbound ports 80/443. Caddy's [automatic HTTPS documentation](https://caddyserver.com/docs/automatic-https) explains the requirements.

Keep SSH forwarding available as an emergency path. It remains useful when DNS, TLS, or the external authentication layer is being repaired.
