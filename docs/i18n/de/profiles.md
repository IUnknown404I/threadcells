---
slug: profiles
source: docs/PROFILES.md
source_sha256: sha256:2bef7848e092db4dfc8e667bf98ea781cbae3ac77e80f02e25cc2500c7ad7776
---

# Profile

Ein Profil ist eine wiederverwendbare Startrichtlinie für einen Agenten. Es beantwortet: Welcher Anbieter und welches Modell sollen laufen, wie viel Reasoning soll es verwenden, welche Rolle und Anweisungen soll es erhalten und welche Fähigkeiten oder Autorität sind erlaubt?

Die meisten Nutzer sollten mit einem integrierten Profil beginnen und dessen aufgelöste Vorschau prüfen. Für die normale Nutzung müssen Sie kein Roh-JSON verfassen.

## Was ein Profil steuert

Ein aufgelöstes Profil kann Folgendes enthalten:

- Anbieterkonfiguration, Modell und Reasoning-Aufwand;
- eine Rolle wie Supervisor, Entwickler, Reviewer oder Spezialist;
- Anweisungen und Skill-Referenzen;
- erlaubte Tools und MCP-Fähigkeiten;
- Timeouts und Ausführungsverhalten;
- Einschränkungen der Writer- oder Owner-Autorität;
- ob es resident bleiben oder abgegrenzte Arbeit abschließen soll.

Modellleistung und Orchestrierungsrolle sind getrennt. Ein starkes Modell ist nicht automatisch ein Supervisor, und der Name eines Profils bestimmt nicht, wie Kapazität verrechnet wird.

## Integrierte Profile

ThreadCells liefert unveränderliche Profile für häufige Rollen, einschließlich alltäglicher und stärkerer Supervisors, Entwickler, Reviewer, Architektur- und Strategiearbeit, Frontend-/UI-Arbeit sowie eines eng gefassten, vom Owner autorisierten XHigh-Ausführers.

Beispiele:

- `supervisor_terra_medium`: der alltägliche Supervisor für gewöhnliche Zerlegung und Integration.
- `supervisor_sol_medium`: stärkere Orchestrierung für wichtige oder modulübergreifende Arbeit.
- `developer_terra_medium` und `developer_sol_medium`: Rollen für abgegrenzte Implementierung.
- `reviewer_sol_high`: unabhängige Überprüfung für riskante oder integrierte Änderungen.
- `critical_sol_xhigh_owner`: ein außergewöhnliches Owner-Ausführerprofil mit separater Autoritätsgrenze.

Integrierte Profile sind unveränderlich, damit eine vertraute ID nicht unbemerkt ihre Bedeutung ändern kann. Um eines anzupassen, duplizieren Sie es; die Kopie erhält eine benutzerdefinierte Identität.

## Ein Profil auswählen

Verwenden Sie das am wenigsten spezialisierte Profil, das die Aufgabe zuverlässig verantworten kann:

| Aufgabe | Ausgangspunkt |
| --- | --- |
| Kleine abgegrenzte Codeänderung | developer |
| Unabhängige Akzeptanzprüfung | reviewer |
| Mehrere abhängige Arbeitsstränge | supervisor |
| Architektur- oder Migrationsentwurf | Architektur-/Strategie-Spezialist |
| Produkt-UI-Implementierung | Frontend- oder UI/UX-Spezialist |
| Kritische Frontier-Owner-Ausführung | nur Owner-autorisierte XHigh |

Mehr Reasoning und weitergehende Autorität kosten Kapazität und erhöhen die Folgen. Sie sollten die Aufgabe widerspiegeln und nicht zum Standard werden.

## Aufgelöste Vorschau

Settings → Profiles zeigt sowohl das gespeicherte Artefakt als auch seine **aufgelöste Vorschau**. Verwenden Sie die Vorschau vor dem Start, um nach Anwendung von Standards und Referenzen den tatsächlichen Anbieter, das Modell, Reasoning, Rolle, Tools, Autorität, Timeouts und Anweisungen zu prüfen.

Neue Starts erfassen diese aufgelöste Revision atomar. Wenn Sie das benutzerdefinierte Profil später bearbeiten, entsteht eine weitere unveränderliche Revision; die historische Bedeutung einer bestehenden Sitzung wird nicht umgeschrieben.

Alte Sitzungen, die vor Revisions-Snapshots erstellt wurden, können `legacy/unavailable snapshot` zeigen. ThreadCells erfindet keine vergangene Konfiguration.

## Ein benutzerdefiniertes Profil erstellen

Der sicherste Weg ist:

1. Öffnen Sie Settings → Profiles.
2. Wählen Sie das nächstgelegene integrierte Profil.
3. Duplizieren Sie es.
4. Geben Sie der Kopie einen eindeutigen rollenbasierten Namen.
5. Ändern Sie die kleinstmögliche Zahl an Feldern.
6. Prüfen Sie die aufgelöste Vorschau.
7. Verwenden Sie es für einen abgegrenzten Teststart, bevor Sie die Arbeit ausweiten.

Benutzerdefinierte Änderungen erzeugen Revisionen. Ein von der Historie referenziertes Profil wird deaktiviert, statt destruktiv gelöscht zu werden.

## Spezialisierte und Owner-Autorität

Nicht vertrauenswürdige Importe können keine Owner-Ausführer-, XHigh-, uneingeschränkte oder `danger-full-access`-Autorität erzeugen. Ein authentifizierter Operator darf eine privilegierte benutzerdefinierte Revision nur über die geschützte Control Plane erstellen, und der Server benötigt beim Start weiterhin den anwendbaren Einmal-Owner-Grant.

Das integrierte Profil `critical_sol_xhigh_owner` kann in beiden Web-Startabläufen ausgewählt werden: beim Erstellen einer Sitzung oder beim Hinzufügen eines Agenten zu einer bestehenden Sitzung. Beide zeigen den Block zur außergewöhnlichen Autorität und verlangen eine ausdrückliche Bestätigung sowie die kurzlebige Operator-Entsperrung, bevor eine normale Start-Capability geprägt und verbraucht wird. Add Agent begrenzt diese Capability auf die bestehende Sitzung und das kanonisch geerbte/Projekt-Arbeitsverzeichnis. Der lokale CLI bietet dieselbe Autoritätsklasse über `--owner-xhigh` und interaktive Bestätigung. Keiner dieser Wege erzeugt eine wiederverwendbare API-Umgehung oder autorisiert andere Profile, Child-Terminals oder unabhängige Settings-Änderungen.

## Profile und Kapazität

Ein Top-Level-Supervisor oder eine Owner-Sitzung verbraucht Kapazität für residente Supervisors. Ein delegiertes Child verbraucht einen Work-Context-Slot. Anbieterausführung und schwere Ausführung werden separat nach Aktivität verrechnet, nicht nur, weil ein Profil `supervisor` oder `reviewer` im Namen trägt.

Lesen Sie [Kapazitäts- und Ressourcenmodell](RESOURCE_MODEL.md), bevor Sie die Parallelität für leistungsfähige Profile erhöhen.

## Erweiterter Import und Export

Die CLI stellt das aktuelle Schema und Beispiele bereit:

```bash
threadcells profiles schema
threadcells profiles example
threadcells profiles export
threadcells profiles validate /path/to/profile.json
threadcells profiles import /path/to/profile.json
```

Validieren Sie vor dem Import. Importe verwenden dieselbe Dienstvalidierung wie die UI und können keine ausführbaren MCP-Befehle einführen. Sie können auf installierte Anbieterkonfigurationen und registrierte Capability-Identifier verweisen.

Bearbeiten Sie keine Datenbankzeilen von Hand und kopieren Sie keine privaten Anweisungen, Dateisystempfade, Anmeldedaten oder internen Owner-Zustand in ein öffentliches Profilartefakt.

## Häufige Fehler

- Ein Profil allein nach seinem Modellnamen auswählen.
- Einem alltäglichen Worker Owner-Autorität geben.
- Ein benutzerdefiniertes Profil ohne Prüfung der aufgelösten Vorschau bearbeiten.
- Erwarten, dass eine Bearbeitung bereits laufende Sitzungen verändert.
- Rohwerte von Geheimnissen statt freigegebener Referenzen importieren.
- Ein Profil mit Anbieterinstallation verwechseln; die ausgewählte CLI muss weiterhin bereit sein.

Weiter geht es mit [Workflows und dauerhaften Ergebnissen](WORKFLOWS_AND_RESULTS.md), um zu erfahren, wie Supervisor- und Worker-Profile zusammenarbeiten.
