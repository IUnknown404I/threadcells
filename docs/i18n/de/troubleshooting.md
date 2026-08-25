---
slug: troubleshooting
source: docs/TROUBLESHOOTING.md
source_sha256: sha256:f6ed11a9ba51cf79c46f46c4e50ce638862a009bcbc3323f875bccfc49ed14a2
---
# Fehlerbehebung

Beginnen Sie mit dem Bewahren von Nachweisen: aktuelle Build-Identität, sichere Fehlermeldung, betroffene Sitzung/Workflow, Kapazitätsstatus, aktuelle Logs und Git-Status. Vermeiden Sie Bereinigung, Löschen oder blindes Wiederholen, bis Sie wissen, ob ein dauerhafter Vorgang bereits erfolgreich war.

## Web-UI startet nicht

**Prüfungen:** Server im Vordergrund ausführen, `curl -fsS http://127.0.0.1:9889/health` aufrufen, bestätigen, dass der Port auf Loopback lauscht, und Settings → About prüfen, falls verfügbar.

**Lösung:** Gemeldeten Abhängigkeits-/Konfigurationsfehler oder Portkonflikt beheben. Funktioniert Health, aber nicht die statischen Dateien, prüfen Sie den Kandidaten und stellen Sie sicher, dass Python-Code und paketierte Web-Assets aus demselben Build stammen.

## Browser auf einem anderen Rechner kann keine Verbindung herstellen

Dies ist erwartet, wenn ThreadCells korrekt auf Loopback lauscht. Ändern Sie dies nicht zu einem öffentlichen Bind. Verwenden Sie den SSH-Tunnel oder authentifizierten Proxy in [Remote-Zugriff](REMOTE_ACCESS.md).

## Provider meldet CLI nicht installiert

**Prüfungen:** Settings → Providers mit Spawn Agent vergleichen und dann `command -v PROVIDER_COMMAND` als ThreadCells-Laufzeitbenutzer ausführen.

**Lösung:** Kanonische CLI des Providers für dieses Konto installieren, dessen Service-`PATH` korrigieren oder einen anderen bereiten Provider wählen. Adapterregistrierung allein ist keine Installation.

## Provider installiert, aber nicht authentifiziert

**Prüfungen:** Den vom Provider unterstützten Befehl für den Authentifizierungsstatus als Laufzeitbenutzer ausführen.

**Lösung:** Den nativen Login-Flow des Providers abschließen. ThreadCells kopiert weder Zugangsdaten eines anderen Benutzers noch meldet es sich während des Preflight an.

## Provider meldet Bereitschaft nicht verifiziert

Der Befehl existiert, kann aber keine sichere nicht-interaktive Authentifizierungswahrheit bereitstellen. Prüfen Sie seine Version und führen Sie einen kleinen nativen Test aus. Er kann startbar bleiben; prüfen Sie das resultierende Terminal auf einen Provider-Login-Prompt.

## Agent startet nicht

**Prüfungen:** Provider-Bereitschaft, aufgelöste Vorschau des ausgewählten Profils, Projektpfad/-berechtigungen, Resident-/Provider-/Work-Kapazität, tmux-Verfügbarkeit und Terminal-Startausgabe.

**Lösung:** Erste fehlgeschlagene Zulassung oder Provider-Voraussetzung korrigieren. Starten Sie nicht wiederholt Duplikate, während die erste Sitzung noch startet.

## Kapazität erschöpft

Orchestration Capacity öffnen und die exakt volle Kategorie identifizieren. Sicher abgeschlossene Arbeit stilllegen oder auf die entsprechende Provider-/Heavy-Aufgabe warten. Erhöhen Sie nur dieses Limit, wenn Host und Kontingent gemessenen Spielraum haben.

## Heavy-Ausführungs-Slot nicht verfügbar

Ein Build, Browser-Test, Scan oder Recovery-Job belegt den Heavy-Slot. Warten Sie oder untersuchen Sie einen veralteten Lease über den kanonischen Status. Führen Sie keinen teuren Befehl außerhalb der Zulassung aus, nur um die Warteschlange zu umgehen.

## Workflow wartet auf Owner

Lesen Sie den Gate-Grund. Treffen Sie die angeforderte Entscheidung nur, wenn sie eine echte Grenze bei Veröffentlichung, Vertrauen, Destruktivität, Kosten oder Produktsemantik darstellt. Ein gewöhnliches Provider-Finale soll berechtigte autonome Arbeit offen lassen; melden Sie einen automatischen Abschluss als Workflow-Defekt.

## Ergebnis nicht eingearbeitet

Bestätigen Sie, dass das Child ein dauerhaftes Ergebnis aufgezeichnet hat und es dem richtigen Parent zugestellt wurde. Der Parent muss das unveränderliche Ergebnis lesen/verwenden und dann die Einarbeitung bestätigen. Ein Neustart-Replay kann ein unbestätigtes Ergebnis erneut zustellen; wenden Sie es nicht zweimal an.

## Neue Owner-Eingabe bleibt hinter geschlossenem Workflow in Warteschlange

Unterstützte Laufzeit einmal neu starten und exakte Workflow- und Inbox-Identitäten prüfen. Aktuelle Builds gleichen einen ausstehenden gewöhnlichen Inbox-Transport ab, dessen gebundener Workflow nicht mehr offen ist, und lassen anschließend den neueren offenen Owner-Turn fortsetzen. Binden Sie die Inbox-Zeile nicht neu und bearbeiten Sie sie nicht manuell; bewahren Sie die Datenbank und melden Sie einen Defekt, falls der veraltete Transport ausstehend bleibt oder ein Payload die Workflow-Identität überschreitet.

## Operator-Autorisierung nicht konfiguriert

Bestätigen Sie, dass `THREADCELLS_OPERATOR_VERIFIER_FILE` den tatsächlichen Serverprozess erreicht, und starten Sie ihn neu. Ist die Konfiguration ungültig, prüfen Sie Schema, absoluten/kanonischen Pfad, Dateieigentümer/-modus, Lesbarkeit und jedes übergeordnete Verzeichnis. Das Dienstkonto darf den Verifier nicht besitzen oder ersetzen können.

## Korrektes Operator-Geheimnis schlägt fehl

Bestätigen Sie, dass der Server denselben Verifier geladen hat, den die CLI erzeugt hat. Das Minimum beträgt genau fünf Zeichen. Prüfen Sie auf einen alten Serverprozess oder einen kürzlich ersetzten Verifier; protokollieren Sie das eingegebene Geheimnis nicht.

## Telegram ist nicht konfiguriert oder ein Test schlägt fehl

Settings → Telegram nach dem Entsperren der Operatoränderungen öffnen. `Not configured` erfordert sowohl ein gültiges Bot-Token als auch eine Chat-ID. `Invalid` bedeutet, dass die private Token-Datei ihre Prüfungen von Inhaberschaft, regulärer Datei oder Modus nicht bestanden hat. Eine erfolgreiche Verbindungsprüfung validiert die Bot-Zugangsdaten; senden Sie eine explizite Testbenachrichtigung, um Chat und optionale Topic-ID zu validieren. Prüfen Sie ausgehendes HTTPS/DNS, falls eine Aktion fehlschlägt. Sichere Fehler lassen Telegram-Antwortkörper und das Token absichtlich aus. Siehe [Telegram-Benachrichtigungen](TELEGRAM_NOTIFICATIONS.md).

## Statistics fehlt eine aktuelle Sitzung

Nutzung/Status aktualisieren, bestätigen, dass der Provider Telemetrie unterstützt, und prüfen, dass seine dauerhaften Rollout-Nachweise lesbar bleiben. Sitzungen müssen vor dem Zählen nicht gelöscht werden. Fehlende Provider-Felder sollen Not reported, nicht null, anzeigen.

## Statistics-Gesamtsumme wirkt dupliziert

Globale, Sitzungs- und Terminaldimensionen vergleichen und die Datenbank bewahren. Kumulative Provider-Snapshots sollen einen stabilen Checkpoint über Poll/Neustart/Replay aktualisieren. Löschen Sie vor der Diagnose keine Zeilen manuell.

## Docs-/Build-Identität stimmt nicht überein

Settings → About, Docs-Footer, Kandidatenmanifest und Revision statischer Assets sollen übereinstimmen. Einen unveränderlichen Kandidaten neu bauen und prüfen; Web-Ausgabe aus einem Checkout nicht mit Python-Code aus einem anderen kombinieren.

## Festplattendruck oder Housekeeping kann nicht freigeben

Einen Housekeeping-Dry-Run-Plan prüfen. Geschützte, aktive, unbekannte, Backup-, aktuelle und Rollback-Elemente werden absichtlich erhalten. Gemeldete Owner-/Referenzbeziehung beheben oder Speicherplatz sicher erweitern; niemals den Laufzeit-Root rekursiv löschen.

Für die maximal nachweislich sichere Freigabe prüfen Sie die separate Full-Cleanup-Vorschau. Die Ausführung bleibt blockiert, bis jeder Agent nach autoritativer Prüfung untätig ist und weder Provider-, Heavy- noch Warteschlangenmutation oder Laufzeitoperation aktiv ist. Schließen Sie Ready-Agenten nicht und schwächen Sie dieses Gate nicht: Ihr Fortsetzungszustand bleibt geschützt. Full Cleanup entfernt alle nachweislich inaktiven lokalen Releases; bestätigen Sie daher, dass der Verlust des lokalen Rollbacks akzeptabel ist. Geschützte mehrdeutige Tools, Backups, Source-Autorität, schmutzige oder unveröffentlichte Worktrees und unbekannte Pfade sind erwartete Berichtseinträge und kein Grund, sie manuell zu löschen.

## Full Output meldet eine bereinigte Ausgabe

Ein beendeter historischer Agent kann in SQLite verbleiben, nachdem Full Cleanup sein altes dauerhaftes Log entfernt hat. Dies ist ein wahrheitsgemäßer Zustand mit aufbewahrten Metadaten: Sessions und Agents bleiben nutzbar, während Full Output `DURABLE_OUTPUT_UNAVAILABLE` meldet. Die Ausgabe aktueller und Ready-Agenten ist geschützt. Stellen Sie aus einem aufbewahrten Backup wieder her, wenn der historische Text benötigt wird; erfinden Sie kein Log und hängen Sie kein anderes an.

## Browser-Terminal verbindet sich nach Neustart nicht wieder

Einmal aktualisieren, bestätigen, dass Server und tmux-Sitzung gesund sind, und die WebSocket-Verbindung des Browsers über jeden Reverse Proxy prüfen. Sicherstellen, dass Caddy oder ein anderer Proxy keine Upgrade-Header entfernt. Eine installierte PWA cached keinen Terminal- oder WebSocket-Zustand.

## Weiterhin blockiert

Kleinsten reproduzierbaren Nachweis aufbewahren und vor breiten Suites die fokussierten Komponentenprüfungen ausführen. In Issue-Berichte nur öffentlich sichere Pfade und Meldungen aufnehmen. Siehe [Contributing](../CONTRIBUTING.md) für Erwartungen an Berichte.
