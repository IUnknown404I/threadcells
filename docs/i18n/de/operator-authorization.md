---
slug: operator-authorization
source: docs/OPERATOR_AUTHORIZATION.md
source_sha256: sha256:543fc9c31e1ffe8e120aa726c819f0e9180d0f6ca92b28c9b4ce549d0025d4b1
---

# Operatorautorisierung

Die Operatorautorisierung schützt sensible Control-Plane-Änderungen in Settings. Sie ist vom Zugriff auf die gewöhnliche Web-UI getrennt: Das Durchsuchen von Agenten, Terminals, Dokumenten und Statistiken erfordert nicht das Operatorgeheimnis.

Diese Funktion ist keine Remote-Benutzerauthentifizierung. Halten Sie ThreadCells nur auf Loopback und folgen Sie [Remotezugriff](REMOTE_ACCESS.md), wenn ein anderes Gerät Zugriff benötigt.

## Funktionsweise

ThreadCells speichert einen aus dem Geheimnis abgeleiteten Verifier, niemals das Klartextgeheimnis. Der Server lädt diesen Verifier beim Start. Die Eingabe des korrekten Geheimnisses erstellt eine kurzlebige, sichere Operator-Sitzung; geschützte Mutationen bleiben nach ihrem Ablauf gesperrt.

```text
Verifier configured
      ↓
Settings shows Locked
      ↓ enter operator secret
Unlock operator changes
      ↓
Short-lived authenticated session
      ↓ expires
Locked again
```

Die Mindestlänge des Operatorgeheimnisses beträgt genau **5 Zeichen**. Vier Zeichen werden abgelehnt. Ein längeres, zufällig generiertes Geheimnis wird dringend empfohlen.

## Einen Verifier erstellen

Führen Sie den eigenständigen Befehl als administrativer Benutzer aus einem beliebigen lesbaren Arbeitsverzeichnis aus:

```bash
threadcells operator create-verifier --output /etc/threadcells/operator-verifier.json
```

Der Befehl fragt ohne Echo des Geheimnisses ab und schreibt nur den gesalzenen KDF-Verifier. Schützen Sie das enthaltende Verzeichnis vor Änderungen durch das ThreadCells-Dienstkonto und erlauben Sie diesem Konto gleichzeitig, die Datei zu lesen. Eine geeignete Anordnung ist:

```bash
sudo chown root:threadcells /etc/threadcells
sudo chmod 0750 /etc/threadcells
sudo chown root:threadcells /etc/threadcells/operator-verifier.json
sudo chmod 0640 /etc/threadcells/operator-verifier.json
```

Passen Sie den Gruppennamen an das von Ihrer Installation verwendete Dienstkonto an. Jedes Elternverzeichnis im Pfad muss ebenfalls vertrauenswürdig sein: ThreadCells lehnt einen Verifier ab, der über ein dienstkontoeigenes oder für Gruppe/alle beschreibbares Verzeichnis erreicht wird.

Legen Sie das Geheimnis oder Verifier-JSON nicht im Repository, in der Datenbank, in Logs, im Browserspeicher, in Telemetrie oder in einer API-Anfrage außerhalb der Entsperroperation ab.

## Den Server konfigurieren

Setzen Sie die absolute Verifier-Referenz in der Serverumgebung:

```bash
THREADCELLS_OPERATOR_VERIFIER_FILE=/etc/threadcells/operator-verifier.json
```

Starten Sie nur den ThreadCells-Server neu und prüfen Sie Settings → General → Operator authorization. Der Zustand sollte **Configured · Locked** sein, nicht **Not configured** oder **Configuration invalid**.

Der Sitzungsendpunkt meldet nur sicheren Zustand:

```bash
curl -s http://127.0.0.1:9889/operator/session
```

Das erwartete Ergebnis enthält vor dem Entsperren `"configured": true` und `"authenticated": false`. Es gibt niemals Verifier-Pfad, Salt, Hash oder Geheimnis zurück.

## Geschützte Änderungen entsperren

Geben Sie in Settings das Geheimnis ein und wählen Sie **Unlock operator changes**. Das standardmäßige authentifizierte Zeitfenster beträgt fünf Minuten. Die UI zeigt den Ablaufzeitpunkt an und kehrt nach Sitzungsende zum gesperrten Zustand zurück.

Geschützte Settings-Aufrufe schlagen im gesperrten Zustand fehl und sind während der authentifizierten Sitzung erfolgreich. Der Browser verwendet das kurzlebige sichere Sitzungscookie des Servers; er speichert das Operatorgeheimnis nicht dauerhaft.

Full Cleanup verwendet exakt dieselbe Autorität. Die Vorschau bleibt als schreibgeschützte Sicherheitsprüfung verfügbar, während die Ausführung die aktuelle Operatorsitzung und die standardmäßige Bestätigung einer dauerhaften Aktion erfordert. Die Bestätigung fragt das Secret nicht erneut ab. Es gibt weder ein separates Cleanup-Secret noch URL-Zugangsdaten, einen Wert im Browserspeicher oder eine dauerhafte Klartextkopie; Ablauf, erneutes Sperren und Rate-Limits bleiben unverändert.

## Das Geheimnis ersetzen

Erstellen Sie einen neuen Verifier an einem temporären administrativen Pfad, validieren Sie Ownership und Berechtigungen und ersetzen Sie dann die konfigurierte Datei atomar; starten Sie ThreadCells neu. Bestehende Operator-Sitzungen sollten nach dem Ersatz als ungültig behandelt werden.

Die aktuelle Web-UI bietet bewusst weder einen unauthentifizierten Remote-Reset noch einen Verifier-Writer in Settings. CLI-Provisionierung hält den Verifier unter Betriebssystem-Ownership und vermeidet die Schaffung eines umfassenderen Sicherheitssystems.

## Owner-XHigh-Start

Das integrierte Profil `critical_sol_xhigh_owner` ist über **Create Session & Spawn Agent**, **Add Agent** für eine bestehende Sitzung und die lokale CLI verfügbar. Beide Web-Abläufe zeigen dieselbe Warnung zur außergewöhnlichen Autorität, verlangen ausdrückliche Bestätigung und eine entsperrte Operator-Sitzung, prägen eine kurzlebige revisions-/scope-gebundene Einmal-Capability und verbrauchen sie über den normalen Startpfad. Add Agent bindet die Capability an die bestehende Sitzung und ihr kanonisch aufgelöstes Arbeitsverzeichnis; der Operator kann keinen beliebigen Ersatzpfad eingeben.

Der lokale CLI-Pfad erfordert `--owner-xhigh` und eine ausdrückliche interaktive Bestätigung. Er prägt und verbraucht dieselbe Klasse von Einmal-Capability über Loopback. Es gibt keine wiederverwendbare Bypass-/Header-Abkürzung: Ein fehlendes Kontrollkästchen/eine fehlende Bestätigung, fehlendes oder falsches Operatorgeheimnis, nicht passender Scope oder wiederverwendeter Grant schlägt fehlgeschlossen fehl. Der authentifizierte Webclient erhält die undurchsichtige Capability genau einmal und ausschließlich zum Ausführen des passenden Starts; das Operatorgeheimnis wird nie zurückgegeben. Keiner der beiden Werte wird in Agenten-/Sitzungsmetadaten, Anbieterprompts, Terminaltranskripte, Logs oder Browserspeicher kopiert. Diese Startpfade autorisieren weder Childs noch schwächen sie geschützte Settings-Mutationen.

## Fehlerbehebung

- **Not configured:** Die Umgebungsvariable fehlt oder ist leer. Bestätigen Sie, dass sie den tatsächlichen Serverprozess erreicht, und starten Sie dann neu.
- **Configuration invalid:** Prüfen Sie die Serverlogs auf den sicheren Validierungsgrund. Prüfen Sie JSON-Schema, absoluten Pfad, Lesbarkeit, Owner, Modus und jedes Elternverzeichnis. Erstellen Sie einen gültigen Verifier nicht neu, nur um ein Pfad- oder Ownership-Problem zu verbergen.
- **Correct secret rejected:** Stellen Sie sicher, dass Generator und Server dieselbe Verifier-Datei verwenden und kein alter Serverprozess noch läuft.
- **Unlock succeeds then immediately locks:** Bestätigen Sie, dass Browser-Cookies akzeptiert werden und die Systemuhr korrekt ist.
- **Unlock works locally but protected changes fail through an HTTPS proxy:** Setzen Sie `THREADCELLS_TRUSTED_PROXY_ORIGINS` in der ThreadCells-Dienstumgebung auf den exakten öffentlichen HTTPS-Origin (zum Beispiel `https://threadcells.example.com`) und starten Sie dann neu. Fügen Sie keine Pfade, Wildcards oder nicht authentifizierten Origins hinzu.
- **Verifier creation fails in an unrelated directory:** Verwenden Sie einen aktuellen ThreadCells-Build. Der eigenständige Befehl darf kein arbeitsverzeichnislokales `.env` prüfen.

Siehe [Sicherheitsmodell](SECURITY_MODEL.md) für die umgebenden Vertrauensannahmen.
