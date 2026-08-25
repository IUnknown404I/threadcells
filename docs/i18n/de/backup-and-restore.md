---
slug: backup-and-restore
source: docs/BACKUP_AND_RESTORE.md
source_sha256: sha256:3e62f0b30f78fa32bfab783c5fa15e89b9646e2c6de211b8c8ddec3b05b53da1
---
# Backup und Wiederherstellung

Ein nützliches ThreadCells-Backup bewahrt den dauerhaften Koordinationszustand und die Konfiguration, die zu seiner Interpretation nötig ist. Installierter Code und Build-Caches sind gewöhnlich neu erstellbar; Datenbank, operator-eigene Konfiguration und provider-native Nachweise möglicherweise nicht.

## Was wichtig ist

Sichern Sie, soweit zutreffend:

- die ThreadCells-SQLite-Datenbank und ihre zugehörigen SQLite-Dateien;
- Konfiguration und Service-Umgebung, ausgenommen Klartext-Geheimnisse in Ad-hoc-Archiven;
- die Operator-Verifier-Datei als separat geschütztes geheimnisnahes Artefakt;
- die Telegram-Bot-Token-Datei, falls konfiguriert, als separat verschlüsselte Zugangsdaten mit erhaltener Inhaberschaft und Modus;
- Agent-Kontext, Anhänge und Logs, die Ihre Aufbewahrungsrichtlinie erfordert;
- Metadaten verwalteter Worktrees und Releases, die zur Interpretation aktiver Arbeit nötig sind;
- die exakten Manifest-/Identitätsdaten des aktiven und des Rollback-Kandidaten;
- externen Provider-Zustand nur nach dessen eigener unterstützter Backup-Richtlinie.

Git-Repositories sollten bereits über ihre eigene Backup-/Remote-Strategie verfügen. Ein ThreadCells-Datenbankbackup ersetzt nicht die Sicherung von Commits.

## Was neu erstellt werden kann

Heruntergeladene Web-Abhängigkeiten, Browser-Revisionen, Package-Caches, temporäre Build-Verzeichnisse und geprüfte Kandidateninhalte lassen sich normalerweise aus Source und Lockfiles neu erzeugen. Vergrößern Sie nicht jedes Backup um Caches, nur weil sie unter Laufzeitpfaden existieren.

## Konsistente Backup-Sequenz

1. Aktive Source-/Kandidatenidentität und aktuellen Servicezustand aufzeichnen.
2. Während des Snapshot-Fensters keine neuen Sitzungen oder Mutationen starten.
3. Den kanonischen Datenbank-Backup-Mechanismus verwenden, statt eine laufende SQLite-Datei blind zu kopieren.
4. SQLite-Integrität auf dem Backup prüfen.
5. Erforderliche Konfiguration, Verifier und konfigurierte Telegram-Token-Artefakte mit erhaltenen Berechtigungen kopieren, ohne Inhalte auszugeben.
6. Prüfsummen aufzeichnen und das Archiv außerhalb des Live-State-Roots speichern.
7. Testen, ob der vorgesehene Recovery-Prinzipal das Backup auflisten und lesen kann.

Stellt das Deployment-Tooling einen Backup-Befehl bereit, verwenden Sie ihn: Er kennt den tatsächlichen Datenbankpfad und die Service-Koordination. Schreiben Sie niemals Klartext-Provider- oder Operator-Geheimnisse in die Shell-Historie, um ein Archiv zu erstellen.

## Prüfung

Prüfen Sie mindestens die kopierte SQLite-Datenbank:

```bash
sqlite3 /path/to/backup.db 'PRAGMA integrity_check;'
```

Erwartetes Ergebnis: `ok`. Zeichnen Sie außerdem eine Prüfsumme auf und bestätigen Sie, dass das Archiv die erwartete Konfiguration, den Verifier und die Build-Identität enthält, ohne Inhalte in Logs offenzulegen.

Ein ungetestetes Backup ist nur eine Hypothese. Üben Sie die Wiederherstellung regelmäßig in einen isolierten Pfad und auf einen lokalen Port.

## Wiederherstellungsreihenfolge

1. Ziel-ThreadCells-Dienst stoppen oder isolieren.
2. Aktuellen fehlgeschlagenen Zustand für forensisches Rollback bewahren.
3. Den exakten kompatiblen Kandidaten installieren oder auswählen.
4. Datenbank und veränderlichen Zustand mit der erwarteten Inhaberschaft des Laufzeitkontos wiederherstellen.
5. Service-Konfiguration wiederherstellen.
6. Operator-Verifier mit einem eigenen vertrauenswürdigen Owner, für den Dienst lesbarem Modus und vertrauenswürdiger Elternverzeichniskette wiederherstellen; ein zutreffendes Telegram-Token als reguläre, zur Laufzeit gehörende Datei mit Modus `0600` nach `$CAO_HOME_DIR/secrets/telegram-bot-token` wiederherstellen.
7. Integritätsprüfungen vor dem Start ausführen.
8. Auf Loopback starten und Health/Build-Identität prüfen.
9. Aktive Workflows, Ergebnisse, Terminals, Projekte, Provider-Preflight und Statistics prüfen, bevor Arbeit wiederholt wird.

Stellen Sie nicht nur die Datenbank wieder her, während Code oder Service-Umgebung nicht übereinstimmen oder veraltet sind. Nehmen Sie nicht an, dass tmux-/Provider-Prozesse konsistent überlebt haben; gleichen Sie jeden Live-Prozess mit dem dauerhaften Sitzungszustand ab.

## Recovery-Validierung

Prüfen Sie nach der Wiederherstellung:

- Settings → About stimmt mit dem vorgesehenen Kandidaten überein;
- `/health` ist erfolgreich;
- Projekte und Sitzungshistorie sind vorhanden;
- zugestellte Ergebnisse bleiben zuordenbar;
- Provider-Verfügbarkeit entspricht der tatsächlichen Installation des wiederhergestellten Laufzeitbenutzers;
- Operator-Autorisierung meldet konfiguriert und entsperrt mit dem bestehenden Geheimnis;
- Telegram meldet den erwarteten sicheren Konfigurationszustand und besteht, falls wiederhergestellt, explizite Verbindungs- und Testnachrichtenprüfungen vor der Aktivierung;
- Statistics-Summen werden ohne Duplizierung erneut abgespielt;
- aktive/Rollback-Releases bleiben korrekt identifiziert.

Backups sind vor automatischem Housekeeping geschützt. Wenden Sie eine separate, geprüfte Aufbewahrungsrichtlinie auf Backup-Speicher an.
