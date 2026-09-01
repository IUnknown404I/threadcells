---
slug: concepts
source: docs/CONCEPTS.md
source_sha256: sha256:558a270183e49568ee5d52d6efd30c6a8215fe7d563df596ee9391313d8f3299
---

# Kernkonzepte

ThreadCells fügt Struktur um native Coding-Agent-Terminals hinzu. Diese Seite führt jeweils eine Idee ein und zeigt dann, wie die Teile zusammenpassen.

## Agent

Ein **Agent** ist eine Provider-CLI, die mit Prompt, Rolle, Profil und Projektkontext läuft. Er kann Dateien untersuchen, Tools verwenden, bei Berechtigung Code schreiben und ein Ergebnis zurückgeben.

Ein Agent ist nicht nur der Modellname. Zwei Agenten können dasselbe Modell verwenden, aber unterschiedliche Rollen, Berechtigungen, Reasoning-Einstellungen und Worktrees haben.

## Terminal

Ein **Terminal** ist die echte, tmux-gestützte Prozessumgebung, in der ein Agent läuft. Es bewahrt die native Provider-Ausgabe und erlaubt dem Betreiber, sich nach dem Schließen des Browsers wiederzuverbinden.

Das Terminal kann enden, während sein dauerhaftes Ergebnis bestehen bleibt. Umgekehrt beweist ein noch existierendes Terminal nicht, dass nützliche Arbeit weiter fortschreitet.

## Sitzung

Eine **Sitzung** ist die dauerhafte Laufzeit von ThreadCells für eine zusammengehörige Gruppe von Agentenläufen: Identität, Lebenszyklus, Terminals, Provider, Profile, Projekt, Nutzung und Ergebnisbeziehungen. **Add Agent** fügt ein Terminal dieser exakten Sitzungslaufzeit hinzu, statt die Mitgliedschaft aus einem wiederverwendeten Anzeigenamen abzuleiten. Sitzungen erlauben Statistics und Workflows, über aktive, abgeschlossene, historische oder aufbewahrte Läufe nachzudenken.

## Projekt

Ein **Projekt** identifiziert die kanonische Git- und Quellcode-Autorität für die Arbeit. Es gibt ThreadCells einen stabilen Geltungsbereich für Sitzungen, Worktrees und Ergebnisse; der registrierte Quellstamm ist nicht das normale beschreibbare cwd eines neuen Supervisors und ersetzt weder Git-Remotes noch Repository-Berechtigungen.

## Verwalteter Worktree

Ein **verwalteter Worktree** ist ein Git-Worktree, der für einen begrenzten beschreibbaren Kontext angelegt wurde. Jede neue mit einem Projekt verbundene Supervisor-Sitzung, einschließlich der ersten, erhält einen. Unabhängige Sitzungen im selben Projekt verwenden verschiedene Branches und Checkouts; ein Recovery Takeover desselben Kontexts bewahrt dessen bestehenden Worktree.

Worktrees verringern Kollisionen; sie sind keine Sicherheits-Sandboxes. Ein Agent kann weiterhin alles erreichen, was sein Betriebssystemkonto erreichen kann.

## Schreibberechtigung

Die **Schreibberechtigung** beantwortet, wer einen bestimmten Arbeitskontext verändern darf. ThreadCells hält diese Eigentümerschaft explizit, damit zwei unabhängig aktive Agenten nicht versehentlich als sichere gleichzeitige Schreiber desselben Worktrees gelten.

Ein Reviewer braucht oft Lesezugriff, aber keine Schreibberechtigung. Ein Entwickler, der eine Implementierung ausführt, braucht sie.

## Provider

Ein **Provider** verbindet ThreadCells mit einer nativen Coding-Agent-CLI wie Codex oder Claude Code. Drei Zustände sind wichtig:

1. ThreadCells enthält einen Provider-Adapter.
2. Die zugehörige CLI ist für den Laufzeitbenutzer installiert.
3. Diese CLI ist gesund und ausreichend authentifiziert, um zu starten.

Dass ein Adapter in Settings aufgeführt ist, bedeutet nicht, dass die externe CLI installiert ist. Siehe [Provider](PROVIDERS.md).

## Profil

Ein **Profil** ist eine wiederverwendbare Start-Richtlinie. Es wählt Provider/Modell und Reasoning-Stufe, liefert Anweisungen und Fähigkeiten, definiert eine Rolle und kann einschränken, wie ein Agent an der Orchestrierung teilnimmt.

Eingebaute Profile stellen sichere, bekannte Rollen bereit. Eigene Profile lassen Betreiber diese Rollen anpassen, ohne Anwendungscode zu ändern.

## Supervisor und Worker

Ein **Supervisor** verantwortet eine größere Mission. Er kann sie in begrenzte Aufgaben teilen, an Worker senden, deren dauerhafte Ergebnisse einsammeln, ein Review anfordern und entscheiden, wann die Mission wirklich abgeschlossen ist.

Ein **Worker** oder **delegierter Agent** verantwortet eine dieser begrenzten Aufgaben. Ein Worker soll seinen Nachweis an sein Parent melden; er entscheidet nicht stillschweigend über das Ergebnis der obersten Ebene.

```text
Owner
  ↓
Supervisor
  ├── Developer ── implementation result ──┐
  └── Reviewer  ── acceptance result ──────┤
                                           ↓
                              Supervisor incorporates results
                                           ↓
                                  Top-level completion
```

Ein **residierender Supervisor** kann verfügbar bleiben, während Worker Turns ausführen. Seine Residenz belegt einen Supervisor-Slot, auch wenn das Modell gerade keine Ausgabe erzeugt.

## Workflow

Ein **Workflow** ist der dauerhafte Koordinationsdatensatz für eine Mission oder delegierte Aufgabe. Er verfolgt, wer die Arbeit besitzt, welche logische Eingabe aktuell ist, ob Ergebnisse geliefert und eingearbeitet wurden und ob Abschluss oder eine Eigentümerentscheidung erforderlich ist.

Der Abschluss eines Provider-/Modell-Turns ist kein Workflow-Abschluss. Ein Supervisor kann einen Turn beenden, später ein Worker-Ergebnis erhalten und dieselbe offene Mission fortsetzen.

## Dauerhaftes Ergebnis

Ein **dauerhaftes Ergebnis** ist der strukturierte Abschlussnachweis, den delegierte Arbeit erzeugt. Es kann eine Zusammenfassung, geänderte Dateien, Prüfungen, Risiken und Blocker enthalten. ThreadCells speichert und liefert es, auch wenn das Worker-Terminal später stillgelegt wird.

Lieferung ist nicht dasselbe wie Einarbeitung. Der Supervisor bestätigt ein Ergebnis erst, nachdem er es tatsächlich verwendet oder bewertet hat.

## Eigentümer-Gate

Ein **Eigentümer-Gate** hält die autonome Fortsetzung an, weil die nächste Entscheidung den menschlichen Eigentümer erfordert — etwa Veröffentlichung, eine neue externe Vertrauensgrenze, eine irreversible destruktive Aktion oder eine zuvor nicht autorisierte Produktentscheidung.

Das Ende eines gewöhnlichen Modell-Turns oder ein schwieriger Implementierungsschritt ist kein Eigentümer-Gate.

## Vier Arten von Kapazität

ThreadCells trennt vier Kapazitätsgrenzen, weil sie verschiedene Teile des Rechners beschränken.

### Residierender Supervisor

Ein Supervisor oder Eigentümer der obersten Ebene bleibt verfügbar, um Callbacks zu erhalten und seinen Workflow fortzusetzen. Residenz unterscheidet sich von aktiver Modellausführung und delegierter Arbeitskapazität.

### Provider-Ausführung

Das Modell erzeugt aktiv einen Turn. Provider-Quoten, Prozessgrenzen und Netzwerkaktivität beschränken diese Kategorie.

### Arbeitskontext

Ein delegierter Coding-Kontext besitzt aktuell Arbeit. Er kann auch beim Warten auf einen Befehl oder Callback einen Worktree und Schreibberechtigung halten.

### Schwere Ausführung

Ein Build, Chromium-Lauf, große Testsuite oder ähnlich aufwendige Host-Aufgabe belegt einen Heavy-Slot. CPU-, Speicher- und I/O-Auslastung beschränken ihn.

Ein residierender Supervisor kann warten, ohne einen Provider-Slot zu verwenden, und ein delegierter Agent kann einen Arbeitskontext halten, ohne einen Provider- oder Heavy-Slot zu verwenden. Alle Grenzen zugleich anzuheben, kann den Host daher überlasten, ohne den Workflow zu beschleunigen. Siehe [Kapazitäts- und Ressourcenmodell](RESOURCE_MODEL.md).

## Ein vollständiges Beispiel

Ein Eigentümer startet für ein Repository einen Supervisor. Der Supervisor weist einem Entwickler einen verwalteten Worktree und Schreibberechtigung zu. Der Entwickler verwendet eine Provider-Ausführung, während er Code erzeugt, und danach einen Heavy-Slot für den Produktions-Build. Sein dauerhaftes Ergebnis kehrt zum Supervisor zurück. Ein Reviewer liest den Worktree und meldet eine blockierende Regression. Der Supervisor startet einen weiteren Turn, bittet den Entwickler um Korrektur, arbeitet beide Ergebnisse ein und schließt den Workflow explizit ab.

Terminal, Sitzung, Worktree, Workflow und Ergebnis sind getrennt, weil jedes eine andere Laufzeit und eine andere zu bewahrende Wahrheit hat.

Weiter: [Workflows und dauerhafte Ergebnisse](WORKFLOWS_AND_RESULTS.md) macht aus diesem Vokabular ein Betriebsvorgehen.
