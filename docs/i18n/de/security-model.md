---
slug: security-model
source: docs/SECURITY_MODEL.md
source_sha256: sha256:6305e6199bae4706af6ed41e99eb0465ed0877bff4e83a7b1df57019f1a3383c
---
# Sicherheitsmodell

ThreadCells ist für einen vertrauenswürdigen Linux-Host vorgesehen, der von einer Person oder einem kleinen vertrauenswürdigen Team betrieben wird. Es koordiniert leistungsfähige native Coding-Agents; es ist keine Sandbox für feindselige Benutzer, Prompts, Repositories oder Provider-Plugins.

## Praktische Vertrauensgrenzen

### Host und Laufzeitbenutzer

Alles, was das Betriebssystemkonto von ThreadCells lesen oder ausführen kann, kann für einen nativen Agenten erreichbar sein. Verwenden Sie ein dediziertes Konto, minimale Dateisystemberechtigungen, überprüfte Provider-Installationen und für Automatisierung geeignete Repositories.

Verwaltete Worktrees trennen Schreibende, schirmen sie jedoch nicht ab. Geben Sie dem Laufzeitkonto weder Zugangsdaten noch Host-Zugriff, die ein Agent nicht benötigt.

### Webzugriff

Die gewöhnliche UI und die paketierten Docs implementieren keinen allgemeinen Benutzer-Login. Binden Sie an `127.0.0.1`. Verwenden Sie für gelegentlichen Zugriff einen SSH-Tunnel oder Caddy zusammen mit Authelia für eine authentifizierte HTTPS-URL. Legen Sie den ungeschützten ThreadCells-Port niemals öffentlich offen.

Eine installierte PWA behält dieselbe Netzwerkvertrauensgrenze bei. Ihr Service Worker kann keinen Offline-Betriebszustand bereitstellen und speichert keine APIs, Terminals, Autorisierung, Workflows, Ergebnisse oder Statistics im Cache.

### Operatorautorisierung

Sensible Mutationen der Control Plane verwenden einen separaten Operatorverifizierer, der von einem anderen Betriebssystemprinzipal bereitgestellt wird. Die exakte Mindestlänge des Geheimnisses beträgt fünf Zeichen; längere zufällig generierte Werte werden empfohlen.

ThreadCells speichert einen gesalzenen scrypt-Verifizierer, kurzlebige Sitzungs-/Grant-Digests, Geltungsbereich, Aussteller, Ablauf, Verbrauch und Audit-Datensätze – nicht den Klartext. Der Verifizierer und jedes übergeordnete Verzeichnis dürfen vom Dienstkonto nicht ersetzbar sein. Die fünfminütige Browsersitzung verwendet ein `HttpOnly`-, `SameSite=Strict`-Cookie.

Diese Grenze schützt konfigurierte Mutationen. Sie macht nicht die gesamte Web UI zu einer authentifizierten Mehrbenutzeranwendung.

### Außergewöhnlicher Owner-Start

Owner-executor/XHigh-Starts erfordern eine Einmal-Capability, die an die exakte unveränderliche Profilrevision, Provider-Konfigurationsrevision, Projekt/Worktree, Sitzungsanfrage, Topologie, Aussteller und Delegationstiefe gebunden ist. Sie wird zusammen mit Terminalmetadaten atomar verbraucht.

Die Web-Abläufe Create Session und Add Agent für das eingebaute `critical_sol_xhigh_owner` erfordern dieselbe außergewöhnliche Warnung, ausdrückliche Bestätigung, Operator-Entsperrung und eine begrenzte Einmal-Capability. Add Agent bindet den Grant an die bestehende Sitzung und den aufgelösten kanonischen Worktree, statt einen vom Benutzer eingegebenen Pfad zu akzeptieren. Der lokale Pfad `critical_sol_xhigh_owner --owner-xhigh` erfordert eine ausdrückliche interaktive Bestätigung und einen reinen Loopback-Start. Keiner dieser Pfade verleiht Kindern oder nicht zugehörigen Web Settings Autorität. Prompt-Text kann keine Owner-Autorität prägen oder delegieren.

### Provider und importierte Artefakte

Provider-Adapter sind vertrauenswürdige ausführbare Pakete und erfordern eine Operatorprüfung. Provider-/Profil-JSON ist nicht vertrauenswürdige deklarative Eingabe: ausführbare Pfade, Befehle, Shell-Flags, Umgebungen, rohe Geheimnisse, beliebige MCP-Befehle und nicht gewährte Wildcard-Autorität werden abgelehnt.

Die Providerauthentifizierung bleibt beim jeweils unterstützten Mechanismus des Providers. Registry-Exporte lassen Geheimniswerte und Einmal-Grants weg.

Terminalbezogene Control-Credentials sind auf den Terminal-/Provider-Prozess begrenzt. ThreadCells startet den langlebigen tmux-Server über einen credential-freien Bootstrap, sodass seine persistente Prozesskommandozeile keine Terminal-Credentials enthält. Diese Credentials bleiben für Prozesse sensibel, die unter demselben vertrauenswürdigen Laufzeitkonto laufen.

### Telegram-Benachrichtigungen

Die Zustellung an Telegram ist optional, standardmäßig deaktiviert, installationsweit und unabhängig von der Projektkonfiguration. Sein Bot-Token ist eine reine Schreib-Webeinstellung, die außerhalb von SQLite im privaten ThreadCells-Zustandsstamm als reguläre, zur Laufzeit gehörende `0600`-Datei gespeichert wird. Lese-APIs geben nur den sicheren Konfigurationszustand preis. Die Operatorautorisierung schützt Aktualisierungen sowie ausdrückliche Verbindungs-/Testaktionen.

Lebenszyklusmeldungen verwenden feste sichere Zusammenfassungen und lassen Prompts, Terminalausgaben, Exception-Bodies, Pfade und Credentials weg. Die externe Zustellung ist für die Workflow-Ausführung fail-open und dauerhaft dedupliziert; das Aktivieren von Benachrichtigungen spielt keine Ereignisse erneut ab, die im deaktivierten Zustand beobachtet wurden.

## Datensensibilität

Behandeln Sie die SQLite-Datenbank, Terminallogs, Prompts, Ergebnisse, Anhänge, verwaltete Worktrees, Backups, den Operatorverifizierer und den nativen Provider-Rollout-Verlauf als sensibel. Sie können proprietären Code oder benutzerbereitgestellte Inhalte enthalten, auch wenn ThreadCells selbst die Protokollierung von Credentials vermeidet.

Legen Sie keine Klartext-Operator-/Provider-/Telegram-Geheimnisse in Repositories, Umgebungs-Dumps, Support-Bundles, Telemetrie, Browserspeicher, API-Antworten oder Screenshots ab.

## Destruktive Vorgänge

Housekeeping erfolgt planbasiert und schlägt geschlossen fehl. Unbekannte, nicht lesbare, geöffnete, aktive, referenzierte, identitätsveränderte oder mit unvollständigen Metadaten versehene Ressourcen bleiben geschützt. Backups werden niemals automatisch gelöscht.

Das Deployment bewahrt eine Rollback-Laufzeitumgebung und ein Datenbank-Backup. Veröffentlichung, öffentliche Netzwerkfreigabe und destruktive Verlaufsänderungen bleiben separate Owner-Entscheidungen.

## Verantwortlichkeiten des Operators

- Patchen Sie das Betriebssystem, ThreadCells, Provider-CLIs, Reverse Proxy und Authentifizierungsschicht.
- Prüfen Sie Prompts, Profile, Adapter und Repositories, bevor Sie Schreibzugriff gewähren.
- Halten Sie Laufzeitbenutzer und Dienstumgebung minimal privilegiert.
- Sichern Sie den dauerhaften Zustand und testen Sie dessen Wiederherstellung.
- Prüfen Sie Diffs/Ergebnisse vor Merge, Deployment oder Veröffentlichung.
- Bewahren Sie Loopback- oder authentifizierte-Proxy-Zugriffskontrollen.
- Rotieren Sie Provider-Credentials und ersetzen Sie den Operatorverifizierer durch einen sicheren administrativen Prozess.

## Sicherheitsproblem melden

Folgen Sie [SECURITY.md](../SECURITY.md). Nehmen Sie keine aktiven Credentials, keinen privaten Zustand und keine öffentlichen Exploit-Details auf, die über das hinausgehen, was Maintainer für eine sichere Reproduktion benötigen.
