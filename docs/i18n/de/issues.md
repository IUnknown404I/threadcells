---
slug: issues
source: docs/ISSUES.md
source_sha256: sha256:fa980f53f7ec42635a41273a8d82bdf2da52cab760ee5da675fbc6a00792cee4
---
# Richtlinie für öffentliche Issues

GitHub Issues sind der kuratierte öffentliche Backlog von ThreadCells, kein Transkript von Warnungen, Audits oder Release-Debugging.

## Eignung

Ein öffentliches Issue sollte normalerweise alle folgenden Bedingungen erfüllen:

- das Problem oder die Chance ist weiterhin ungelöst;
- es ist reproduzierbar oder durch dauerhafte Evidenz gestützt;
- es hat relevante Auswirkungen auf Benutzer, Projekt, Zuverlässigkeit, Dokumentation oder Wartbarkeit;
- öffentliche Nachverfolgung ist für Projekt oder Community nützlich und umsetzbar;
- öffentliche Offenlegung ist sicher;
- das erwartete Verhalten oder Ergebnis ist klar; und
- konkrete Akzeptanzkriterien können formuliert werden.

Dauerhafte technische Evidenz kann Reproduktionsschritte ersetzen, wenn eine deterministische Reproduktion unpraktisch ist.

Fragen, Fehlerbehebung und offene Gespräche gehören in [Discussions Q&A](https://github.com/IUnknown404I/threadcells/discussions/categories/q-a). Erkunden Sie frühe problem- und anwendungsfallorientierte Vorschläge in [Discussions Ideas](https://github.com/IUnknown404I/threadcells/discussions/categories/ideas). Verschieben Sie einen Befund oder Vorschlag erst dann in Issues, wenn er nach dieser Richtlinie bestätigt, konkret, öffentlich sicher und umsetzbar ist.

## Was nicht in öffentliche Issues gehört

Erstellen Sie ein öffentliches Issue nicht allein für:

- Verwaltung, die ausschließlich dem Repository- oder Kontoinhaber vorbehalten ist;
- Credential-Verwaltung oder private Infrastrukturarbeit;
- Credentials, Geheimnisse oder Sicherheitsdetails, deren Offenlegung unsicher ist;
- vorübergehendes CI-, Umgebungs-, Netzwerk- oder Runner-Rauschen;
- bereits gelöste Befunde;
- isolierte Laufzeitbezeichner ohne zugrunde liegende reproduzierbare Problemklasse;
- Warnungen, die sich sicher verhalten und keinen nachgewiesenen Defekt aufweisen;
- spekulative Verschönerungen ohne definiertes Problem und Ergebnis;
- vorübergehende Release- oder Debugging-Beobachtungen; oder
- nicht klassifizierte Notizen aus einem Audit oder einer Restschuldprüfung.

Maßnahmen, die nur dem Owner zustehen, gehören in den operativen Owner-Kanal des Repositories, nicht in den Mitwirkenden-Backlog. Ein Befund wird erst zu einem öffentlichen Issue, nachdem er die Eignungsprüfung bestanden hat.

## Inhalt eines Berichts

Verwenden Sie das passende Issue-Formular und geben Sie die nützlichen Teile dieser Struktur an:

1. **Problem / Kontext**
2. **Auswirkung**
3. **Aktuelles Verhalten**
4. **Erwartetes Verhalten**
5. **Reproduktion oder Evidenz**
6. **Akzeptanzkriterien**
7. **Nicht-Ziele**, sofern nützlich

Nehmen Sie Umgebungs- oder Versionsinformationen nur auf, wenn sie den Bericht beeinflussen. Schwärzen Sie Logs und Screenshots. Nehmen Sie niemals Geheimnisse, Credentials, personenbezogene Daten, private Nachrichten, unnötige private Pfade, Zustandsdatenbanken oder Terminaltranskripte auf.

Schwachstellen und sicherheitssensible Befunde müssen den privaten Weg in [SECURITY.md](../SECURITY.md) nutzen, nicht ein öffentliches Issue.

## Triage und Duplikate

Durchsuchen Sie offene und geschlossene Issues vor dem Einreichen. Maintainer verknüpfen Duplikate mit dem kanonischen Issue und schließen sie als Duplikat, statt Diskussion und Evidenz aufzuteilen.

Verwenden Sie die kleinste sinnvolle Menge an Labels. `bug`, `enhancement`, `documentation`, `accessibility` und `technical-debt` beschreiben die Arbeit; `duplicate` beschreibt die Triage. Maintainer können fehlende Evidenz anfordern, bevor sie entscheiden, ob ein Bericht geeignet ist.

Schließen Sie ein Issue, wenn die Akzeptanzkriterien erfüllt sind, wenn es ein kanonisches Issue dupliziert oder als nicht geplant mit einer knappen Begründung, wenn es außerhalb des Umfangs liegt, nicht umsetzbar gemacht werden kann oder die Projektverfolgung nicht mehr rechtfertigt. Bereits gelöste Berichte sollten auf die lösende Evidenz verweisen.

## Labels für Mitwirkende

Verwenden Sie `good first issue` nur für sichere, begrenzte, wenig mehrdeutige Arbeit mit genügend Kontext und Akzeptanzkriterien für einen neuen Mitwirkenden. Verwenden Sie `help wanted` nur, wenn externe Beiträge wirklich erwünscht sind und die Aufgabe ausreichend spezifiziert ist.

Kritische Sicherheits- oder Authentifizierungsgrenzen, Lebenszyklus- und Exactly-once-Verhalten, destruktive Sicherheit, Release-Autorität, Provider-Vertrauens- oder Remote-Code-Execution-Grenzen, Migrationen und Datenintegrität sind niemals automatisch Arbeit für Einsteiger.
