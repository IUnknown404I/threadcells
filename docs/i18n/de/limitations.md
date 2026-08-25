---
slug: limitations
source: docs/LIMITATIONS.md
source_sha256: sha256:3456542efd4138cb1868100f5311749f297d32a65687a455d2a7b213ef89f050
---
# Aktuelle Einschränkungen

ThreadCells ist eine technische Vorschau, die sich auf vertrauenswürdige lokale Abläufe für Coding-Agents auf einem Linux-Host konzentriert. Diese Grenzen sind absichtliche Produktfakten, keine Versprechen über nicht implementierte Enterprise-Funktionen.

## Plattform und Umfang

- Die unterstützte Host-Basis ist Ubuntu/Debian Linux.
- Eine lokale Control Plane und SQLite-Datenbank koordinieren eine überschaubare Flotte auf einem Host.
- Kapazitätsgrenzen verringern Contention, schaffen jedoch keine harten CPU-/Speicher-Container und garantieren keinen Durchsatz.
- Sehr große Installationen über mehrere Hosts, mit hoher Verfügbarkeit oder horizontaler Skalierung liegen außerhalb des aktuellen Vertrags.

## Vertrauen und Isolation

- Native Agents werden mit dem Betriebssystemzugriff des Laufzeitbenutzers ausgeführt.
- Worktrees isolieren Git-Checkouts, nicht Dateisystem- oder Netzwerksicherheit.
- Provider-Adapter sind vertrauenswürdige ausführbare Pakete.
- Das System ist nicht für feindselige Mandantentrennung oder nicht vertrauenswürdige öffentliche Registrierung ausgelegt.

## Webzugriff

- Die gewöhnliche UI verfügt über keinen eingebauten allgemeinen Benutzer-Login.
- Der Server muss auf Loopback beschränkt bleiben, sofern er nicht durch einen externen authentifizierten HTTPS-Proxy geschützt ist.
- Die Operatorautorisierung schützt sensible Einstellungen; sie ersetzt keine externe Zugriffskontrolle.
- Die installierbare PWA ist netzwerkabhängig und stellt keine Offline-Agentensteuerung bereit.

## Provider und Telemetrie

- Die Verfügbarkeit eingebauter Adapter hängt von CLI-Installation, Kompatibilität und Providerauthentifizierung ab.
- Einige Provider können den Authentifizierungszustand nicht nicht-interaktiv melden.
- Nutzungsfelder sind nur vorhanden, wenn der Provider wahrheitsgemäße Telemetrie liefert.
- Statistics ist operative Telemetrie, keine Abrechnung; historische Unbekannte bleiben unbekannt.

## Wiederherstellung und Automatisierung

- Die Wiederherstellung gleicht dauerhaften Zustand mit externen tmux-/Provider-Prozessen ab, kann jedoch einen nicht idempotenten externen Befehl nicht umkehrbar machen.
- Backups und Wiederherstellung erfordern Operator-Disziplin und sollten eingeübt werden.
- Housekeeping lässt mehrdeutige Artefakte absichtlich bestehen.
- Full Cleanup ist maximal nachweislich sichere Freigabe und keine Garantie, dass jede große Datei entbehrlich ist. Es erfordert, dass alle Agenten untätig sind, kann mehrdeutige Tools/Backups/Worktrees aufbewahren und lässt nach erfolgreicher Bereinigung inaktiver Releases absichtlich keinen lokalen Release-Rollback zurück.
- Automatisierung für Veröffentlichung und Remote-Releases gehört absichtlich nicht zum gewöhnlichen lokalen Deployment.

Evaluieren Sie ThreadCells zuerst auf nicht kritischen Repositories, bewahren Sie verifizierte Backups auf und prüfen Sie die Agentenausgabe vor folgenreichen Maßnahmen.
