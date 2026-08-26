---
slug: operations
source: docs/OPERATIONS.md
source_sha256: sha256:dc01111a81e6386ac3ffc8d2203e01f18caa175e4bb781ca451ac5f8d939c392
---
# Betrieb

Der routinemäßige Betrieb von ThreadCells besteht vor allem darin, vier Arten von Wahrheit zu bewahren: die Identität des laufenden Builds, Workflow-Inhaberschaft, verfügbare Kapazität und wiederherstellbaren Zustand.

## Tägliche Prüfungen

Verwenden Sie Home, Agents, Settings → General und Settings → Housekeeping, um Folgendes zu beantworten:

- Ist der Server gesund und läuft der erwartete Build?
- Sind Festplatte und Kapazität GREEN, YELLOW oder RED?
- Welche Supervisoren und Worker sind tatsächlich aktiv?
- Gibt es Ergebnisse, die zugestellt, aber nicht eingearbeitet wurden?
- Wartet ein Workflow auf eine Owner-Entscheidung?
- Falls Telegram aktiviert ist: Zeigt Settings → Telegram den erwarteten sicheren Verbindungs-/Testzustand?

Die Kommandozeilenansicht der Kapazität lautet:

```bash
threadcells-resource-status
```

Verwenden Sie für Service-Monitoring den lokalen Health-Endpunkt:

```bash
curl -fsS http://127.0.0.1:9889/health
```

## Starten und Stoppen

Führen Sie `threadcells-server` auf Loopback aus oder verwenden Sie den kanonisch installierten Dienst. Eine Browsertrennung beendet keine tmux-gestützten Agents. Ein unterstützter Serverneustart bewahrt rechtmäßig aktive Terminal-Laufzeiten und hydriert danach den dauerhaften Zustand offener Workflows und der Inbox-Zustellung wieder. Geschlossene Laufzeiten werden anhand exakter Terminal-/Prozessidentität stillgelegt; historische Sitzungs- und Ergebnisdatensätze hängen nicht davon ab, dass ein tmux-Pane aktiv bleibt.

Vor einem geplanten Neustart:

1. aktive Provider- und Heavy-Arbeit prüfen;
2. eine Mutation möglichst nicht unterbrechen;
3. aktuelle aktive und Rollback-Build-Identitäten festhalten;
4. Datenbank für ein Upgrade sichern und Integrität prüfen;
5. nur erforderliche ThreadCells-Dienste neu starten;
6. erneut verbinden und Workflows/Ergebnisse prüfen, bevor etwas wiederholt wird.

Verwenden Sie Graceful Exit für den Provider-Lebenszyklus. Das Beenden von tmux oder manuelle Löschen von Datenbankzeilen kann Terminalzustand und dauerhafte Workflow-Wahrheit auseinanderbringen.

## Sitzungs- und Workflow-Hygiene

Ein beendetes Child ist nicht sofort entbehrlich. Bestätigen Sie, dass sein dauerhaftes Ergebnis zugestellt, gelesen, eingearbeitet und bestätigt wurde. Legen Sie anschließend seine Laufzeitressourcen still, während die Historie erhalten bleibt.

**Add Agent** zielt auf die stabile Lebensdauer der ausgewählten Sitzung. Das Löschen historischer Sitzungen und beendeter Terminals zielt auf exakte dauerhafte Identitäten und wird abgelehnt, solange eine aktive Laufzeit, ein offener/Wiederherstellungs-Workflow, ein Writer-Lease, ein ausstehendes Ergebnis oder eine andere echte Lebenszyklusabhängigkeit besteht. Aufbewahrte Logs, geschützte Cleanup-Worktrees und Bereinigungsansprüche nach dem Beenden verhindern die logische Löschung nicht für sich allein: ThreadCells bewahrt die Ressourcenautorität, tombstoned die exakte Sitzung und macht Wiederholungen idempotent. Eine blockierte Löschung meldet den spezifischen Lebenszykluskonflikt statt eines generischen Fehlers wegen fehlender Ressource oder eines Serverfehlers.

Innerhalb einer Sitzung bewahren Home und Agents die dauerhafte Erstellungsreihenfolge des Backends in List- und Grid-Ansichten. Status, Provider, Profil, Aktivität, Polling, Wiederverbinden und Neustart sortieren Agenten nicht um; ein neu erstellter Agent wird hinter den älteren angefügt.

Ein Provider-Finale schließt keine offene Mission. Schließen Sie einen Top-Level-Workflow explizit erst ab, wenn alle owner-autorisierten Arbeiten beendet sind. Verwenden Sie ein Owner Gate nur für eine echte Entscheidungsgrenze.

## Kapazitätsänderungen

Settings → Orchestration Capacity übernimmt Änderungen ohne Serverneustart. Reduzierungen laufen aus; sie beenden keine aktiven Sitzungen. Ändern Sie jeweils nur eine Beschränkung und beobachten Sie, ob sich die beabsichtigte Warteschlange verbessert.

Kapazitätsmutationen erfordern eine entsperrte Operator-Sitzung und werden auditiert. Siehe [Kapazitäts- und Ressourcenmodell](RESOURCE_MODEL.md).

## Logs und Nachweise

Bewahren Sie genug Logs und Ergebnishistorie auf, um einen fehlgeschlagenen Lauf zu diagnostizieren, behandeln Sie Logs aber nicht als einzige dauerhafte Wahrheit. Datenbank, Workflow-Ergebnis, Git-Commit/Diff, Kandidatenmanifest und Testnachweise beantworten jeweils unterschiedliche Fragen.

Vermeiden Sie das Protokollieren von Prompts oder Werten mit Zugangsdaten. Öffentliche/API-Fehler von ThreadCells müssen sicher anzeigbar bleiben.

## Housekeeping

Housekeeping erfolgt immer zuerst als Plan. Prüfen Sie die Kandidatenliste des Dry-Runs und die Planidentität, führen Sie dann den exakten Plan ausdrücklich aus. Der Executor baut den aktuellen Schutz neu auf und validiert jeden Kandidaten vor einer Mutation erneut. Er kann nachweislich geschlossene Terminal-Laufzeiten und bestätigte, zur Bereinigung ausstehende Worktrees stilllegen, ohne dauerhafte Historie zu löschen.

Backups sind nur Inventar und werden niemals automatisch gelöscht. Unbekannte oder aktive Ressourcen bleiben geschützt. Full Cleanup ist eine separat bestätigte Operatoraktion, die nur ausgeführt wird, während alle Agenten untätig sind, die Fortsetzungsautorität von Ready-Agenten bewahrt und absichtlich jedes nachweislich inaktive lokale Release entfernt, sodass lokaler Rollback nicht mehr verfügbar ist. Siehe [Housekeeping](HOUSEKEEPING.md).

## Disziplin bei Produktionsänderungen

Für ein Upgrade:

1. einen unveränderlichen Kandidaten aus einem exakten Commit bauen und prüfen;
2. die aktuelle Installation als Rollback bewahren;
3. Datenbank sichern und Integrität prüfen;
4. über den kanonischen Deployment-Mechanismus stagen;
5. den exakt gestagten Kandidaten promoten;
6. nur erforderliche Dienste neu starten;
7. Health, UI, Provider-Preflight, Operator-Autorisierung, Workflows, Terminals und konfigurierte globale Telegram-Benachrichtigungen per Smoke-Test prüfen.

Veröffentlichen, pushen, taggen oder öffentliche Exposition ändern Sie nicht als beiläufigen Teil eines lokalen Deployments. Siehe [Upgrading](UPGRADING.md) und [Deployment](DEPLOYMENT.md).

## Wenn etwas nicht stimmt

Bewahren Sie Nachweise vor Bereinigung oder Wiederholung. Halten Sie Build-Identität, Sitzungs-/Terminal-/Workflow-IDs, sichere Fehlermeldung, relevantes Log-Fenster, Git-Status und aktuelle Kapazität fest. Nutzen Sie dann den symptomorientierten Leitfaden [Troubleshooting](TROUBLESHOOTING.md).
