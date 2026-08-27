---
slug: remote-access
source: docs/REMOTE_ACCESS.md
source_sha256: sha256:6d63e2f9473ae0f156d8e8a207c8bedfb00dcc4581f44727aae2ae48b1819d10
---

# Acceso remoto

ThreadCells prioriza loopback: el servidor debe escuchar en `127.0.0.1`, no en una interfaz pública. La Web UI habitual es una consola de operador y no ofrece un límite general de inicio de sesión.

> No expongas directamente el puerto sin procesar de ThreadCells a Internet pública.

Elige un túnel SSH para acceso ocasional. Usa un proxy inverso HTTPS autenticado cuando necesites una URL permanente y el propietario del host haya aprobado explícitamente ese límite de autenticación/proxy.

## Opción A: túnel SSH

Desde tu portátil, conéctate al host de ThreadCells y reenvía un puerto local:

```bash
ssh -L 9889:127.0.0.1:9889 user@server
```

Mantén abierta esa sesión SSH y visita:

```text
http://127.0.0.1:9889
```

El navegador se conecta al puerto 9889 de tu portátil. SSH cifra el tráfico y lo envía a `127.0.0.1:9889` del servidor. ThreadCells sigue escuchando solo en la interfaz loopback del servidor.

Si el puerto local 9889 está ocupado, usa otro puerto local:

```bash
ssh -L 19889:127.0.0.1:9889 user@server
```

Después abre `http://127.0.0.1:19889`. El túnel termina cuando SSH se desconecta; vuelve a conectarte con el mismo comando. OpenSSH ofrece la misma sintaxis `-L` en instalaciones actuales de Linux, macOS y Windows.

## Opción B: Caddy y Authelia

Para una URL permanente práctica, coloca autenticación y HTTPS delante de ThreadCells:

```text
Browser
   ↓ HTTPS
Caddy reverse proxy
   ↓ forward-auth
Authelia login and second factor
   ↓ approved request
ThreadCells at 127.0.0.1:9889
```

Caddy termina TLS y actúa de proxy para tráfico HTTP/WebSocket. Authelia proporciona el límite de autenticación de usuario. ThreadCells permanece como upstream local; esta configuración no inventa un segundo sistema de autorización de ThreadCells.

### Requisitos previos

- Registros DNS para `threadcells.example.com` y `auth.example.com` que apunten al host;
- puertos TCP entrantes 80 y 443 disponibles para Caddy;
- ThreadCells en buen estado en `127.0.0.1:9889`;
- Caddy y Authelia instalados según sus instrucciones oficiales;
- almacenamiento de Authelia, secretos de sesión, notificador y al menos un usuario configurados de forma segura.
- `THREADCELLS_TRUSTED_PROXY_ORIGINS=https://threadcells.example.com` establecido en el entorno de servicio ThreadCells existente.

Usa la [guía oficial de instalación de Caddy](https://caddyserver.com/docs/install) y la [guía oficial de primeros pasos de Authelia](https://www.authelia.com/integration/prologue/get-started/). Authelia documenta despliegues tanto [bare-metal](https://www.authelia.com/integration/deployment/bare-metal/) como [en contenedores](https://www.authelia.com/integration/deployment/docker/).

### Conecta Caddy con Authelia

Sigue la [guía actual de integración de Caddy de Authelia](https://www.authelia.com/integration/proxies/caddy/). Una forma compacta de Caddyfile es:

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

Trata esto como la conexión entre los servicios, no como una configuración completa de Authelia. En Authelia, configura las URL públicas, el dominio de cookies, la política de control de acceso, usuarios, notificador, almacenamiento y un segundo factor usando sus guías oficiales. Guarda los secretos generados fuera del repositorio. Reinicia ThreadCells después de añadir o cambiar `THREADCELLS_TRUSTED_PROXY_ORIGINS`; el valor es una lista de permitidos exacta de orígenes HTTPS separada por comas, sin ruta. Permite que las mutaciones del operador autenticadas con cookies acepten el origen público del navegador sin confiar en cabeceras arbitrarias de proxy.

[`forward_auth`](https://caddyserver.com/docs/caddyfile/directives/forward_auth) de Caddy comprueba cada solicitud antes de que llegue a ThreadCells. La sustitución upstream de `Host` conserva el límite Trusted Host solo-loopback de ThreadCells mientras Caddy posee el nombre de host externo y el límite de autenticación. [`reverse_proxy`](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy) de Caddy admite actualizaciones WebSocket, que utiliza el terminal en vivo.

### Inicia y valida

Valida la configuración antes de recargar servicios:

```bash
caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
sudo systemctl status caddy authelia --no-pager
```

Después verifica todo lo siguiente:

- `https://auth.example.com` presenta la página esperada de Authelia;
- visitar `https://threadcells.example.com` sin iniciar sesión se deniega o redirige;
- iniciar sesión y completar el segundo factor configurado abre ThreadCells;
- un terminal de agente transmite salida y se reconecta tras actualizar el navegador;
- `curl http://127.0.0.1:9889/health` sigue funcionando en el host;
- el puerto 9889 no es accesible públicamente.

### Problemas comunes

- **Bucle de redirección:** la URL pública de Authelia, el dominio de cookies o el host de control de acceso no coincide con DNS. Compáralos exactamente.
- **502 Bad Gateway:** Caddy no puede alcanzar el listener local de ThreadCells o Authelia. Comprueba ambos servicios y sus puertos loopback.
- **El inicio de sesión funciona pero el terminal no transmite:** confirma que la solicitud llega al `reverse_proxy` de Caddy sin otro proxy que elimine las cabeceras de actualización WebSocket.
- **Falla la emisión del certificado:** comprueba el DNS público y los puertos entrantes 80/443. La [documentación de HTTPS automático](https://caddyserver.com/docs/automatic-https) de Caddy explica los requisitos.

Mantén disponible el reenvío SSH como ruta de emergencia. Sigue siendo útil cuando se reparan DNS, TLS o la capa de autenticación externa.
