---
slug: overview
source: docs/OVERVIEW.md
source_sha256: sha256:c4082c5da946df3936a8eb1c711b4701ba75b6dfa5000d82bc5f5416d8322f3e
---

# Hier beginnen: Was ist ThreadCells?

ThreadCells ist ein selbstgehostetes System, um mehrere Coding-Agenten als einen koordinierten Workflow auf einem Linux-Rechner auszuführen. Es gibt Agenten echte Terminals und Git-Worktrees, hält offene Missionen über Modell-Turns hinweg in Bewegung und lässt den Betreiber Kapazität, Schreibzugriff, geschützte Änderungen und das Endergebnis kontrollieren.

Wenn du Git, SSH und einen Kommandozeilen-Coding-Agenten verwenden kannst, hast du genug Grundlagen für den Einstieg. Du musst die interne Architektur von ThreadCells nicht verstehen, bevor du nützliche Arbeit startest.

## Warum verwenden?

Ein einzelnes Coding-Agent-Terminal ist leicht zu verstehen. Mehrere Terminals werden schwieriger: Zwei Agenten können denselben Branch bearbeiten, ein Build kann den Speicher erschöpfen, ein Supervisor kann verschwinden, bevor er ein Review einsammelt, und ein abgeschlossenes Terminal bedeutet nicht zwangsläufig, dass die angeforderte Mission abgeschlossen ist.

ThreadCells macht diese Beziehungen explizit und verwaltet seine eigene Betriebsumgebung. Es ist besonders nützlich, wenn du:

- lang laufende Agenten sichtbar und wiederverbindbar halten möchtest;
- parallelen Workern getrennte verwaltete Worktrees geben möchtest;
- einem Supervisor die Delegation von Implementierung und Review ermöglichen möchtest;
- Ergebnisse und Inbox-Nachrichten zurückerhalten möchtest, ohne sie manuell zwischen Terminals zu kopieren;
- eine logische Mission über Provider-Turns und normale Neustarts hinweg fortsetzen möchtest;
- Modell-Turns, aktive Arbeit und schwere Host-Aufgaben unabhängig begrenzen möchtest;
- Ergebnisse auch nach Ende eines Terminals erhalten möchtest;
- Host-Auslastung überwachen und entbehrliche ThreadCells-Laufzeit-, Log-, Cache-, Build- und Release-Artefakte sicher bereinigen möchtest;
- vor einem sensiblen oder mehrdeutigen Schritt eine Eigentümerentscheidung verlangen möchtest.

ThreadCells ist für einen vertrauenswürdigen Betreiber oder ein kleines vertrauenswürdiges Team auf einem selbst kontrollierten Host ausgelegt. Es ist keine Sandbox für feindliche Mandanten.

## Der grundlegende Ablauf

```text
Create a session and choose a project and agent
        ↓
Give the agent or supervisor the job
        ↓
Watch the coordinated workflow and host state
        ↓
ThreadCells continues eligible work across model turns
        ↓
Step in only for an explicit owner decision or final review
```

Der Agent läuft weiterhin über seine native Provider-CLI. ThreadCells koordiniert die umgebende Arbeit; es ersetzt den Provider nicht. Housekeeping schützt aktive Arbeit, dauerhaften Zustand, Backups und aktuelle/Wiederherstellungs-Releases und beansprucht nur Kandidaten zurück, deren Eigentümerschaft und Berechtigung nachweisbar sind. Das reduziert die manuelle Betreuung von ThreadCells-Artefakten, ist aber kein Versprechen, dass der physische Host niemals ausfallen kann.

## Eine sinnvolle erste Stunde

1. Befolge den [Schnellstart](../QUICK_SETUP.md), um einen lokalen Kandidaten zu bauen und zu verifizieren.
2. Nutze [Installation](INSTALLATION.md), wenn du die Begründung hinter jedem Schritt möchtest oder Hilfe bei Voraussetzungen brauchst.
3. Befolge [Dein erstes Projekt und dein erster Agent](FIRST_AGENT.md).
4. Lies [Kernkonzepte](CONCEPTS.md), nachdem du einen Agentenlauf gesehen hast.
5. Wähle vor der Nutzung eines weiteren Rechners eine sichere Methode aus [Remotezugriff](REMOTE_ACCESS.md).

Danach erläutern [Provider](PROVIDERS.md), [Profile](PROFILES.md) und [Workflows und dauerhafte Ergebnisse](WORKFLOWS_AND_RESULTS.md) das zentrale Betriebsmodell. [Betrieb](OPERATIONS.md) behandelt die Routineprüfungen für eine gesunde Installation.

## Was ThreadCells nicht tut

ThreadCells-Worktrees organisieren Schreibzugriffe; sie schotten einen Agenten nicht vom Host ab. ThreadCells fügt der Web UI auch keinen allgemeinen Login-Schutz hinzu. Halte den Server auf Loopback und nutze für Remotezugriff SSH-Weiterleitung oder einen authentifizierten Reverse Proxy.

Die aktuelle Version ist eine technische Vorschau. Lies [Sicherheitsmodell](SECURITY_MODEL.md) und [Einschränkungen](LIMITATIONS.md), bevor du wertvolle Repositories unter Agentenkontrolle stellst.

## Ersteller und Maintainer

ThreadCells wurde von [Subaev Ruslan](https://github.com/IUnknown404I) erstellt und wird von ihm gepflegt, mit Beiträgen der ThreadCells-Community. Es entstand aus dem praktischen Bedarf, mehrere native CLI-Coding-Agenten mit stärkerer operativer Kontrolle, dauerhaften Ergebnissen und Ressourcensicherheit zu betreiben.
