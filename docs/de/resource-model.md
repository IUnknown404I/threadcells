---
slug: resource-model
source: docs/RESOURCE_MODEL.md
source_sha256: sha256:50fdcf87c80a11bbd1e8d9c210e584f2640a388c71882bd4b2bf06af0b27f725
---

# Kapazitäts- und Ressourcenmodell

ThreadCells trennt Kapazität, weil Coding-Agent-Arbeit zu verschiedenen Zeiten unterschiedliche Teile eines Hosts belasten kann. Ein Modellzug verbraucht Anbieterkapazität; ein zugewiesener Coding-Kontext kann aktiv bleiben, während das Modell inaktiv ist; ein Build kann die Maschine sättigen, nachdem die Modellausgabe beendet ist.

![Live-Orchestrierungskapazität mit unabhängigen Grenzen für residente, Anbieter-, Work- und Heavy-Kapazität](/media/screenshots/threadcells-capacity.webp)

Alle Zahlen gemeinsam zu erhöhen ist normalerweise nicht schneller. Dadurch können Kontingentkonflikte beim Modell, Speicherlast, Datenträger-Churn und mehrere teure Builds entstehen, die um dieselbe CPU konkurrieren.

## Die vier Grenzen

### Residente Supervisors

Ein residenter Slot hält eine Top-Level-Supervisor- oder Owner-Sitzung, die über Delegierung und Callbacks hinweg verfügbar bleiben muss. Er verbraucht Residenz, selbst wenn er auf ein Worker-Ergebnis wartet.

Dies ist getrennt, weil das Beenden eines scheinbar inaktiven Supervisors den Kontext verlieren kann, der für die Integration der Mission verantwortlich ist.

### Anbieterausführungen

Ein Provider-Execution-Slot wird verwendet, während ein Modell/Anbieter aktiv einen Zug erzeugt. Die relevanten Einschränkungen sind Anbieterparallelität, Netzwerkaktivität, Prozessanzahl und manchmal Speicher.

Ein an einem Prompt wartender Agent benötigt keinen Provider-Execution-Slot.

### Work-Kontexte

Ein Work-Slot repräsentiert einen delegierten Worker oder Reviewer, der derzeit einen abgegrenzten Kontext besitzt. Er kann ein verwaltetes Worktree und Writer-Autorität halten, während er zwischen Modellzügen wartet.

Eine Top-Level-Sitzungswurzel verbraucht residente Kapazität, nicht Work-Kapazität. Ein residentes delegiertes Child verbraucht Work-Kapazität.

### Schwere Ausführungen

Ein Heavy-Slot ist für hostintensive Arbeit wie einen Produktionsbuild, Chromium-Lauf, große Testsuite oder repositoryweiten Scan. Heavy-Zulassung schützt CPU-, Speicher- und I/O-Reserven.

Verwenden Sie für qualifizierende Befehle den kanonischen Heavy-Runner. Gewöhnliche kleine Tests und Dateiprüfung benötigen keinen Heavy-Slot.

## Der Standardausgangspunkt

Die paketierte Konfiguration `5 resident / 3 provider / 2 Work / 1 Heavy` ist ein konservativer Ausgangspunkt für kleine Hosts, kein Benchmark und keine feste Produktgrenze.

Zulässige Bereiche sind 2–50 residente Slots und 1–50 für jede andere Grenze. Werte werden in der Laufzeitdatenbank gespeichert und ohne Serverneustart wirksam.

## Was soll ich auf meiner Maschine einstellen?

Beginnen Sie konservativ, beobachten Sie Speicher-/Datenträgerlast und Warteschlangen und ändern Sie dann jeweils nur eine Grenze. Diese Beispiele veranschaulichen die Form; sie sind keine Leistungsgarantien.

| Host-Beispiel | Residente | Anbieter | Work | Heavy | Begründung |
| --- | ---: | ---: | ---: | ---: | --- |
| Kleiner VPS | 2 | 1 | 1 | 1 | Ein Supervisor und ein abgegrenztes Child; teure Arbeit serialisieren. |
| Entwicklerarbeitsplatz | 5 | 3 | 2 | 1 | Nützliche parallele Modellzüge bei serialisierten Builds. |
| Größerer gemeinsam genutzter Host | 8 | 5 | 4 | 2 | Mehr residente Missionen und Worker mit gemessener Reserve für zwei schwere Aufgaben. |

Fragen Sie vor dem Erhöhen einer Grenze, welche Warteschlange den Fortschritt tatsächlich blockiert:

- Provider voll, CPU aber inaktiv: Erwägen Sie einen weiteren Provider-Slot, wenn Kontingente dies erlauben.
- Work voll bei inaktiver Anbieterkapazität: Setzen Sie abgeschlossene bestätigte Childs in den Ruhestand oder erhöhen Sie Work vorsichtig.
- Heavy voll während Builds: Ein zweiter Heavy-Slot hilft nur, wenn CPU, RAM und Datenträger parallele Builds unterstützen können.
- Resident voll: Schließen Sie abgeschlossene Top-Level-Sitzungen; kaschieren Sie nicht aufgegebene Supervisors, indem Sie nur Resident erhöhen.

## Speicher- und Datenträgerlast

ThreadCells beobachtet Hostlast zusammen mit konfigurierten Zählwerten. Viele native CLIs, tmux-Panes, Browserprozesse, Worktrees, Build-Caches und Logs können den kurzen Modellzug überleben, der sie erzeugt hat.

Der Datenträgerstatus verwendet exakte Schwellen:

- **GREEN:** unter 70 % belegt.
- **YELLOW:** 70 % bis unter 85 %.
- **RED:** 85 % bis unter 92 %.
- **CRITICAL:** 92 % oder mehr. Die aggregierte Zulassung bleibt RED und enthält den
  Grund `DISK_CRITICAL`, während die datenträgerspezifische Projektion CRITICAL meldet.

YELLOW fordert dazu auf, Wachstum zu prüfen und Housekeeping zu planen. RED kann riskante neue Arbeit ablehnen und wiederherstellungssichere Bereinigung zulassen. Unbekannter Zustand schlägt fehlgeschlossen fehl; ThreadCells nimmt nicht an, dass ein unlesbares Dateisystem gesund ist.

Eine ausdrückliche Workflow-Composer-Entscheidung für einen bereits residenten Workflow im Owner-Gate ist auch unter ausschließlich datenträgerbedingtem RED ein enger Wiederherstellungspfad: Sie belegt normale Anbieter-Kapazität, erstellt aber keinen Work-Kontext. RED durch Speicher, PSI, unbekannte oder gemischte Gründe wird weiterhin fehlgeschlossen abgelehnt; der dauerhafte Zug zeigt stattdessen einen Wartegrund für die Ressourcenwiederherstellung an, ohne Transportwiederholungen zu verbrauchen.

## Draining nach einer Verringerung

Das Absenken einer Grenze beendet nie aktive Arbeit. Wenn die aktuelle Nutzung über dem neuen Wert liegt, wird diese Kategorie **draining** und verweigert neue Zulassungen, bis die aktive Nutzung innerhalb der Grenze liegt.

Beispiel: Das Ändern von Work von 4 auf 2 lässt bei drei aktiven Childs alle drei weiterlaufen. Wenn Childs abschließen und in den Ruhestand gehen, wird kein Ersatz zugelassen, bis die Nutzung 2 oder weniger erreicht.

Heavy-Inventar zählt aktive Slots mit höherer Nummer nach einer Verringerung weiter, sodass eine Grenzänderung keinen teuren Prozess verbergen kann.

## Wann Kapazität freigegeben wird

- Anbieterkapazität wird freigegeben, wenn der aktive Modellzug endet.
- Heavy-Kapazität wird freigegeben, wenn der registrierte Heavy-Befehl endet.
- Work-Kapazität wird erst freigegeben, nachdem der delegierte Kontext sicher in den Ruhestand versetzt wurde.
- Residente Kapazität wird freigegeben, wenn die Top-Level-Supervisor-/Owner-Sitzung schließt.

Das Ergebnis eines abgeschlossenen Child muss aufgezeichnet, zugestellt, übernommen und bestätigt werden, bevor Ressourcen in den Ruhestand gehen. Die Historie bleibt bestehen, nachdem Laufzeitkapazität freigegeben wurde.

Die Zulassung wird an Start- und Fortsetzungsgrenzen erneut geprüft. Ein wartender Anbieterzug startet, wenn ein Provider-Slot verfügbar wird. Anbieterabschluss gibt nur Anbieterausführungskapazität frei; er schließt keinen offenen Workflow, verwirft seinen Callback nicht und gibt keinen delegierten Work-Kontext frei, der noch dauerhafte Arbeit besitzt.

## Konfigurieren und beobachten

Verwenden Sie Settings → Orchestration Capacity für aktuelle Nutzung, Grenzen, Empfehlungen und Draining-Zustand. Kapazitätsänderungen sind durch [Operatorautorisierung](OPERATOR_AUTHORIZATION.md) geschützt und werden auditiert.

Die Befehlszeilen-Statusansicht lautet:

```bash
threadcells-resource-status
```

Prüfen Sie nach einer Änderung, ob UI und CLI übereinstimmen. Eine Grenze ist eine Zulassungssteuerung, kein Durchsatzversprechen oder Workload-Sandbox.

## Häufige Fehler

- Jede Grenze erhöhen, weil ein Build langsam ist.
- Ein inaktives Worktree als Anbieterausführung zählen.
- Residente Supervisors bei der Dimensionierung lang laufender Missionen vergessen.
- Eine Grenze verringern und erwarten, dass aktive Aufgaben beendet werden.
- GREEN-Kapazität als Beweis verfügbarer Anbieterquoten behandeln.
- Laufzeitdateien löschen, um einen Slot freizugeben, statt den besitzenden Workflow sicher in den Ruhestand zu versetzen.

Siehe [Housekeeping](HOUSEKEEPING.md) für Datenträgerwiederherstellung und [Workflows und dauerhafte Ergebnisse](WORKFLOWS_AND_RESULTS.md) für sicheren Child-Ruhestand.
