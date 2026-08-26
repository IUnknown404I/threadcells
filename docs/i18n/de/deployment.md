---
slug: deployment
source: docs/DEPLOYMENT.md
source_sha256: sha256:952a25cc4d8f85fc4ea89cf2d7d9cb999316f9335209fc5a1598092827939f37
---
# Lokales Deployment

Ein ThreadCells-Deployment übernimmt einen geprüften unveränderlichen Kandidaten in die lokale Laufzeit. Es impliziert weder Veröffentlichung, Git-Push/Tag, Paket-Release noch öffentliche Netzwerkexposition.

## Kandidatendisziplin

Erstellen Sie aus einem exakten sauberen Source-Commit und prüfen Sie den Kandidaten vor dem Staging:

```bash
python3 scripts/build_local_candidate.py --output "$PWD/threadcells-candidate"
python3 scripts/verify_local_candidate.py \
  --candidate "$PWD/threadcells-candidate/threadcells-0.3.0a2-local"
```

Der Kandidat soll Python-Code, paketierte Web-Assets, das erlaubte Docs-Bundle, Build-Identität, Prüfsummen und Release-Metadaten aus derselben Revision enthalten.

Host-Staging verwendet eine eigene Release-Maintenance-Gruppe, sodass die laufende Control Plane einen unveränderlichen Kandidaten lesen, aber nicht ersetzen kann, während die Housekeeping-Dienste ein ausdrücklich ungeschütztes Release entfernen können. Erstellen Sie diese Systemgruppe einmal vor dem ersten Host-Staging:

```bash
sudo groupadd --system threadcells-release-admin
```

Die installierten Control-Plane- und Housekeeping-Units deaktivieren Python-Bytecode-Schreibvorgänge. So verändern Routineimporte weder Inhaberschaft noch Inhalt eines unveränderlichen Releases, auch nicht während die eng begrenzte Release-Maintenance-Gruppe aktiv ist.

Der Staging-Befehl schlägt geschlossen fehl, falls diese Gruppe nicht verfügbar ist. Er bewahrt Release-Kandidaten, den atomaren aktiven Pointer, die Staging-Sperre und Release-Schutzmetadaten unter einem root-eigenen `/var/lib/threadcells`-Anker außerhalb des laufzeiteigenen Zustands. Produktionsdienste führen über `/var/lib/threadcells/active` aus, nicht über einen vom Laufzeitbenutzer schreibbaren Befehlslink. Kandidatenpfade müssen unmittelbare Children von `/var/lib/threadcells/releases` sein; symbolische Links und alternative Sperr-/Metadatenziele werden abgelehnt.

## Sichere Promotion-Sequenz

1. Aktuelle aktive Laufzeit und deren Health aufzeichnen.
2. Sie als geprüften Rollback-Zielzustand bewahren.
3. Datenbankbackup erstellen und Integrität prüfen.
4. Den exakten geprüften Kandidaten mit dem kanonischen Deployment-Mechanismus des Repositorys stagen.
5. Gestagten Kandidaten erneut prüfen.
6. Gestagte Identität atomar promoten.
7. Nur erforderliche ThreadCells-Dienste neu starten.
8. Produktionsabnahme auf Loopback oder über den bestehenden geschützten Zugangspfad durchführen.

Überschreiben Sie das aktive Verzeichnis nicht an Ort und Stelle. Ein Release-Pointer/Symlink oder ein gleichwertiger kanonischer Mechanismus soll aktive, Rollback- und gestagte Kandidaten eindeutig identifizieren.

Nachdem das Staging den exakten Kandidaten aufgezeichnet hat, promoten Sie ihn über den kanonischen gesperrten Vorgang:

```bash
sudo python3 deployment/promote-ops-p1.py \
  --system-root / \
  --candidate-root /var/lib/threadcells/releases/RELEASE_ID \
  --expected-commit EXACT_PUBLIC_SHA
```

Verwenden Sie `--rollback-root`, wenn bereits ein geprüftes kanonisches Rollback-Release vorhanden ist. Der Vorgang ist idempotent: Eine Wiederholung schließt einen unterbrochenen Pointer-/Metadatenübergang ab, ohne eine neue Release-Identität zu erfinden.

## Abnahme

Prüfen Sie mindestens:

- Health und Build-Identität in Settings → About;
- Home, Agents, Flows, Statistics, Settings, Docs und Spawn Agent;
- Provider-Inventar und einen sicheren Preflight;
- Verhalten für Operator konfiguriert/gesperrt/entsperren/geschützte Mutation;
- sicheren globalen Telegram-Konfigurationszustand und, nur wenn native Zugangsdaten bereits konfiguriert sind, explizites Verbindungs-/Testverhalten;
- Terminalverbindung und Wiederverbindung;
- Fortsetzung von Workflow/Ergebnis;
- Datenbankintegrität und keine Duplizierung bei erneuter Nutzungswiedergabe;
- PWA-Manifest/Icons/Service-Worker-Registrierung ohne dynamisches Caching.

## Rollback

Rollback wechselt zum bewahrten vorherigen Kandidaten und startet nur erforderliche Dienste neu. Stellen Sie die Datenbank nur wieder her, wenn die neue Version eine inkompatible oder schädliche Migration durchgeführt hat; eine unnötige Datenbankwiederherstellung kann gültige nach der Promotion abgeschlossene Arbeit verwerfen.

Prüfen Sie nach dem Rollback Build-Identität, Health, Schema-Kompatibilität, aktive Workflows und Terminals. Bewahren Sie den fehlgeschlagenen Kandidaten und Logs auf, bis die Grundursache verstanden ist.

## Grenzen

Lokale Deployment-Autorität gewährt keine Berechtigung, Pakete zu veröffentlichen, zu einem Remote zu pushen, ein Tag/Release zu erstellen oder einen rohen Service-Port offenzulegen. Dies bleiben separate Owner-Entscheidungen.
