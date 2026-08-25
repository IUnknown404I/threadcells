---
slug: remote-access
source: docs/REMOTE_ACCESS.md
source_sha256: sha256:6d63e2f9473ae0f156d8e8a207c8bedfb00dcc4581f44727aae2ae48b1819d10
---

# Acesso remoto

O ThreadCells prioriza loopback: o servidor deve escutar em `127.0.0.1`, não em uma interface pública. A Web UI comum é um console do operador e não fornece um limite geral de login.

> Não exponha diretamente a porta bruta do ThreadCells à Internet pública.

Escolha um túnel SSH para acesso ocasional. Use um proxy reverso HTTPS autenticado quando precisar de uma URL permanente e o proprietário do host tiver aprovado explicitamente esse limite de autenticação/proxy.

## Opção A: túnel SSH

No seu notebook, conecte-se ao host do ThreadCells e encaminhe uma porta local:

```bash
ssh -L 9889:127.0.0.1:9889 user@server
```

Mantenha essa sessão SSH aberta e acesse:

```text
http://127.0.0.1:9889
```

O navegador se conecta à porta 9889 do seu notebook. O SSH criptografa o tráfego e o envia para `127.0.0.1:9889` no servidor. O ThreadCells continua escutando somente na interface de loopback do servidor.

Se a porta local 9889 estiver ocupada, use outra porta local:

```bash
ssh -L 19889:127.0.0.1:9889 user@server
```

Depois, abra `http://127.0.0.1:19889`. O túnel termina quando o SSH desconecta; reconecte com o mesmo comando. O OpenSSH fornece a mesma sintaxe `-L` nas instalações atuais de Linux, macOS e Windows.

## Opção B: Caddy e Authelia

Para uma URL permanente conveniente, coloque autenticação e HTTPS na frente do ThreadCells:

```text
Browser
   ↓ HTTPS
Caddy reverse proxy
   ↓ forward-auth
Authelia login and second factor
   ↓ approved request
ThreadCells at 127.0.0.1:9889
```

O Caddy termina TLS e faz proxy do tráfego HTTP/WebSocket. O Authelia fornece o limite de autenticação de usuário. O ThreadCells permanece como upstream apenas local; essa configuração não inventa um segundo sistema de autorização do ThreadCells.

### Pré-requisitos

- Registros DNS para `threadcells.example.com` e `auth.example.com` apontando para o host;
- portas TCP de entrada 80 e 443 disponíveis para o Caddy;
- ThreadCells íntegro em `127.0.0.1:9889`;
- Caddy e Authelia instalados conforme suas instruções oficiais;
- armazenamento do Authelia, segredos de sessão, notificador e pelo menos um usuário configurados com segurança.
- `THREADCELLS_TRUSTED_PROXY_ORIGINS=https://threadcells.example.com` definido no ambiente do serviço ThreadCells existente.

Use o [guia oficial de instalação do Caddy](https://caddyserver.com/docs/install) e o [guia oficial de introdução do Authelia](https://www.authelia.com/integration/prologue/get-started/). O Authelia documenta implantações tanto em [bare metal](https://www.authelia.com/integration/deployment/bare-metal/) quanto [em contêineres](https://www.authelia.com/integration/deployment/docker/).

### Conecte o Caddy ao Authelia

Siga o [guia atual de integração com Caddy do Authelia](https://www.authelia.com/integration/proxies/caddy/). Uma forma compacta de Caddyfile é:

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

Trate isto como a conexão entre os serviços, não como uma configuração completa do Authelia. No Authelia, configure as URLs públicas, o domínio de cookies, a política de controle de acesso, usuários, notificador, armazenamento e um segundo fator usando seus guias oficiais. Guarde os segredos gerados fora do repositório. Reinicie o ThreadCells depois de adicionar ou alterar `THREADCELLS_TRUSTED_PROXY_ORIGINS`; o valor é uma allowlist exata, separada por vírgulas, de origens HTTPS sem caminho. Ele permite que mutações do operador autenticadas por cookie aceitem a origem pública do navegador sem confiar em cabeçalhos arbitrários de proxy.

O [`forward_auth`](https://caddyserver.com/docs/caddyfile/directives/forward_auth) do Caddy verifica cada solicitação antes de ela chegar ao ThreadCells. A substituição de `Host` no upstream preserva o limite Trusted Host somente por loopback do ThreadCells enquanto o Caddy controla o hostname externo e o limite de autenticação. O [`reverse_proxy`](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy) do Caddy oferece suporte a atualizações WebSocket, usadas pelo terminal ao vivo.

### Inicie e valide

Valide a configuração antes de recarregar os serviços:

```bash
caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
sudo systemctl status caddy authelia --no-pager
```

Em seguida, verifique todos os itens abaixo:

- `https://auth.example.com` apresenta a página esperada do Authelia;
- acessar `https://threadcells.example.com` sem login é negado ou redirecionado;
- fazer login e concluir o segundo fator configurado abre o ThreadCells;
- um terminal de agente transmite saída e reconecta após atualizar o navegador;
- `curl http://127.0.0.1:9889/health` continua funcionando no host;
- a porta 9889 não é acessível publicamente.

### Problemas comuns

- **Loop de redirecionamento:** a URL pública do Authelia, o domínio de cookies ou o host de controle de acesso não corresponde ao DNS. Compare-os exatamente.
- **502 Bad Gateway:** o Caddy não consegue alcançar o listener local do ThreadCells ou do Authelia. Verifique ambos os serviços e suas portas de loopback.
- **O login funciona, mas o terminal não transmite:** confirme que a solicitação chega ao `reverse_proxy` do Caddy sem que outro proxy elimine cabeçalhos de atualização WebSocket.
- **A emissão do certificado falha:** verifique o DNS público e as portas de entrada 80/443. A [documentação de HTTPS automático](https://caddyserver.com/docs/automatic-https) do Caddy explica os requisitos.

Mantenha o encaminhamento SSH disponível como caminho de emergência. Ele continua útil quando o DNS, TLS ou a camada de autenticação externa está sendo reparada.
