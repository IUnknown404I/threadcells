---
slug: architecture
source: docs/ARCHITECTURE.md
source_sha256: sha256:0fa43fdddc696e3203367cd85ab6b0ca6ec9d4c03753ebdfea3fc1d336507447
---
# Architektur

ThreadCells ist eine lokale Control Plane um native Coding-Agent-Prozesse. Provider-Terminal, Git-Repository, dauerhafter Koordinationszustand und Browser-UI bleiben bewusst getrennte Komponenten mit expliziten Grenzen.

Beginnen Sie mit [Grundkonzepte](CONCEPTS.md), falls die folgenden Begriffe neu sind.

## Systemansicht

```text
Browser or installed PWA
        ↓ HTTP / WebSocket on loopback
FastAPI ThreadCells server
  ├── SQLite durable state
  ├── provider/profile registries
  ├── workflow and result service
  ├── capacity and Housekeeping service
  └── tmux/provider adapter control
               ↓
        Native provider CLIs
               ↓
      Git repositories/worktrees
```

## Server und Web-UI

Der FastAPI-Server stellt Anwendung/API bereit und liefert einen produktiven Web-Build aus. Die React-UI liest den laufenden Betriebszustand und verbindet sich über WebSockets mit Terminal-Streams.

Der grundlegende PWA-Worker cached nur statische Assets mit Fingerprint. HTML, APIs, Autorisierung, Sitzungen, Workflows, Statistics, Terminals, Mutationen und WebSockets bleiben netzwerkabhängig, damit die UI keinen Offline-Control-Plane-Zustand erfinden kann.

Das Docs-Bundle wird beim Build aus `DOCS_MANIFEST.json` erzeugt. Nur erlaubtes öffentliches Markdown gelangt in die Laufzeit.

## Dauerhafter Zustand

SQLite enthält Sitzungen, Terminals, Projekte, Profil-/Provider-Revisionen, Ressourcen-Leases, Workflows, Ergebnisse, Nutzungsdatensätze, Audit-Ereignisse und Scheduling-Quittungen. Vorgänge, die genau einmal oder replay-sicher erfolgen müssen, verwenden stabile Identitäten und Datenbanktransaktionen statt sich auf flüchtige Terminalausgaben zu verlassen.

Provider-Prozesse und tmux-Sitzungen sind externe Laufzeitfakten. Start/Wiederherstellung gleicht sie mit der Datenbank ab; sie darf nicht annehmen, dass die Existenz einer Seite beweist, dass die andere aktuell ist.

## Provider-Ausführung

Ein Adapter übersetzt einen normalisierten ThreadCells-Start in einen geprüften nativen CLI-Aufruf. Der Provider rendert weiterhin sein eigenes Terminal-UI und verwaltet seine eigene Authentifizierung. Adapter melden Fähigkeiten und Preflight-Wahrheit, statt nicht unterstütztes Verhalten zu simulieren.

Strukturierte Provider-Telemetrie wird in dauerhafte Nutzungsdatensätze normalisiert. Kumulative Zähler verwenden stabile Checkpoints, sodass Polling und Neustart keine Summen duplizieren.

## Git-Arbeitskontexte

Verwaltete Worktrees teilen die Objektdatenbank des Repositorys, isolieren jedoch Checkout-Pfade und Branches. Schreibautorität hält die Inhaberschaft von Mutationen explizit. Worktrees sind Parallelitätswerkzeuge, keine Betriebssystem-Sandboxes.

## Workflows und Ergebnisse

Der Workflow-Zustand übersteht einzelne Provider-Turns. Delegierte Ergebnisse werden aufgezeichnet, mindestens einmal zugestellt, vom Parent eingearbeitet und bestätigt, bevor ein Child zur Stilllegung berechtigt ist. Expliziter Abschluss — nicht ein Modellfinale — schließt die Top-Level-Mission.

## Zulassung und Druck

Residente Supervisoren, Provider-Ausführungen, Arbeitskontexte und Heavy-Ausführungen haben unabhängige Leases und Limits. Festplattendruck und Housekeeping-Schutz sind zusätzliche Laufzeitbeschränkungen. Prozessübergreifende Sperren stellen sicher, dass nicht zwei Prozesse beide glauben, den letzten Slot erworben zu haben.

## Sicherheitsgrenze

ThreadCells setzt einen vertrauenswürdigen Host und eine vertrauenswürdige Operatorumgebung voraus. Allgemeiner UI-Zugriff wird extern durch Loopback/SSH oder einen authentifizierten Reverse Proxy geschützt. Sensible Settings-Mutationen verwenden eine eigene Operator-Verifier-/Sitzungsgrenze, dies ist jedoch kein allgemeines Login-System.

Provider-Pakete und native CLIs sind vertrauenswürdiger ausführbarer Code. Importierte Konfiguration ist eingeschränkte deklarative Daten. Siehe [Sicherheitsmodell](SECURITY_MODEL.md).
