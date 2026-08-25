---
slug: localization
source: docs/LOCALIZATION.md
source_sha256: sha256:b3b5c92689f7e752f2aa7f508f22a9dba9007f076b16be56a7c10c02bcd548dc
---
# Lokalisierungsleitfaden

Englisch ist die kanonische Autorität für die öffentliche ThreadCells-Dokumentation, das Root-README und Produktbehauptungen. Eine Übersetzung darf die natürliche Formulierung verbessern, jedoch kein Verhalten auslassen oder erfinden, keine Sicherheitsgrenze schwächen, kein Limit ändern und keinen Befehl verändern.

## Gebietsschemamodell

Release-Gebietsschemata sind `en`, `ru`, `zh-CN`, `es`, `pt-BR`, `de` und `ja`. Kanonisches Markdown bleibt in der von `docs/DOCS_MANIFEST.json` benannten Quelle. Jedes nicht englische Dokument befindet sich unter `docs/i18n/LOCALE/SLUG.md` und enthält:

```yaml
---
slug: overview
source: docs/OVERVIEW.md
source_sha256: sha256:EXACT_ENGLISH_SOURCE_HASH
---
```

Slugs, Manifestreihenfolge und Navigationszugehörigkeit werden von allen Gebietsschemata geteilt. Erstellen Sie kein zweites gebietsschemaspezifisches Docs-Manifest oder keinen zweiten Renderer.

## Übersetzung aktualisieren

1. Aktualisieren und akzeptieren Sie zuerst das kanonische englische Dokument.
2. Übersetzen Sie jede Behauptung und Überschrift, ohne Code oder Bezeichner zu ändern.
3. Aktualisieren Sie `source_sha256` anhand der exakten kanonischen Quellbytes.
4. Führen Sie `python3 scripts/validate_localizations.py` aus.
5. Bauen Sie die Website und prüfen Sie die betroffenen Routen bei Desktop-, Tablet- und Mobilbreiten.

Der Validator weist fehlende, veraltete, unbekannte, doppelte oder nicht passende übersetzte Slugs zurück. Ein unterstütztes Gebietsschema darf nicht stillschweigend eine alte Übersetzung veröffentlichen, nachdem sich Englisch geändert hat.

## Gebietsschema hinzufügen

Fügen Sie das Gebietsschema einmal in `website/lib/locales.ts` hinzu, stellen Sie vollständige Landing-/UI-Metadaten bereit, fügen Sie für jeden Manifest-Slug ein übersetztes Dokument sowie dessen lokalisiertes README hinzu und erweitern Sie die deterministischen Routen-/Browserprüfungen. Behalten Sie beim Sprachwechsel denselben öffentlichen Slug bei.

Das Hinzufügen eines künftigen Gebietsschemas wie `fr` oder `ko` sollte eine begrenzte Inhaltsänderung sein. Es darf keine weitere Anwendungs-, Manifest- oder Docs-Architektur erfordern.

## Technischer Text

Behalten Sie Folgendes exakt bei, sofern die kanonische englische Quelle es nicht ändert:

- umschlossene Codeblöcke und Shell-Befehle;
- Inline-Codebezeichner;
- API-Pfade, Konfigurationsschlüssel, Umgebungsvariablen, Reason-Codes, Profil-/Provider-IDs, Paketnamen und Dateipfade;
- Produkt- und Providernamen wie ThreadCells, Codex, Claude Code, Git, Git worktree und tmux;
- Markdown-Linkziele und Medienpfade.

Übersetzen Sie die Erklärungen um diese Werte herum natürlich. Vermeiden Sie wörtliche Lehnübersetzungen, die Entwickleranleitungen schwerer lesbar machen.

## README-Dateien

`README.md` ist kanonisches Englisch. Jedes lokalisierte README folgt derselben Abschnittsstruktur, verweist auf dieselbe Evidenz und beginnt mit der kompakten Sprachauswahl für sieben Sprachen. Heben Sie die aktuelle Sprache **fett** hervor und verwenden Sie für die anderen sechs repository-relative Links.

## Visuelle Abnahme

Übersetzungen benötigen keine identischen Zeilenumbrüche oder Abschnittshöhen. Sie müssen Hierarchie, lesbare Typografie, funktionierende CTAs, Medien, Tabellen, Codeblöcke, Kopf-/Fußzeilenverhalten und keinen horizontalen Überlauf bewahren. Achten Sie besonders auf deutsche Ausdehnung, russischen Zeilenumbruch, spanische und portugiesische Navigation sowie chinesischen/japanischen Zeilenumbruch.

Eine semantische Prüfung durch einen fließend lesenden Entwicklerleser bleibt erforderlich. Bestehende Markdown-, Hash-, Routen- und Browserprüfungen belegen strukturelle Aktualität; sie belegen nicht die Übersetzungsqualität.
