---
slug: first-agent
source: docs/FIRST_AGENT.md
source_sha256: sha256:d677c6eeb4bf6847ed8361d5073920ff9682b13de198f994ac8c9fb0625672bb
---

# Dein erstes Projekt und dein erster Agent

Dieses Tutorial startet einen bewusst kleinen Agenten und zeigt, wo du dessen Terminal und Ergebnis findest. Führe zuerst den [Schnellstart](../QUICK_SETUP.md) aus und lasse den ThreadCells-Server laufen.

## 1. Sicheres Repository vorbereiten

Nutze für den ersten Lauf ein entbehrliches oder sauberes Git-Repository. ThreadCells identifiziert ein Projekt über sein Repository und kann daneben verwaltete Worktrees anlegen.

```bash
mkdir -p /tmp/threadcells-first-project
cd /tmp/threadcells-first-project
git init
printf '# First project\n' > README.md
git add README.md
git commit -m 'Create first project'
```

Erwartetes Ergebnis: `git status --short` gibt nichts aus. Ein sauberer Start macht die Änderungen des Agenten leicht prüfbar.

## 2. ThreadCells öffnen

Öffne `http://127.0.0.1:9889` auf dem Rechner, auf dem ThreadCells läuft. Wenn der Host remote ist, richte zuerst den in [Remotezugriff](REMOTE_ACCESS.md) beschriebenen SSH-Tunnel ein.

Öffne **Spawn Agent**, wähle das Repository als Projekt und einen installierten Provider. Ein als **CLI not installed** markierter Provider kann nicht starten; siehe [Provider](PROVIDERS.md), wenn dein erwarteter Provider nicht verfügbar ist.

Wähle für diese erste Aufgabe ein allgemeines Worker-Profil. Gib einen begrenzten Prompt ein, etwa:

```text
Add a short Usage section to README.md. Do not change any other file.
Run git diff --check and report the changed file.
```

Starte den Agenten.

## 3. Terminal beobachten

Der neue Agent erscheint unter **Agents**. Sein Terminal ist eine echte tmux-Sitzung, daher bleibt die native Ausgabe des Providers sichtbar und wiederverbindbar. ThreadCells zeichnet Projekt, Profil, Provider und Sitzungsidentität zu diesem Terminal auf.

Erwartetes Ergebnis: Der Status wechselt von starting zu running, Provider-Ausgabe erscheint und die Kapazität zeigt eine aktive Provider-Ausführung, während das Modell einen Turn erzeugt.

Wenn der Agent nie startet, prüfe die Verfügbarkeitsbezeichnung des Providers und die Kapazitätskarten. [Troubleshooting](TROUBLESHOOTING.md) enthält symptomorientierte Prüfungen.

## 4. Arbeit prüfen

Wenn der Agent endet, prüfe sein dauerhaftes Ergebnis und den Repository-Diff. Ein Terminal, das eine finale Provider-Nachricht erreicht, ist ein Nachweis, aber keine Erlaubnis zum Mergen, Veröffentlichen oder Bereitstellen.

```bash
cd /tmp/threadcells-first-project
git status --short
git diff -- README.md
```

Der mit einem Projekt verbundene Agent arbeitet im von ThreadCells angezeigten verwalteten Worktree-Pfad und nicht im registrierten Quellstamm. Nutze diesen Pfad zur Prüfung. Der Worktree hält nebenläufige Schreiber getrennt, bis ihre Commits bewusst zusammengeführt werden.

## 5. Supervision ausprobieren

Sobald ein einzelner Worker sinnvoll erscheint, starte ein Supervisor-Profil für eine weitere kleine Aufgabe. Bitte es, eine Implementierungsaufgabe und ein unabhängiges Review zuzuweisen. Die Beziehung sollte so aussehen:

```text
Owner
  └── Supervisor
        ├── Developer
        └── Reviewer
              ↓
        Durable results return to the supervisor
```

Der Supervisor bleibt dafür verantwortlich, diese Ergebnisse einzuarbeiten und den Workflow der obersten Ebene abzuschließen. Das Ende eines Workers schließt nicht die Mission des Supervisors.

## Nächste Schritte

- Lerne die in der UI verwendeten Bezeichnungen: [Kernkonzepte](CONCEPTS.md).
- Verstehe Profile, bevor du eigene erstellst: [Profile](PROFILES.md).
- Erfahre, wie Delegation das Ende eines Terminals überdauert: [Workflows und dauerhafte Ergebnisse](WORKFLOWS_AND_RESULTS.md).
- Dimensioniere den Rechner konservativ: [Kapazitäts- und Ressourcenmodell](RESOURCE_MODEL.md).
