---
slug: workflows-and-results
source: docs/WORKFLOWS_AND_RESULTS.md
source_sha256: sha256:d6a1133dbc73417c1e5cfc8d6b96037535cf4b5552bd094cbb4efad93351fc0a
---

# Workflows und dauerhafte Ergebnisse

Ein Workflow steht für Arbeit, die über mehrere Modellzüge, Terminals oder delegierte Agenten hinweg kohärent bleiben muss. Er verhindert, dass die Abschlussmeldung eines Anbieters mit der Erledigung der größeren Mission verwechselt wird.

![Erweiterte Live-ThreadCells-Sitzung mit aktiven und abgeschlossenen Workflow-Teilnehmern](/media/screenshots/threadcells-session-workflow.webp)

## Top-Level- und delegierte Arbeit

Der **Top-Level-Workflow** gehört zum Agenten oder Supervisor, der für die Mission des Owners gestartet wurde. Ein **delegierter Workflow** gehört zu einem Child, dem eine abgegrenzte Aufgabe zugewiesen wurde.

```text
Top-level: "Prepare the release candidate"
  ├── Delegated: "Fix the statistics parser"
  ├── Delegated: "Review operator authorization"
  └── Owner gate: "Approve public publication"
```

Jeder Workflow hat seine eigene aktuelle logische Eingabe und seinen eigenen Abschlusszustand. Ein Worker kann seinen delegierten Workflow abschließen, während der Top-Level-Workflow offen bleibt.

## Assign und handoff

**Assign** startet unabhängige abgegrenzte Arbeit und lässt das Parent fortfahren. Das Ergebnis des Child wird später zugestellt. Das ist für parallele Untersuchung, Implementierung oder Prüfung nützlich.

**Handoff** überträgt eine abgegrenzte Aufgabe und wartet auf ihr validiertes Ergebnis, bevor das Parent fortfährt. Das ist nützlich, wenn der nächste Schritt des Parent unmittelbar von dieser Antwort abhängt.

Beide Formen bewahren Parent-/Child-Identität und ein dauerhaftes Ergebnis. Keine gibt einem Child weitergehende Owner-Autorität, als das Parent ausdrücklich delegiert hat.

Eine vorübergehende Zulassungsverweigerung vor dem Start, etwa wegen ausgeschöpfter Work-Context-Kapazität, wird als nicht zugelassen statt als ausgeführte Zuweisung erfasst. Derselbe logische Effekt kann erneut versucht werden, sobald Kapazität verfügbar ist; nachdem ein Child-Start zugelassen wurde oder sein Ergebnis ungewiss wird, bleibt der normale Duplikatschutz in Kraft.

## Ergebnislebenszyklus

```text
Task admitted
   ↓
Child works
   ↓
Structured result recorded
   ↓
Result delivered to parent
   ↓
Parent reads and incorporates it
   ↓
Parent acknowledges incorporation
   ↓
Eligible child resources can retire
```

Ein Ergebnis enthält normalerweise eine knappe Zusammenfassung, geänderte Dateien, durchgeführte Prüfungen, verbleibende Risiken und Blocker. Dies ist operativer Nachweis und kein Ersatz dafür, den Diff oder die Testausgabe zu prüfen.

Die Zustellung erfolgt mindestens einmal. Wenn das Parent vor der Bestätigung eines zugestellten Ergebnisses neu startet, kann ThreadCells es erneut zustellen. Das Parent sollte die unveränderliche Ergebnisidentität verwenden, um dieselbe Arbeit nicht zweimal zu übernehmen.

Inbox-Zustellung ist innerhalb eines Terminals FIFO und an genau den Workflow und logischen Zug gebunden, die sie erstellt haben. Ein ausstehender Transport ist Zustellzustand und keine Autorität, ein Payload oder Ergebnis in einen anderen Workflow zu verschieben. Wenn sein gebundener Workflow nicht mehr offen ist, terminalisiert ThreadCells diesen veralteten Transport und lässt neuere offene Owner-Arbeit fortfahren, ohne Payload-, Workflow-, Zustell-, Receipt- oder Effektidentität neu zu binden. Dieselbe Abstimmung läuft nach einem Neustart und ist idempotent.

## Abschluss des Anbieters gegenüber Workflow-Abschluss

Ein Anbieterzug endet, sobald das Modell die Kontrolle zurückgibt. Für die Mission kann noch zulässige Arbeit bestehen: ein weiterer Test, ein ausstehendes Child, ein Korrekturdurchlauf oder ein Deployment-Schritt.

ThreadCells hält daher einen Top-Level-Workflow offen, bis eines dieser ausdrücklichen Ergebnisse eintritt:

- die vom Owner autorisierte Mission ist abgeschlossen;
- ein Owner-Gate ist tatsächlich erforderlich;
- der Owner bricht ihn ab;
- ein echter nicht behebbarer Fehler erschöpft seinen begrenzten Wiederherstellungspfad.

Wiederholte gewöhnliche Anbieterabschlüsse verwenden dauerhafte Ein-Zug-Fortsetzung mit begrenztem Backoff. ThreadCells lässt den nächsten logischen Zug weiter zu, solange der Workflow offen ist. Wenn ein Anbieter direkt auf Ready übergeht, statt einen wiederholbaren Abschlussrahmen bereitzustellen, entprellt ThreadCells diesen Zustand dauerhaft über Neustarts hinweg und führt denselben offenen Workflow weiter; eine spätere Processing-Beobachtung verwirft einen vorübergehenden Ready-Kandidaten. Direkte Owner-Eingabe und dauerhafte Child-Ergebnisse setzen den Zähler für ausbleibenden Fortschritt zurück. Als Schutz vor kostenpflichtigen Schleifen versetzen 65 aufeinanderfolgende Abschlüsse ohne dauerhaften Fortschritt den Workflow in ein ausdrückliches, für den Owner sichtbares Gate. Der Abschluss des Anbieters wird nie zum Missionsabschluss, und gewöhnliche autonome Fortsetzung erfordert kein Aufwecken des Owners.

## Owner-Gates

Verwenden Sie ein Owner-Gate, wenn der nächste Schritt Autorität benötigt, die die Mission nicht erteilt hat. Gute Beispiele sind die Veröffentlichung in ein öffentliches Remote, das Freigeben eines neuen Netzwerkdienstes, das Bezahlen einer Ressource oder die Wahl zwischen wesentlich unterschiedlichen Produktsemantiken.

Verwenden Sie ein Owner-Gate nicht nur, weil die Arbeit langsam ist, ein Test fehlgeschlagen ist oder ein Anbieterzug geendet hat. Führen Sie zuerst alle unabhängige zulässige Arbeit fort.

## Wiederherstellung

Beim Neustart rekonstruiert ThreadCells die Workflow-Ownership aus dauerhaftem Zustand. Zugestellte, aber nicht bestätigte Ergebnisse bleiben verfügbar. Ein wartender Handoff kann gegen dasselbe Child fortgesetzt werden, statt ein Duplikat zu starten. Sobald ein neuerer logischer Zug für einen offenen Workflow zugelassen wird, wird eine ältere ausstehende Fortsetzung dauerhaft ersetzt und kann nach Kompaktierung oder Unterbrechung nicht später als unabhängige Arbeit wiedergegeben werden.

Wenn die Anbieter-/Modellausführung unterbrochen wird, nachdem ihre logische Eingabe zugelassen wurde, aber bevor die erforderliche Arbeit beendet ist, setzt ThreadCells über einen neuen dauerhaften Fortsetzungszug fort, statt das ursprüngliche Receipt wiederzugeben. Abgeschlossene Effekte bleiben eingezäunt, die Ownership der Anbieterausführung folgt dem fortgesetzten Zug und dasselbe unveränderliche Child-Ergebnis und dieselbe Abschlussbarriere bleiben zur Übernahme und genau-einmaligen Bestätigung verfügbar.

Direkter Abschluss, Fehlschlag, Abbruch, Owner-Gating, Child-Terminalisierung und zentraler Abbruch geschützter Workflows zäunen ausstehende Inbox-Transporte in derselben Datenbanktransaktion ein. Das verhindert, dass ein Terminalübergang gewöhnlichen Zustellzustand hinterlässt, der einen späteren Owner-Zug unterdrücken kann.

Ein Neustart des Dienstes mit demselben Build hält die anbieterseitige Kontrollverbindung kompatibel. Nachdem ein hochgestufter Build privilegierten Orchestrierungscode ändert, wird eine alte Verbindung eingezäunt, bevor sie einen Effekt erzeugen kann. Wenn die aktive Identität beim Neustart vorübergehend nicht verfügbar ist, wird die Operation ohne Effekt abgelehnt und nach Rückkehr des Dienstes wiederholt. Für Codex bindet ThreadCells die genaue Anbieterkonversation beim Start-Ready an das verwaltete Terminal und die Laufzeitgeneration und speichert diese Identität dann als Wiederverbindungsautorität. Andere offene Rollout-Dateien können dieses verwaltete Terminal nicht mehrdeutig machen. Eine fehlende, veraltete, falsche oder nicht beweisbare Identität schlägt vor Anbieter-Dispatch fehlgeschlossen fehl. Die dauerhafte Resume-Identität macht einen Dienstneustart selbst zwischen Exit und Relaunch sicher. Eingabetransport, Wiederverbindung und Ruhestand teilen sich einen dauerhaften Mutations-Claim pro Terminal, sodass Text nicht in die Shell-Lücke der Wiederverbindung eingefügt werden kann und eine veraltete Wiederverbindung nicht nach einem gewonnenen Ruhestand erneut starten kann. Der bereits dauerhafte logische Zug wird erneut versucht, nicht ersetzt.

Wenn ein Terminal verschwindet, prüfen Sie Workflow- und Ergebnisdatensätze vor einem erneuten Versuch. Ein neues Terminal darf eine Mutation, die das alte bereits abgeschlossen hat, nicht stillschweigend duplizieren.

## Konkretes Beispiel

1. Der Owner startet einen Supervisor, um ein Feature hinzuzufügen und es zu validieren.
2. Der Supervisor weist die Implementierung einem Entwickler zu und prüft weiter Tests.
3. Der Entwickler committet die Änderung und zeichnet ein Ergebnis auf.
4. ThreadCells stellt es zu; der Supervisor liest den Diff und bestätigt die Übernahme.
5. Der Supervisor weist einen unabhängigen Reviewer zu.
6. Der Reviewer findet eine blockierende Browserregression und zeichnet Nachweise auf.
7. Der Supervisor setzt denselben offenen Top-Level-Workflow fort, fordert eine Korrektur an und führt die Akzeptanz erneut aus.
8. Erst nach dem akzeptierten Build und autorisierten Deployment schließt der Supervisor den Workflow ausdrücklich ab.

In den Schritten 3, 4 und 6 sind einzelne Modellzüge beendet. Die Mission ist es nicht.

## Häufige Fehler

- Eine abschließende Terminalmeldung als Top-Level-Abschluss behandeln.
- Ein Ergebnis bestätigen, bevor es gelesen oder verwendet wird.
- Ein Ersatz-Child starten, ohne auf ein dauerhaftes vorheriges Ergebnis zu prüfen.
- Zwei Childs dasselbe Worktree ändern lassen.
- Ein Owner-Gate als allgemeine Pausentaste verwenden.

Siehe [Projekte und verwaltete Worktrees](PROJECTS_AND_WORKTREES.md) für Schreibisolation und [Kapazitäts- und Ressourcenmodell](RESOURCE_MODEL.md) für Zulassungsgrenzen.
