---
slug: getting-started
source: QUICK_SETUP.md
source_sha256: sha256:321ac8cca8705ac1a90bce08efb278d8296e053fe2377b6f21412fb3b99efc90
---

# ThreadCells-Schnellstart

Dies ist der schnellste unterstützte Weg von einem Source-Checkout zu einem lokalen ThreadCells-Server. Er baut einen unveränderlichen lokalen Kandidaten, verifiziert dessen Inhalt, installiert ihn unter dem aktuellen Repository und lauscht nur auf Loopback.

Für Voraussetzungen, Fehlererklärungen und Dienstinstallation nutze den vollständigen [Installationsleitfaden](docs/INSTALLATION.md).

## 1. Host prüfen

ThreadCells zielt derzeit auf Ubuntu/Debian Linux mit Python 3, Git, tmux, Node.js/npm für den Web-Build und mindestens einer unterstützten Provider-CLI. Codex ist der primär getestete Provider.

Im Repository-Stamm:

```bash
python3 --version
git --version
tmux -V
node --version
npm --version
```

## 2. Kandidaten bauen und verifizieren

```bash
python3 scripts/build_local_candidate.py --output "$PWD/threadcells-candidate"
candidate="$PWD/threadcells-candidate/threadcells-0.3.0a2-local"
python3 scripts/verify_local_candidate.py --candidate "$candidate"
```

Erwartetes Ergebnis: Die Verifizierung gelingt für Manifest, Dateien, Prüfsummen und paketierte Web UI des Kandidaten. Ein Kandidat ist ein eigenständiges Release-förmiges Verzeichnis; seine Unveränderlichkeit macht den laufenden Build identifizierbar und Rollback praktikabel.

## 3. Vorschau, dann installieren

```bash
"$candidate/scripts/install-threadcells.sh" --source "$candidate" --dry-run
"$candidate/scripts/install-threadcells.sh" --source "$candidate" --prefix "$PWD/.threadcells"
```

Erwartetes Ergebnis: Der Probelauf erläutert seine Ziele, ohne sie zu verändern; danach erstellt die Installation `.threadcells` mit einer Python-Umgebung und ThreadCells-Befehlen.

## 4. Diagnose ausführen

```bash
"$PWD/.threadcells/venv/bin/threadcells" doctor
```

Behebe fehlgeschlagene erforderliche Prüfungen, bevor du Agenten startest. Ein optionaler Provider kann fehlen; er erscheint in der UI als **CLI not installed**.

## 5. Server starten

```bash
"$PWD/.threadcells/venv/bin/threadcells-server" --host 127.0.0.1 --port 9889
```

Öffne `http://127.0.0.1:9889`.

Erwartetes Ergebnis: Home lädt, Settings → About zeigt die laufende Build-Identität und diese Dokumentation ist unter Docs verfügbar.

Halte Host und Port für diesen ersten Lauf exakt nur auf Loopback. Ändere für einen anderen Rechner den Listener nicht auf `0.0.0.0`; nutze [Remotezugriff](docs/REMOTE_ACCESS.md).

Das Betriebsmodell ist bewusst kurz: Erstelle eine Sitzung, wähle einen Agenten oder Supervisor, gib ihm die Aufgabe, beobachte den Workflow und greife nur bei einer expliziten Eigentümerentscheidung oder abschließenden Prüfung ein. Der Abschluss eines Providers allein schließt keinen offenen Workflow.

## 6. Nützliche Arbeit beginnen

Befolge [Dein erstes Projekt und dein erster Agent](docs/FIRST_AGENT.md). Das enthaltene [sichere Starterbeispiel](examples/threadcells-starter/README.md) ist ebenfalls eine begrenzte Supervisor-/Entwickler-/Reviewer-Übung, die weder veröffentlicht noch Dienste ändert.

## Anhalten und fortsetzen

Beende den Vordergrundserver mit `Ctrl-C`. Agenten-Terminals sind tmux-gestützt und können eine Browserverbindung überleben, aber gehe nicht davon aus, dass ein unterbrochener Server ihre Workflows abgeschlossen hat. Starte denselben installierten `threadcells-server` neu, öffne Agents und prüfe ihren aktuellen Zustand und die dauerhaften Ergebnisse.

## Weiterführende Lektüre

- [Kernkonzepte](docs/CONCEPTS.md)
- [Provider](docs/PROVIDERS.md) und [Profile](docs/PROFILES.md)
- [Kapazitäts- und Ressourcenmodell](docs/RESOURCE_MODEL.md)
- [Housekeeping](docs/HOUSEKEEPING.md)
- [Telegram-Benachrichtigungen](docs/TELEGRAM_NOTIFICATIONS.md)
- [Backup und Wiederherstellung](docs/BACKUP_AND_RESTORE.md)
- [Sicherheitsmodell](docs/SECURITY_MODEL.md)
