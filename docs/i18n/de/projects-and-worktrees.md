---
slug: projects-and-worktrees
source: docs/PROJECTS_AND_WORKTREES.md
source_sha256: sha256:330c4175df07b3a91dc7d9e0c88bbf91d6c3bb7b2bb76fd4340828260562dd02
---

# Projekte und verwaltete Worktrees

Ein ThreadCells-Projekt ist ein registriertes Git-Repository. Es gibt Sitzungen, Profilen, Statistics und Workflows einen stabilen Ort, zu dem sie gehören. ThreadCells macht ein Repository nicht allein durch dessen Registrierung sicher; beginne daher mit einem sauberen Status und verstehe die Schreibgrenze, die du gewährst.

## Projekt registrieren

Nutze die Projektauswahl in Spawn Agent, um ein bestehendes Repository auszuwählen, oder füge das Repository über das unterstützte Projekt-Steuerelement hinzu. Verwende einen absoluten kanonischen Pfad und bestätige, dass der ThreadCells-Laufzeitbenutzer ihn lesen kann.

Vor dem ersten Agenten:

```bash
git -C /path/to/project status --short
git -C /path/to/project worktree list
```

Erwartetes Ergebnis: Du kannst bereits vorhandene Änderungen und Worktrees von allem unterscheiden, was ThreadCells später anlegt. Bestehende nicht committete Arbeit gehört dem Betreiber; Agenten dürfen sie nicht verwerfen.

## Warum verwaltete Worktrees existieren

Zwei Schreiber in einem Checkout können die Änderungen des jeweils anderen überschreiben, selbst wenn ihre Prompts nichts miteinander zu tun haben. Ein verwalteter Git-Worktree gibt jedem begrenzten Schreiber seinen eigenen Checkout und Branch, während er die Objektdatenbank des Repositorys teilt.

```text
Canonical repository
  ├── operator checkout
  ├── supervisor context
  ├── developer worktree
  └── reviewer worktree or read-only context
```

ThreadCells zeichnet die Beziehung auf, statt temporäre Verzeichnisse als anonym zu behandeln. Das macht Bereinigung und Ergebniszuordnung sicherer.

## Schreibberechtigung

Nur der Kontext mit Schreibberechtigung sollte einen verwalteten Worktree verändern. Reviewer können Diffs prüfen und sichere Checks ausführen, ohne zu einem nicht nachverfolgten zweiten Schreiber zu werden.

Bearbeite einen verwalteten Worktree nicht manuell, während sein Agent aktiv ist. Falls ein Notfalleingriff nötig ist, stoppe oder koordiniere den Schreiber zuerst und dokumentiere, was sich geändert hat.

## Arbeit zurückführen

Ein dauerhaftes Ergebnis sollte geänderte Dateien und Prüfungen benennen, aber Git bleibt die Quelle der Wahrheit für Code. Prüfe Status, Diff und Commits des Worktrees, bevor du sie mit deinem normalen Repository-Prozess mergst oder cherry-pickst.

ThreadCells erteilt keine Veröffentlichungsberechtigung. Ein erfolgreiches Worker-Ergebnis autorisiert weder Push, Tagging, Bereitstellung noch Umschreiben der Historie.

## Bereinigung

Housekeeping entfernt einen verwalteten Worktree nur, wenn es beweisen kann, dass der Worktree nicht mehr durch ein aktives Terminal, einen Workflow, einen Schreib-Lease oder ein nicht eingearbeitetes Ergebnis geschützt ist. Unbekannte Eigentümerschaft schlägt geschlossen fehl.

Bei hoher Plattennutzung plane zuerst Housekeeping. Lösche ein Worktree-Verzeichnis nicht direkt; das kann Git-Metadaten und ThreadCells-Zustand inkonsistent zurücklassen.

## Häufige Fehler

- Mit einem schmutzigen Repository starten, ohne bestehende Änderungen zu erfassen.
- Zwei Agenten Schreibberechtigung für denselben Checkout geben.
- Einen Worktree als Sicherheits-Sandbox behandeln.
- Einen Worktree löschen, bevor Ergebnis und Commits eingearbeitet sind.
- Annehmen, dass ein verwalteter Branch automatisch gemergt oder gepusht wird.

Siehe [Workflows und dauerhafte Ergebnisse](WORKFLOWS_AND_RESULTS.md), um zu erfahren, wie Worktree-Ergebnisse einen Supervisor erreichen.
