---
slug: remote-access
source: docs/REMOTE_ACCESS.md
source_sha256: sha256:6d63e2f9473ae0f156d8e8a207c8bedfb00dcc4581f44727aae2ae48b1819d10
---

# Remotezugriff

ThreadCells ist loopback-first: Der Server sollte auf `127.0.0.1` lauschen, nicht auf einer öffentlichen Schnittstelle. Die gewöhnliche Web UI ist eine Betreiberkonsole und bietet keine allgemeine Login-Grenze.

> Lege den rohen ThreadCells-Port nicht direkt im öffentlichen Internet offen.

Wähle für gelegentlichen Zugriff einen SSH-Tunnel. Nutze einen authentifizierten HTTPS-Reverse-Proxy, wenn du eine dauerhafte URL brauchst und der Host-Eigentümer diese Authentifizierungs-/Proxy-Grenze ausdrücklich genehmigt hat.

## Option A: SSH-Tunnel

Verbinde dich von deinem Laptop mit dem ThreadCells-Host und leite einen lokalen Port weiter:

```bash
ssh -L 9889:127.0.0.1:9889 user@server
```

Lasse diese SSH-Sitzung geöffnet und rufe dann auf:

```text
http://127.0.0.1:9889
```

Der Browser verbindet sich mit Port 9889 auf deinem Laptop. SSH verschlüsselt den Verkehr und sendet ihn an `127.0.0.1:9889` auf dem Server. ThreadCells lauscht weiterhin nur auf der Loopback-Schnittstelle des Servers.

Wenn der lokale Port 9889 belegt ist, verwende einen anderen lokalen Port:

```bash
ssh -L 19889:127.0.0.1:9889 user@server
```

Öffne dann `http://127.0.0.1:19889`. Der Tunnel endet, wenn SSH die Verbindung trennt; verbinde dich mit demselben Befehl erneut. OpenSSH verwendet dieselbe `-L`-Syntax auf aktuellen Linux-, macOS- und Windows-Installationen.

## Option B: Caddy und Authelia

Für eine bequeme dauerhafte URL setze Authentifizierung und HTTPS vor ThreadCells:

```text
Browser
   ↓ HTTPS
Caddy reverse proxy
   ↓ forward-auth
Authelia login and second factor
   ↓ approved request
ThreadCells at 127.0.0.1:9889
```

Caddy terminiert TLS und proxyt HTTP/WebSocket-Verkehr. Authelia stellt die Benutzer-Authentifizierungsgrenze bereit. ThreadCells bleibt ein rein lokaler Upstream; dieses Setup erfindet kein zweites ThreadCells-Autorisierungssystem.

### Voraussetzungen

- DNS-Einträge für `threadcells.example.com` und `auth.example.com`, die auf den Host zeigen;
- eingehende TCP-Ports 80 und 443, die für Caddy verfügbar sind;
- gesundes ThreadCells unter `127.0.0.1:9889`;
- Caddy und Authelia, gemäß ihren offiziellen Anweisungen installiert;
- sicher konfigurierter Authelia-Speicher, Session-Secrets, Notifier und mindestens ein Benutzer.
- `THREADCELLS_TRUSTED_PROXY_ORIGINS=https://threadcells.example.com`, gesetzt in der bestehenden ThreadCells-Dienstumgebung.

Nutze den [offiziellen Caddy-Installationsleitfaden](https://caddyserver.com/docs/install) und den [offiziellen Authelia-Einstiegsleitfaden](https://www.authelia.com/integration/prologue/get-started/). Authelia dokumentiert sowohl [Bare Metal](https://www.authelia.com/integration/deployment/bare-metal/) als auch [Container](https://www.authelia.com/integration/deployment/docker/)-Bereitstellungen.

### Caddy mit Authelia verbinden

Folge Authelias aktuellem [Caddy-Integrationsleitfaden](https://www.authelia.com/integration/proxies/caddy/). Eine kompakte Caddyfile-Form ist:

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

Behandle dies als Verbindung zwischen den Diensten, nicht als vollständige Authelia-Konfiguration. Konfiguriere in Authelia die öffentlichen URLs, Cookie-Domain, Zugriffskontrollrichtlinie, Benutzer, Notifier, Speicher und zweiten Faktor mithilfe der offiziellen Leitfäden. Speichere erzeugte Secrets außerhalb des Repositorys. Starte ThreadCells nach dem Hinzufügen oder Ändern von `THREADCELLS_TRUSTED_PROXY_ORIGINS` neu; der Wert ist eine exakte durch Kommas getrennte Allowlist von HTTPS-Origins ohne Pfad. Sie erlaubt es Cookie-authentifizierten Betreiber-Mutationen, die öffentliche Browser-Origin zu akzeptieren, ohne beliebigen Proxy-Headern zu vertrauen.

Caddys [`forward_auth`](https://caddyserver.com/docs/caddyfile/directives/forward_auth) prüft jede Anfrage, bevor sie ThreadCells erreicht. Die Upstream-Überschreibung von `Host` bewahrt die nur auf Loopback geltende Trusted-Host-Grenze von ThreadCells, während Caddy den externen Hostnamen und die Authentifizierungsgrenze besitzt. Caddys [`reverse_proxy`](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy) unterstützt WebSocket-Upgrades, die das Live-Terminal verwendet.

### Starten und validieren

Validiere die Konfiguration, bevor du Dienste neu lädst:

```bash
caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
sudo systemctl status caddy authelia --no-pager
```

Prüfe dann alles Folgende:

- `https://auth.example.com` zeigt die erwartete Authelia-Seite;
- der Aufruf von `https://threadcells.example.com` im abgemeldeten Zustand wird abgelehnt oder umgeleitet;
- die Anmeldung und der konfigurierte zweite Faktor öffnen ThreadCells;
- ein Agenten-Terminal streamt Ausgabe und verbindet sich nach Browser-Aktualisierung erneut;
- `curl http://127.0.0.1:9889/health` funktioniert weiterhin auf dem Host;
- Port 9889 ist nicht öffentlich erreichbar.

### Häufige Probleme

- **Weiterleitungsschleife:** Die öffentliche Authelia-URL, Cookie-Domain oder Zugriffskontroll-Host stimmt nicht mit DNS überein. Vergleiche sie exakt.
- **502 Bad Gateway:** Caddy kann den lokalen ThreadCells- oder Authelia-Listener nicht erreichen. Prüfe beide Dienste und ihre Loopback-Ports.
- **Login funktioniert, aber das Terminal streamt nicht:** Bestätige, dass die Anfrage Caddys `reverse_proxy` erreicht, ohne dass ein anderer Proxy WebSocket-Upgrade-Header entfernt.
- **Zertifikatausstellung schlägt fehl:** Prüfe öffentliches DNS und eingehende Ports 80/443. Caddys [Dokumentation zu automatischem HTTPS](https://caddyserver.com/docs/automatic-https) erklärt die Voraussetzungen.

Halte SSH-Weiterleitung als Notfallpfad verfügbar. Sie bleibt nützlich, wenn DNS, TLS oder die externe Authentifizierungsschicht repariert wird.
