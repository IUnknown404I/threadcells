---
slug: web-ui
source: docs/WEB_UI.md
source_sha256: sha256:d3556d3674af5593090f679a3897b8b3a3bfe79b540c9e97ab4ffdf5f05e76d7
---

# Die Web UI verwenden

Die Web UI ist die Live-Ansicht für Betreiber von ThreadCells. Sie ist für einen Loopback-Listener ausgelegt und funktioniert gewöhnlich im Browser oder als installierte grundlegende PWA. Die Installation fügt weder Offline-Betriebsverhalten noch eine neue Authentifizierungsgrenze hinzu.

![Live-ThreadCells-Home mit dichten Zusammenfassungen von Sitzungen, Agenten und Workflows](/media/screenshots/threadcells-home.webp)

## Hauptbereiche

- **Home** fasst dauerhafte Sitzungs- und Agentenhistorie, aktuelle Aktivität, Eigentümeraufmerksamkeit und First/Last/Total-Statuszählungen zusammen, ohne jedes Terminal zu laden.
- **Agents** bietet die Ansichten Sessions, Statuses und Profiles für Terminals, Profil-/Provider-Identität, Ausführungszustand, Workflow-Zustand und dauerhafte Ergebnisse.
- **Flows** erstellt, aktiviert, deaktiviert, untersucht und startet wiederkehrende Agentenzeitpläne manuell. Die entstehenden Agenten und ihr Workflow-Lebenszyklus erscheinen unter Agents.
- **Statistics** zeigt vom Provider gemeldete Nutzung ohne erfundene Metriken.
- **Settings** enthält General, Orchestration Capacity, Profiles, Providers, Housekeeping, installationsweite Telegram-Benachrichtigungen und About.
- **Docs** stellt die mit dem laufenden Build paketierte, öffentliche zugelassene Dokumentation bereit.
- **Spawn Agent** startet eine neue Sitzung aus Projekt, Provider und Profil.
- **Add Agent** startet ein weiteres Terminal in der exakt gewählten Sitzungslaufzeit; es tritt nicht einer anderen historischen Sitzung bei, die zufällig denselben Namen hat.

Direkte URLs werden unterstützt. Der Browserverlauf sollte die gewählte Settings- und Docs-Seite beibehalten.

## Ein normaler Betriebsablauf

1. Prüfe Home auf aktuelle Sitzungs-/Workflow-Aktivität und Settings auf Host-Gesundheit, Plattenauslastung und verfügbare Kapazität.
2. Nutze Spawn Agent und bestätige, dass der gewählte Provider bereit ist.
3. Beobachte die neue Sitzung unter Agents.
4. Nutze Flows für wiederkehrende Zeitpläne. Folge den Agenten, die sie starten, unter Agents.
5. Lies dauerhafte Ergebnisse und arbeite sie ein, bevor du Children stilllegst.
6. Nutze Statistics, um die vom Provider gemeldete Nutzung zu verstehen.

Statusbezeichnungen stammen aus dauerhafter Control-Plane-Wahrheit. **Processing** bedeutet, dass ein Turn aktiv ist; **Ready** bedeutet, dass die Provider-Laufzeit lebt und wirklich untätig ist. Warteschlangenbezeichnungen unterscheiden erschöpfte Provider-Kapazität, Child-Retirement-Sperren und allgemeine Workflow-Fortsetzung. Ein Owner-gated-Badge bleibt kategorisch, während das erweiterte Owner-Decision-Panel den konkreten dauerhaften Grund zeigt.

Aktive und historische Sitzungen bleiben getrennte dauerhafte Laufzeiten. Das Löschen einer historischen Sitzung entfernt nur diese exakte berechtigte Laufzeit. Auch das Löschen eines beendeten Terminals prüft seine exakte Laufzeitidentität, Schreib-Lease, Workflow-/Ergebnisschutz und Sitzungsbeziehung vor der Bereinigung; mehrdeutiger oder aktiver Zustand bleibt geschützt.

![Live-Ansicht des Agents-Status mit aus der öffentlichen Aufnahme entfernten lokalen Worktree-Pfaden](/media/screenshots/threadcells-agents.webp)

## Geschützte Einstellungen

Sensible Mutationen teilen ein Steuerelement **Unlock operator changes**. Fehlende, ungültige, gesperrte, entsperrte und abgelaufene Zustände sind getrennt. Die exakte Mindestlänge des Secrets beträgt fünf Zeichen und die standardmäßige authentifizierte Sitzung dauert fünf Minuten.

Die UI sendet das Secret nur zum Entsperren, löscht es sofort und legt es niemals im Browser-Persistenzspeicher ab oder exportiert es. Kapazität, privilegierte Profil-/Provider-Änderungen, Telegram-Konfiguration/-Tests, Housekeeping-Ausführung und anwendbare Eigentümerstarts bleiben ohne Serversitzung gesperrt.

Folge [Betreiberautorisierung](OPERATOR_AUTHORIZATION.md), um den Verifier sicher bereitzustellen.

## Provider- und Profilauswahl

Provider-Bezeichnungen unterscheiden **Built-in adapter** von **CLI ready**, **CLI not installed**, **Authentication required**, **Installed but unhealthy** oder **Readiness unverified**. Spawn deaktiviert nur nachweislich nicht verfügbare Provider und verwendet denselben Server-Preflight wie Settings.

Profile priorisieren die durchsuchbare Entdeckung eingebauter/eigener Profile und aufgelöste Vorschauen. Der Rohimport/-export von Artefakten befindet sich bewusst unter Advanced. Die Auswahl des außergewöhnlichen Owner-XHigh-Profils zeigt eine Autoritätswarnung und erfordert seinen separaten Grant-Pfad.

## Telegram-Benachrichtigungen

Settings → Telegram konfiguriert ein installationsweites Ziel unabhängig von Projekten. Das Bot-Token ist in der UI nur schreibbar; Verbindungs- und Testnachrichten-Aktionen erfolgen explizit, und die separate bestätigte Löschaktion deaktiviert die Zustellung und entfernt das Zugangsmittel. Aktivierte Zustellung umfasst nur Abschlüsse der obersten Ebene, Owner-attention-Gates und unerwartete Fehler von Terminals der obersten Ebene, mit dauerhafter Duplikatunterdrückung und Fail-open-Zustellung. Siehe [Telegram-Benachrichtigungen](TELEGRAM_NOTIFICATIONS.md).

## Statistics

Statistics umfasst aktive, abgeschlossene und aufbewahrte nicht gelöschte Sitzungen, sobald dauerhafte Provider-Telemetrie verfügbar ist. Gecachte Eingabe und Reasoning-Ausgabe bleiben getrennt; nicht verfügbare Felder zeigen **Not reported**. Siehe [Statistics und Provider-Nutzung](STATISTICS.md).

## Docs-Reader

Die Docs-Navigation ist entlang der Lernreise gruppiert, durchsuchbar und auf breiten Bildschirmen von einer Gliederung auf der Seite begleitet. Vorherige/Nächste-Links folgen der Reihenfolge des veröffentlichten Manifests. Der Reader zeigt nur paketiertes zugelassenes Markdown; er besitzt keinen beliebigen Dateisystem-Browser oder Bearbeitungsendpunkt.

## Full Output

Full Output rendert aufbewahrten Provider-Text zur menschlichen Prüfung, nachdem ANSI/VT-Steuersequenzen und Terminal-Cursor-Manipulation entfernt wurden. Die Sanitization verhindert, dass Präsentationssteuerungen die sichtbare Historie überschreiben; sie interpretiert, führt aus oder zertifiziert den Provider-Text nicht.

## Als App installieren

Unterstützte Chromium-Browser können ThreadCells über die Installationsaktion des Browsers installieren. Das Manifest verwendet ThreadCells-Branding und öffnet im Standalone-Anzeigemodus. iOS kann **Add to Home Screen** verwenden.

Wenn der Betreiberzugriff durch Browser-Zugangsdaten geschützt ist, nutzen Manifest und verwandte Same-Origin-Anfragen dieselbe Zugangskontrolle. Cross-Origin-Zugriff bleibt auf ausdrücklich vertrauenswürdige Origins begrenzt; PWA-Metadaten umgehen weder Betreiber- noch Remotezugriffskontrollen.

Der konservative Service Worker cached nur unveränderliche statische Assets mit Fingerprint. Er cached niemals HTML-Navigation, APIs, Betreiberautorisierung, Agenten, Sitzungen, Workflows, Ergebnisse, Statistics, Terminals, WebSockets oder Mutationen. Ist der Server nicht verfügbar, meldet die installierte App den tatsächlichen Netzwerkfehler, statt einen veralteten operativen Zustand darzustellen.

Ein neuer unveränderlicher Build ersetzt alte Assets mit Fingerprint über den normalen Browser-Service-Worker-Aktualisierungslebenszyklus. ThreadCells hält den Betreiber nicht in einer veralteten Offline-Shell fest.

## Responsives Layout und Tastaturbedienung

Primäre Navigation, Docs, Settings, Tabellen und Terminal-Steuerungen unterstützen Smartphone-, Tablet- und Desktop-Breiten. Breite operative Tabellen scrollen auf schmalen Bildschirmen horizontal, statt Werte in unlesbare Größe zu schrumpfen.

Auf Smartphones verwendet jeder Home-Sitzungskopf eine eigene Namenszeile und eine separate Metadaten-/Aktionszeile. Agentenkarten verwenden immer die kanonische einspaltige Liste; der List/Grid-Selektor ist ausgeblendet. Tablet- und Desktop-Layouts bewahren ihre List/Grid-Auswahl.

Nutze die normale Tab/Shift-Tab-Navigation und sichtbare Fokusindikatoren. Codeblöcke in Docs scrollen horizontal und bieten eine Kopier-Steuerung. Das Terminal-Tastaturverhalten bleibt Provider-nativ; Touch-Scrolling sollte keine Terminaleingabe einfügen.

## Zugriffsgrenze

Die gewöhnliche UI und Docs bieten keinen allgemeinen Benutzer-Login. Halte ThreadCells auf Loopback. Nutze einen SSH-Tunnel oder einen authentifizierten Caddy/Authelia-Proxy aus [Remotezugriff](REMOTE_ACCESS.md); veröffentliche Port 9889 niemals direkt.
