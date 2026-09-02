---
slug: upgrading
source: docs/UPGRADING.md
source_sha256: sha256:6b8fc9eedd7c87562991ebed6aa062f97ecd70668491eb0871a81aa57f550786
---
# ThreadCells aktualisieren

Ein Upgrade ist eine kontrollierte Kandidatenpromotion mit geprüftem Rollback, kein In-Place-Überschreiben beliebiger laufender Dateien.

## Vor dem Upgrade

- Lesen Sie die Release Notes und [Limitations](LIMITATIONS.md).
- Bestätigen Sie aktuellen Health-Zustand sowie aktive/Rollback-Build-Identitäten.
- Lassen Sie kritische Provider-/Heavy-Vorgänge eine sichere Grenze erreichen.
- Prüfen Sie offene Workflows und zugestellte Ergebnisse.
- Erstellen Sie ein konsistentes Backup und führen Sie Datenbankintegritätsprüfungen aus.
- Bewahren Sie den aktuellen Kandidaten als Rollback.

## Bauen und prüfen

Aus dem vorgesehenen Source-Commit:

```bash
python3 scripts/build_local_candidate.py --output "$PWD/threadcells-candidate"
candidate="$PWD/threadcells-candidate/threadcells-0.3.4a0-local"
python3 scripts/verify_local_candidate.py --candidate "$candidate"
```

Promoten Sie nicht, wenn die Kandidatenidentität vom geprüften Commit abweicht oder Docs-/Web-/Build-Prüfungen fehlschlagen.

## Stagen und promoten

Verwenden Sie die kanonischen lokalen Deployment-Tools, um den Kandidaten zu stagen, ohne den aktiven Pointer zu ändern. Prüfen Sie die gestagten Dateien und promoten Sie anschließend atomar; starten Sie nur ThreadCells-Dienste neu, die das Release verwenden.

Erwartetes Ergebnis: Settings → About, der Docs-Footer und Release-Metadaten identifizieren dieselbe Kandidatenrevision.

## Prüfungen nach dem Upgrade

1. `curl -fsS http://127.0.0.1:9889/health`
2. Home öffnen und Kapazitäts-/Festplattenstatus prüfen.
3. Bestehende Agents/Flows öffnen und bestätigen, dass dauerhafte Beziehungen erhalten sind.
4. Provider-Bereitschaft in Settings und Spawn vergleichen.
5. Bestätigen, dass Operator-Autorisierung konfiguriert ist und geschützte Mutationen bis zum Entsperren gesperrt bleiben.
6. Statistics öffnen und bestätigen, dass Aktualisierung/Neustart keine Nutzung dupliziert.
7. Docs-Routen öffnen und die paketierte Build-Identität prüfen.
8. Terminal-Streaming/Wiederverbindung prüfen.
9. Bestätigen, dass PWA-Manifest und Service Worker keine dynamischen Requests cachen.
10. Settings → Telegram öffnen und den sicheren Konfigurationszustand bestätigen; falls native Zugangsdaten bereits konfiguriert waren, die expliziten Verbindungs- und Testnachrichtenprüfungen ausführen.
11. Für einen offenen Agent, der die Promotion überdauert, bestätigen, dass eine Neuinitialisierung der Control Connection genau einmal abgeschlossen wird und derselbe dauerhafte Workflow ohne Owner-Wake oder doppeltes Child/Effekt fortgesetzt wird.

## Historische Reparaturen

Ein Upgrade kann eine begrenzte Datenreparatur enthalten. Führen Sie sie nur aus, wenn die Source-Nachweise deterministisch sind, halten Sie sie idempotent und zeichnen Sie Vorher-/Nachher-Anzahlen auf. Fehlende Provider-Telemetrie muss fehlen bleiben; erfinden Sie niemals historische Nutzung.

## Rollback

Wenn die Abnahme wesentlich fehlschlägt:

1. fehlgeschlagenen Kandidaten und relevante sichere Logs bewahren;
2. den kanonischen aktiven Pointer auf den geprüften Rollback-Kandidaten umschalten;
3. nur erforderliche Dienste neu starten;
4. Rollback-Build und Kernoberflächen prüfen;
5. Pre-Upgrade-Datenbank nur wiederherstellen, wenn Schema-/Datenkompatibilität es erfordert.

Verwenden Sie keinen destruktiven Git-Reset und löschen Sie keine neueren Laufzeitnachweise, um ein Rollback zu simulieren.

Ein ausdrücklich bestätigtes Full Cleanup ist die Ausnahme von der normalen Aufbewahrung lokaler Releases: Es entfernt alle nachweislich inaktiven Releases einschließlich des beim Deployment ausgewählten Rollbacks und lässt nur das aktive unveränderliche Release zurück. Führen Sie es nicht während der Upgrade-Abnahme oder während der Ausführung eines Agenten aus. Stellen Sie nach einem erfolgreichen Full Cleanup die Rollback-Verfügbarkeit nur durch Staging eines weiteren verifizierten unveränderlichen Releases wieder her; rekonstruieren Sie es niemals aus einem ungeprüften Verzeichnis.

Siehe [Lokales Deployment](DEPLOYMENT.md) und [Backup und Wiederherstellung](BACKUP_AND_RESTORE.md).
