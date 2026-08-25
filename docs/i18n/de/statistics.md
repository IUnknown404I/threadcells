---
slug: statistics
source: docs/STATISTICS.md
source_sha256: sha256:ca9ce387ff845fb61aba3bc22c45084a825764e2ce7e156d6963e152e490de2b
---

# Statistiken und Anbieternutzung

Statistics fasst die Nutzung zusammen, die unterstützte Anbieter-CLIs tatsächlich ausgeben. Sie hilft zu beantworten, welche Sitzungen, Profile, Projekte und Anbieter Modell-Token verbraucht haben; sie ist kein Abrechnungsbuch und erfindet keine fehlenden Werte.

## Bedeutung der Zahlen

Für Codex zeichnet ThreadCells die kumulativen anbietereigenen Zähler auf, die in der Rollout-Telemetrie verfügbar sind:

- Eingabetoken;
- zwischengespeicherte Eingabetoken;
- Ausgabetoken;
- Reasoning-Token;
- Gesamttoken.

Zwischengespeicherte Eingaben bleiben separat sichtbar. Sie werden nicht stillschweigend erneut als frische Eingabe hinzugezählt. Eine Metrik, die der Anbieter nicht meldet, erscheint als **Nicht gemeldet**, nicht als irreführende Null.

Die Standardtabellen lassen Cache-Write-Token aus, da kein aktueller Adapter diese als aussagekräftige unterstützte Metrik bereitstellt. Die normalisierte API behält ein optionales Kompatibilitätsfeld, sodass ein zukünftiger Adapter wahrheitsgemäße Unterstützung ohne Datenbankmigration ergänzen kann.

Anbieter-Credit-, Preis- und Kosteninformationen werden nur angezeigt, wenn der Adapter einen unterstützten, maßgeblichen Wert liefert. ThreadCells schätzt keine Rechnungen aus Token-Gesamtsummen.

## Wann Nutzung erscheint

Die Nutzung wird gesammelt, während eine Live-Sitzung läuft, und dauerhaft gespeichert. Eine Sitzung muss nicht gelöscht, in den Ruhestand versetzt oder bereinigt werden, bevor sie zu Statistics beiträgt. Abgeschlossene, aber aufbewahrte Sitzungen zählen weiter.

Codex gibt kumulative Snapshots aus. ThreadCells setzt Checkpoints für diese Snapshots und aktualisiert denselben kanonischen Nutzungsdatensatz, sodass Abfragen, Neustart, Wiedergabe oder Fortsetzen dieselben Token nicht doppelt zählen.

## Die Seite lesen

Beginnen Sie mit den globalen Summen und verwenden Sie dann die Dimensionstabellen, um Nutzung nach Terminal, Sitzung, Projekt, Anbieter oder Profil zu finden. Die Summen verwenden dieselben normalisierten Datensätze wie die Detailansichten.

Eine beispielhafte Untersuchung:

1. Bemerken Sie einen Anstieg der globalen Ausgabetoken.
2. Öffnen Sie die Sitzungsdimension, um die beitragende Sitzung zu identifizieren.
3. Vergleichen Sie deren Projekt, Anbieter und Profil.
4. Öffnen Sie Agents, um das entsprechende Terminal und dauerhafte Ergebnis zu prüfen.

## Historische Daten

Upgrades können historische Nutzung nur wiederherstellen, wenn aufbewahrte anbietereigene Belege deterministisch einer ThreadCells-Sitzung zugeordnet werden können. Mehrdeutige oder fehlende Quelldaten bleiben unbekannt. Eine Reparatur ist idempotent: Ein erneuter Lauf darf keinen doppelten Datensatz erzeugen.

Legacy-Best-Effort-Terminal-Parsing kann in alten Datenbanken zur Provenienz verbleiben. Sobald ein exakter anbietereigener Datensatz existiert, ersetzt der exakte Datensatz die Legacy-Näherung in sichtbaren Summen.

## Fehlerbehebung

- **Eine Live-Sitzung fehlt:** Aktualisieren Sie die Seite, prüfen Sie, ob der Anbieter Nutzungserfassung unterstützt, und bestätigen Sie, dass der Anbieter-Rollout für das Dienstkonto lesbar bleibt.
- **Ein Feld zeigt Nicht gemeldet:** Der Anbieter hat diese Metrik nicht bereitgestellt. Interpretieren Sie sie nicht als Null.
- **Summen wirken nach einem Neustart doppelt:** Vergleichen Sie die Sitzungs- und Terminaldimensionen und bewahren Sie die Datenbank zur Diagnose auf; die Wiedergabe sollte einen Checkpoint aktualisieren, nicht eine zweite kumulative Summe einfügen.
- **Die Abrechnung weicht ab:** Verwenden Sie das eigene Abrechnungssystem des Anbieters als Abrechnungsautorität. ThreadCells meldet operative Telemetrie.

Für Kapazität — nicht Token-Abrechnung — siehe [Kapazitäts- und Ressourcenmodell](RESOURCE_MODEL.md).
