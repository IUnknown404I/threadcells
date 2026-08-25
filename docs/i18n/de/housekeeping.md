---
slug: housekeeping
source: docs/HOUSEKEEPING.md
source_sha256: sha256:a7a64e54c6aaea5593617556363e4741ecb97759caa08e9d8bd3ba47d879927c
---
# Housekeeping

Housekeeping gibt Laufzeitartefakte nur frei, wenn ThreadCells ihre Berechtigung nachweisen kann. Es ist bewusst konservativ: Eine unbekannte, nicht lesbare, aktive, referenzierte oder veränderte Ressource wird geschützt, statt als sicher löschbar vermutet zu werden.

![Live-ThreadCells-Housekeeping mit Festplattenzustand, geschützten Backups, Zeitplänen und Bereinigungsrichtlinie](/media/screenshots/threadcells-housekeeping.webp)

## Was bereinigt werden kann

Abhängig von Alter und Nachweisen zur Inhaberschaft kann ein Plan Folgendes enthalten:

- abgelaufene temporäre Pfade mit ThreadCells-Inhaberschaftsmarkern;
- alte Terminal-Anhänge ohne Referenz durch ein aktives Terminal;
- Logs, die für Komprimierung oder Aufbewahrungsbereinigung geeignet sind;
- verwaiste Browser-Prozessgruppen, die anhand exakter Prozessidentität erkannt wurden;
- Browser-Revisionen und Caches ohne Referenz durch aktive Metadaten;
- mit ThreadCells gekennzeichnete Container und Volumes, deren Owner tot und nicht referenziert ist;
- vertrauenswürdige Package-Caches mit messbarer Freigabeaktion;
- inaktive Kandidaten/Releases, die durch kanonische Staging-Metadaten repräsentiert werden.
- exakte Closed-Terminal-Laufzeit-Panes und Prozessnachfahren, deren dauerhaftes Terminal bereits geschlossen ist und deren Prozessidentität weiterhin übereinstimmt;
- zur Bereinigung ausstehende verwaltete Child-Worktrees, nachdem ihre dauerhafte Ergebnis-/Stilllegungsgrenze bestätigt und erneut validiert wurde.
- saubere inaktive verknüpfte Worktrees, deren HEAD bereits in einem ausdrücklich konfigurierten dauerhaften Git-Ref enthalten ist;
- markierte reproduzierbare Caches/generierte Nachweise, die sich direkt unter einem zugelassenen Cache-Root befinden, nachdem ihr Owner tot und die Aufbewahrungszeit abgelaufen ist.

Housekeeping löscht nicht blind Source-Repositories, aktive oder unbekannte Worktrees, laufende Terminals, offene Dateien, aktuelle/Rollback-Releases, gestagte Kandidaten oder Backups. Verknüpfte Worktrees werden über `git worktree remove` und `git worktree prune`, nie durch generisches rekursives Löschen, stillgelegt. Das Stilllegen einer geschlossenen Terminal-Laufzeit löscht weder ihre dauerhafte Sitzungs-, Agent-, Inbox-, Ergebnis- noch Workflow-Historie.

Ein reproduzierbares Verzeichnis muss ein unmittelbares Child eines konfigurierten Roots sein und `.threadcells-reproducible.json` enthalten:

```json
{"schema_version":1,"owner":"threadcells","kind":"cache","created_at":1790000000,"owner_pid":12345}
```

Unterstützte Arten sind `cache`, `generated`, `test_evidence` und `candidate`. Fehlende oder ungültige Marker, Symlinks, Pfadausbrüche, lebende Owner und Pfade innerhalb des Aufbewahrungsfensters bleiben geschützt.

Deployments können zusätzlich exakte ThreadCells-eigene Cache-Präfixe für rückwärtskompatible CI-Caches benennen. Diese Einträge bleiben auf unmittelbare Children des zugelassenen laufzeiteigenen Roots beschränkt und erfordern abgelaufene Aufbewahrung sowie dieselben Prüfungen der aktiven Prozesse und Identität zur Ausführungszeit. Nicht aufgeführte Präfixe, einschließlich mehrdeutiger Release-Kandidatenartefakte, bleiben geschützt.

## Erst planen, dann ausführen

Ein Dry-Run-Plan ist schreibgeschützt. Jeder Kandidat enthält Kategorie, kanonische Identität/Fingerprint, vorgeschlagene Aktion, Gesamtbytes, geschätzte freizugebende Bytes, soweit bekannt, Aufbewahrungsgrund und Schutzgrund. Klassenzusammenfassungen berichten getrennt über umsetzbare/freizugebende und bewahrte/geschützte Footprints, sodass eine große geschützte Klasse nicht als null Bytes verborgen wird.

```text
Inspect current state
      ↓
Build immutable plan and plan_id
      ↓ operator reviews
Execute exact plan_id
      ↓
Rebuild protected set under lock
      ↓
Revalidate each candidate immediately before action
      ↓
Report reclaimed, skipped, changed, and failed items
```

Ändert sich die Kandidatenmenge zwischen Planung und Ausführung, lehnt die manuelle Ausführung den veralteten Plan ab, ohne Ressourcen zu verändern. Jeder verbleibende Kandidat wird unmittelbar vor der Mutation erneut geprüft.

## Full Cleanup

Die letzte Gefahrenaktion in Settings → Housekeeping heißt **Delete all system files — Full Cleanup**. Sie verwendet dasselbe kanonische Inventar, denselben geschützten Satz, dieselbe unveränderliche Planidentität und dieselben Identitätsprüfungen zur Ausführungszeit wie normales Housekeeping, wendet jedoch die maximal nachweislich sichere Aufbewahrung an: reproduzierbare Caches, alte Logs, Build-/Kandidaten-/Temp-Artefakte, sicher stilllegbare Worktrees und jedes inaktive lokale Release können berechtigt werden. Unbekannte Eigentümerschaft oder mehrdeutige Autorität bleibt geschützt und wird in Plan und Bericht erklärt.

Full Cleanup ist nur verfügbar, wenn die Lebenszykluswahrheit des Backends beweist, dass jeder relevante Agent Ready, Exited oder in einem ausdrücklich gleichwertigen nicht ausführenden Zustand ist. Working, Processing, Starting, in die Warteschlange gestellte Dateisystemmutationen, Provider-Ausführung, Heavy-Arbeit, Laufzeitoperationen und unbekannte Lebenszyklusidentität blockieren die Ausführung. Der Server erwirbt die kanonischen Admission-Fences und prüft dieses Leerlauf-Gate unmittelbar vor der Mutation erneut; wird ein Agent nach der Vorschau aktiv, bricht der Lauf ab, ohne etwas zu löschen.

Die Vorschau ist schreibgeschützt. Die Ausführung erfordert die bestehende kurzlebige Operator-Entsperrung und das bestehende Bestätigungsmodal für dauerhafte Aktionen; es gibt kein Full-Cleanup-Passwort und kein clientseitig gespeichertes Secret. Die Anfrage bestätigt genau eine 64 Zeichen lange `plan_id` und enthält keinen beliebigen Pfad.

Jeder pfadnamensbasierte Full-Cleanup-Kandidat wird durch den eng begrenzten, socket-aktivierten Root-Helper ausgeführt, nachdem dieser den Operator eigenständig erneut authentifiziert, den exakten Plan neu erstellt, das Leerlauf-Gate nachgewiesen und geprüft hat, dass die Control Plane weiterhin alle Admission-Fences hält. Der Helper verschiebt jeden Kandidaten in eine Root-exklusive Quarantäne auf demselben Dateisystem, sperrt den erfassten Verzeichnisbaum gegen Mutationen durch den Runtime-Benutzer und löscht anschließend ausschließlich die verifizierten Identitäten über Verzeichnisdeskriptoren. Eine geänderte Identität wird aufbewahrt und gemeldet; die Ausführung fällt nie auf eine schwächere Pfadlöschung durch den Runtime-Benutzer zurück. Nicht dateisystemgebundene Lebenszyklusressourcen laufen weiterhin über ihre kanonischen transaktionalen Executor-Pfade.

Nach einem erfolgreichen Full Cleanup bleibt nur das aktive unveränderliche lokale ThreadCells-Release erhalten. Alle nachweislich inaktiven Rollback-/Recovery-Releases werden entfernt, Release-Metadaten atomar abgeglichen und lokaler Rollback als nicht verfügbar gemeldet. Das aktive Release und der aktive Pointer können nie Kandidaten sein. Ready-Agenten bleiben nutzbar: Ihre Worktrees, Writer-Autorität, ihr aktueller Kontext, ihre aktuelle Ausgabe und anderer Fortsetzungszustand bleiben geschützt. Die Historie beendeter Agenten kann in SQLite verbleiben, nachdem ihre sichere Dateisystemausgabe bereinigt wurde; Full Output meldet dann, dass die dauerhafte Ausgabe nicht verfügbar ist, statt fehlzuschlagen oder Text zu erfinden.

Backups, aktuelle Source-/Tool-Autorität, Provider-Zugangsdaten/-zustand, die SQLite-Datenbank und jede nicht nachgewiesene Ressource bleiben geschützt. Ein zweiter Full Cleanup erzeugt sicher einen nahezu leeren umsetzbaren Plan, abgesehen von neu berechtigten oder zuvor geschützten Elementen.

## Sicheres manuelles Beispiel

Fordern Sie in der installierten Umgebung zunächst JSON-Ausgabe an:

```bash
threadcells-housekeeping --dry-run --json
```

Prüfen Sie jeden Kandidaten und kopieren Sie die zurückgegebene `plan_id`. Führen Sie nur diesen geprüften Plan aus:

```bash
threadcells-housekeeping --plan-id PLAN_ID_FROM_DRY_RUN
```

Skripten Sie das Extrahieren und unmittelbare Ausführen von `plan_id` nicht, bevor Sie den Plan verstehen. Ein Dry-Run bedeutet nie Zustimmung zum Löschen.

## Philosophie der geschützten Menge

Die geschützte Menge kombiniert aktive Terminals und Worktrees, Writer-/Workflow-Inhaberschaft, aktuelle Source-/Laufzeitlinie, aktive und Rollback-Releases, gestagte Kandidaten, referenzierte Browser-Revisionen, offene Dateien, Startidentität laufender Prozesse und Terminalidentität, Container-Referenzmetadaten, Backups und gemeinsame Sperren.

Die Details sind für die Implementierung wichtig, die Operatorregel ist jedoch einfach: **Das Fehlen von Nachweisen ist kein Nachweis dafür, dass eine Ressource tot ist**. Kann Schutz nicht genau festgestellt werden, überspringt Housekeeping sie und meldet warum.

Geschützte Workflow-Autorität wird aus der dauerhaften Root-Terminal-Identität abgeleitet. Start und häufige Abgleiche brechen verwaiste Nicht-Recovery-Workflows ab, deren Root-Terminal nicht mehr existiert, und erzeugen dann die geschützte Menge neu. Bis diese Beziehung abgeglichen ist, schlägt die Worktree-Stilllegung für das gesamte unsichere Inventar geschlossen fehl.

## Zeitpläne

Settings → Housekeeping trennt Richtlinie, Zeitplan, Planung, Ausführung und Berichte. Unterstützte Zeitplanformen umfassen:

- ein häufiges Intervall von 15 Minuten bis 365 Tage, beispielsweise `6h`;
- einen wöchentlichen UTC-Zeitplan, beispielsweise `Sun 04:00 UTC`;
- Bereinigung bei Festplattendruck mit `on_red`.

Installierte Timer können alle 15 Minuten pollen, mit gestaffelter Erstaktivierung, sodass häufige und wöchentliche Prüfungen normalerweise nicht zusammenfallen. Dauerhafte Quittungen verhindern, dass eine Zeitplanklasse vor Fälligkeit zweimal läuft. Ein geplanter Poll, der die kanonische Housekeeping-Engine bereits aktiv vorfindet, endet erfolgreich als übersprungen und versucht es später erneut; manuelle Sperrkonkurrenz bleibt ein Fehler. Ein geplanter Lauf erstellt und führt seinen fälligen Plan unter einer Service-Sperre aus; er verwendet keinen von Menschen genehmigten manuellen Plan wieder.

Housekeeping-Änderungen und manuelle Ausführung sind durch [Operator-Autorisierung](OPERATOR_AUTHORIZATION.md) geschützt.

## Verhalten bei Festplattendruck

Bei YELLOW prüfen Sie das Wachstum und führen einen Dry-Plan aus. Bei RED kann ThreadCells einen recovery-sicheren Housekeeping-Heavy-Lease zulassen, obwohl gewöhnliche Heavy-Arbeit abgelehnt werden kann. Druckpläne ordnen die größten nachweislich sicheren Kandidaten zuerst an und zeigen dominante geschützte Klassen, die Bereinigung zählt jedoch weiterhin als eine Heavy-Ausführung und umgeht keinen Kandidatenschutz.

YELLOW ist ein Prüfzustand, keine Erlaubnis, freizugebende Bytes zu erfinden. Sind alle verbleibenden großen Klassen geschützt, schaffen Sie externe Kapazität oder dokumentieren Sie den geschützten Footprint, statt die Prädikate zu schwächen.

Die Freigabe des Package-Cache wird als unbekannt/null gemeldet, wenn der Befehl Bytes nicht nachweisen kann; ThreadCells bewirbt keine geschätzte Wiedergewinnung.

## Berichte und Teilausfälle

Der jüngste Bericht zeichnet Plan-/Laufidentität, Ressourcenzustand, Schätzungen, tatsächliche Ergebnisse, Ergebnisse je Kandidat und stabile Grundcodes auf. Das Scheitern eines Kandidaten schwächt weder den Schutz späterer Kandidaten noch verbirgt es unabhängige Erfolge.

Prüfen Sie nach einem Lauf Festplattendruck und übersprungene/fehlgeschlagene Einträge. Planen Sie vor einer weiteren Ausführung neu; verwenden Sie nach Zustandsänderungen keinen alten Plan wieder.

## Backups und Releases

Backups sind nur Inventar. Entscheidungen zur Aufbewahrung von Backup-Medien gehören zur Backup-Richtlinie des Operators, nicht zum automatischen Housekeeping.

Die Bereinigung von Releases und Kandidaten teilt die kanonische Staging-Sperre und erfordert vertrauenswürdige Referenzmetadaten. Normales Housekeeping schützt die aktiven und Rollback-Laufzeiten. Full Cleanup schützt nur das aktive Release und entfernt nach ausdrücklicher Operatorbestätigung absichtlich jedes nachweislich inaktive lokale Rollback-Release. Siehe [Upgrading](UPGRADING.md).

Installierte zeitgesteuerte Housekeeping-Dienste erhalten die enge Release-Maintenance-Gruppe, die zum Freigeben eines berechtigten unveränderlichen Releases nötig ist. Die zentrale Control Plane und gewöhnliche Agent-Prozesse erhalten sie nicht. Ein manueller/API-Lauf ohne diese Autorität überspringt das Löschen des Releases mit `RELEASE_ADMIN_GROUP_REQUIRED`, setzt unabhängige sichere Bereinigung fort und überlässt dem zeitgesteuerten Dienst, das Release später über dieselbe Plan-/Ausführungs-Engine freizugeben.

Der Schutz offener Pfade inventarisiert jeden Prozess, der dem konfigurierten ThreadCells-Laufzeitkonto gehört, unabhängig davon, welches autorisierte Konto einen manuellen Plan ausführt. Andere Host-Konten liegen außerhalb der Inhaberschaftsgrenze für entsorgbaren ThreadCells-Zustand; nicht lesbare private `/proc`-Einträge dieser Konten deaktivieren nicht die Bereinigung für den ganzen Host. Eine unbekannte Laufzeitidentität oder jede Unsicherheit bei der Prüfung eines Prozesses des Laufzeitkontos schlägt weiterhin geschlossen fehl.

## Häufige Fehler

- Ein Worktree-Verzeichnis direkt löschen, um Speicherplatz zurückzugewinnen.
- Eine geschätzte Bytezahl als garantierte Freigabe behandeln.
- Einen nicht geprüften Plan ausführen.
- Annehmen, eine gestoppte PID sei ausreichender Nachweis, dass eine Browser-/Prozessgruppe die alte ist.
- Erwarten, dass Housekeeping Backups löscht.
- Festplattenschwellen erhöhen, statt anhaltendes Wachstum zu beheben.
