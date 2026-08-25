---
slug: remote-access
source: docs/REMOTE_ACCESS.md
source_sha256: sha256:6d63e2f9473ae0f156d8e8a207c8bedfb00dcc4581f44727aae2ae48b1819d10
---
# Удалённый доступ

ThreadCells ориентирован прежде всего на loopback: сервер должен слушать `127.0.0.1`, а не публичный интерфейс. Обычный Web UI — это консоль оператора и не предоставляет общую границу входа.

> Не открывайте необработанный порт ThreadCells напрямую в публичный Интернет.

Для редкого доступа выберите SSH-туннель. Если нужен постоянный URL и владелец хоста явно одобрил такую границу аутентификации/proxy, используйте аутентифицированный HTTPS reverse proxy.

## Вариант A: SSH-туннель

На ноутбуке подключитесь к хосту ThreadCells и перенаправьте локальный порт:

```bash
ssh -L 9889:127.0.0.1:9889 user@server
```

Не закрывайте эту SSH-сессию, затем откройте:

```text
http://127.0.0.1:9889
```

Браузер подключается к порту 9889 на ноутбуке. SSH шифрует трафик и направляет его на `127.0.0.1:9889` сервера. ThreadCells по-прежнему слушает только loopback-интерфейс сервера.

Если локальный порт 9889 занят, используйте другой локальный порт:

```bash
ssh -L 19889:127.0.0.1:9889 user@server
```

Затем откройте `http://127.0.0.1:19889`. Туннель завершается при отключении SSH; подключитесь снова той же командой. OpenSSH использует тот же синтаксис `-L` в актуальных установках Linux, macOS и Windows.

## Вариант B: Caddy и Authelia

Для удобного постоянного URL поместите аутентификацию и HTTPS перед ThreadCells:

```text
Browser
   ↓ HTTPS
Caddy reverse proxy
   ↓ forward-auth
Authelia login and second factor
   ↓ approved request
ThreadCells at 127.0.0.1:9889
```

Caddy завершает TLS и проксирует HTTP/WebSocket-трафик. Authelia предоставляет границу аутентификации пользователя. ThreadCells остаётся локальным upstream; эта настройка не создаёт вторую систему авторизации ThreadCells.

### Предварительные требования

- DNS-записи для `threadcells.example.com` и `auth.example.com`, указывающие на хост;
- входящие TCP-порты 80 и 443, доступные Caddy;
- работоспособный ThreadCells на `127.0.0.1:9889`;
- Caddy и Authelia, установленные по их официальным инструкциям;
- безопасно настроенные хранилище Authelia, session secrets, notifier и как минимум один пользователь.
- `THREADCELLS_TRUSTED_PROXY_ORIGINS=https://threadcells.example.com`, установленная в существующем service environment ThreadCells.

Используйте [официальное руководство по установке Caddy](https://caddyserver.com/docs/install) и [официальное руководство по началу работы с Authelia](https://www.authelia.com/integration/prologue/get-started/). Authelia документирует развёртывания [bare-metal](https://www.authelia.com/integration/deployment/bare-metal/) и [в контейнере](https://www.authelia.com/integration/deployment/docker/).

### Подключите Caddy к Authelia

Следуйте актуальному [руководству по интеграции Caddy](https://www.authelia.com/integration/proxies/caddy/) от Authelia. Компактная форма Caddyfile:

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

Считайте это связью между сервисами, а не полной конфигурацией Authelia. В Authelia настройте публичные URL, cookie domain, access-control policy, пользователей, notifier, storage и второй фактор по официальным руководствам. Храните сгенерированные secrets вне репозитория. Перезапустите ThreadCells после добавления или изменения `THREADCELLS_TRUSTED_PROXY_ORIGINS`; значение представляет собой точный разделённый запятыми allowlist HTTPS origins без path. Оно позволяет мутациям оператора с аутентификацией cookie принимать public browser origin, не доверяя произвольным proxy headers.

[`forward_auth`](https://caddyserver.com/docs/caddyfile/directives/forward_auth) Caddy проверяет каждый запрос, прежде чем тот достигнет ThreadCells. Переопределение upstream `Host` сохраняет границу Trusted Host ThreadCells, доступную только через loopback, тогда как Caddy владеет внешним hostname и границей аутентификации. [`reverse_proxy`](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy) Caddy поддерживает WebSocket upgrades, которые использует live terminal.

### Запуск и проверка

Проверьте конфигурацию до перезагрузки сервисов:

```bash
caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
sudo systemctl status caddy authelia --no-pager
```

Затем проверьте всё перечисленное:

- `https://auth.example.com` показывает ожидаемую страницу Authelia;
- переход на `https://threadcells.example.com` без входа отклоняется или перенаправляется;
- вход и прохождение настроенного второго фактора открывают ThreadCells;
- терминал агента передаёт вывод и переподключается после обновления браузера;
- `curl http://127.0.0.1:9889/health` по-прежнему работает на хосте;
- порт 9889 недоступен из публичной сети.

### Частые проблемы

- **Цикл перенаправления:** публичный URL Authelia, cookie domain или access-control host не совпадает с DNS. Сравните их в точности.
- **502 Bad Gateway:** Caddy не может подключиться к локальному listener ThreadCells или Authelia. Проверьте оба сервиса и их loopback-порты.
- **Вход работает, но терминал не передаёт вывод:** убедитесь, что запрос достигает `reverse_proxy` Caddy и другой proxy не удаляет WebSocket upgrade headers.
- **Не удаётся выпустить сертификат:** проверьте публичный DNS и входящие порты 80/443. В [документации Caddy об автоматическом HTTPS](https://caddyserver.com/docs/automatic-https) объясняются требования.

Сохраняйте SSH forwarding как аварийный путь. Он остаётся полезен при восстановлении DNS, TLS или внешнего слоя аутентификации.
