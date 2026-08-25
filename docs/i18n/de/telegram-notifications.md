---
slug: telegram-notifications
source: docs/TELEGRAM_NOTIFICATIONS.md
source_sha256: sha256:c1c50ae5d9e7937dff2794e49e2914929e7d02e4adb3de0c2f04c7cd5d656735
---

# Telegram-Benachrichtigungen

ThreadCells kann geräuscharme Lebenszyklusbenachrichtigungen an ein Telegram-Ziel senden. Dies ist eine installationsweite ThreadCells-Fähigkeit: Sie gehört nicht zum aktuell ausgewählten Projekt, liest keine Konfiguration daraus und hängt nicht davon ab.

![Live-Telegram-Benachrichtigungseinstellungen mit ausdrücklich geschwärzten Ziel- und Anmeldedatenfeldern](/media/screenshots/threadcells-telegram.webp)

## Das Ziel konfigurieren

1. Erstellen oder wählen Sie einen Telegram-Bot über den unterstützten Bot-Verwaltungsablauf von Telegram.
2. Ermitteln Sie die Chat-ID des Ziels. Ermitteln Sie für ein Forum-Thema außerdem dessen positive Message-Thread-ID.
3. Öffnen Sie **Settings → Telegram** und entsperren Sie Operator-Änderungen.
4. Geben Sie Bot-Token, Chat-ID und optionale Topic-/Thread-ID ein.
5. Speichern Sie, während Benachrichtigungen deaktiviert sind.
6. Verwenden Sie **Check connection**, um die Bot-Anmeldedaten zu validieren, und anschließend **Send test notification**, um das Ziel zu validieren.
7. Aktivieren Sie Benachrichtigungen und speichern Sie erneut.

Die Testaktion ist ausdrücklich; das Öffnen von Settings kontaktiert Telegram nie. Das Deaktivieren von Benachrichtigungen behält das konfigurierte Ziel und Token bei, damit sie später wieder aktiviert werden können. **Clear bot token** ist eine separate bestätigte Operatoraktion: Sie entfernt die Anmeldedaten, deaktiviert Benachrichtigungen und behält die nicht geheimen Zielfelder.

## Umgang mit Geheimnissen

Die Web-UI sendet ein neues Token nur bei einer geschützten Aktualisierung und leert anschließend ihr Passwortfeld. Lese-APIs melden nur `Configured`, `Not configured` oder `Invalid`; sie geben das Token nie zurück. ThreadCells legt das Token nicht im Browserspeicher, in Terminal-Prompts, Sitzungs- oder Agentenmetadaten, gewöhnlichen Logs oder der SQLite-Settings-Zeile ab.

Der Server speichert das Token unter:

```text
$CAO_HOME_DIR/secrets/telegram-bot-token
```

Das Elternverzeichnis ist auf das Laufzeitkonto beschränkt und die Token-Datei verwendet Modus `0600`. Der Ersatz verwendet eine atomare Dateisystemumbenennung; das Löschen entfernt die Anmeldedaten ohne ihnen zu folgen und synchronisiert das Geheimnisverzeichnis. `CAO_HOME_DIR` ist der private veränderliche Zustandsstamm der Installation, kein öffentlicher Repository-Pfad.

Behandeln Sie diese Datei als Anmeldedatum. Kopieren Sie sie nicht in Quellcodeverwaltung, gewöhnliche Support-Bundles, Datenbankexporte, Shell-Historie oder Screenshots. Rotieren Sie sie über Telegram, wenn eine Offenlegung vermutet wird.

## Benachrichtigungsrichtlinie

Die Richtlinie des ersten Releases sendet höchstens einen Versuch für jedes dauerhafte Top-Level-Workflow-Ereignis:

- erfolgreicher Top-Level-Abschluss;
- ein Top-Level-Gate, das Owner-Aufmerksamkeit erfordert;
- unerwarteter Fehler eines Top-Level-Terminals, während sein Workflow offen ist.

ThreadCells benachrichtigt nicht über Child-Abschluss, Delegierung, Polling, Fortschrittsaktualisierungen, interne Wiederholungszyklen oder jeden Modell-/Tool-Zug. Dauerhafte Ereignisschlüssel verhindern, dass eine wiederholte Beobachtung oder ein Neustart eine bereits beanspruchte Zustellung dupliziert.

Nachrichten enthalten nur knappen sicheren Kontext: ThreadCells-Identität, Sitzung, Projektanzeigename, falls vorhanden, Lebenszykluszustand, eine feste Zusammenfassung und UTC-Zeitstempel. Sie enthalten keine Prompts, Modellausgabe, Dateisystem-Dumps, Ausnahmeinhalte, Operatorgeheimnisse oder das Bot-Token.

## Verhalten bei Fehlern

Die Telegram-Zustellung ist für Agentenarbeit fail-open. Ein Timeout, abgelehnte Anmeldedaten oder ein nicht verfügbarer Telegram-Dienst zeichnet einen sicheren Ergebniscode auf, kann jedoch den Workflow nicht fehlschlagen lassen oder wieder öffnen. Die Zustellung hat genau einen begrenzten Versuch; ThreadCells wiederholt nicht endlos und gibt historische Ereignisse nicht erneut aus, nachdem Benachrichtigungen aktiviert wurden.

**Check connection** validiert das Bot-Token mit Telegram. **Send test notification** validiert auch das konfigurierte Chat-/Topic-Routing. Eine erfolgreiche Verbindungsprüfung beweist nicht, dass der Bot an das gewählte Ziel schreiben kann; verwenden Sie bei der Konfiguration eines neuen Ziels beide Aktionen.

## Sicherung und Wiederherstellung

Der nicht geheime Aktiviert-/Zielzustand und das Zustellungsledger liegen in der ThreadCells-Datenbank. Das Bot-Token ist getrennt. Wenn Benachrichtigungen eine Notfallwiederherstellung überstehen müssen, sichern Sie das Token als separat verschlüsseltes Anmeldedatum mit erhaltener Ownership und Modus; fügen Sie es nicht zu einem routinemäßigen Klartext-Datenbankarchiv hinzu.

Prüfen Sie nach der Wiederherstellung Geheimnispfad und Berechtigungen, lassen Sie Benachrichtigungen zunächst deaktiviert, führen Sie beide ausdrücklichen Prüfungen aus und aktivieren Sie dann die Zustellung. Die Wiederherstellung der Datenbank ohne Token meldet sicher `Not configured`.

## Fehlerbehebung

- **Not configured:** Geben Sie vor dem Aktivieren sowohl ein gültiges Bot-Token als auch eine Chat-ID an.
- **Invalid token storage:** Prüfen Sie, dass das Token eine reguläre Datei ohne Symlink ist, dem Laufzeitkonto gehört und keine Gruppen-/Andere-Berechtigungen besitzt.
- **Connection failed:** Prüfen Sie ausgehendes HTTPS/DNS und rotieren oder ersetzen Sie ein abgelehntes Bot-Token; sichere UI-Fehler lassen Telegram-Antwortdetails absichtlich aus.
- **Connection works but test fails:** Bestätigen Sie, dass der Bot zum Ziel gehört und dort posten kann; prüfen Sie Chat- und optionale Topic-IDs.
- **No lifecycle message:** Bestätigen Sie, dass Enabled eingeschaltet ist, und denken Sie daran, dass nur Top-Level-Abschluss, Owner-Aufmerksamkeit und unerwarteter Top-Level-Fehler benachrichtigen. Ereignisse, die im deaktivierten Zustand auftraten, werden nicht erneut ausgegeben.
