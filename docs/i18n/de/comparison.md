---
slug: comparison
source: docs/COMPARISON.md
source_sha256: sha256:43b9f58d4b33db88b6d6271456b89ce9008759f75a7bcf1dbb7ce02657a1ccee
---
# Wofür ThreadCells geeignet ist

ThreadCells richtet sich an Entwickler, die native Coding-Agent-CLIs bereits schätzen, aber einen klareren Weg brauchen, mehrere davon auf einem Rechner zu betreiben.

## Gegenüber getrennten Terminalfenstern

Getrennte tmux-Shells sind einfach, erfassen jedoch nicht automatisch Profil-/Provider-Identität, verwaltete Schreiberverantwortung, Kapazitätszulassung, Workflow-Abstammung, dauerhafte Kinderergebnisse oder Operator-Gates. ThreadCells behält die nativen Terminals bei und ergänzt diese Betriebsaufzeichnungen.

## Gegenüber einer gehosteten Agentenplattform

ThreadCells wird selbst gehostet und ist Loopback-first. Repositories, Terminals und die Koordinationsdatenbank bleiben auf dem Host des Operators. Im Gegenzug trägt der Operator Verantwortung für Installation, Providerauthentifizierung, Backups, Patchen, Ressourcendimensionierung und Schutz des Remotezugriffs.

## Gegenüber Containern oder Sicherheitssandboxes

ThreadCells ist keines von beiden. Verwaltete Worktrees und Autoritätsrichtlinien verringern Koordinationsfehler, isolieren jedoch keine nativen Provider-Prozesse vom Betriebssystemkonto.

## Gegenüber autonomen Softwarefabriken

ThreadCells betont begrenzte Delegation, inspizierbare Terminals, explizite Ergebnisse, Owner-Entscheidungen und evidenzgestützten Abschluss. Es verspricht nicht, dass Agents beliebige Software ohne Überprüfung ausliefern können.

ThreadCells ist ein unabhängiger Downstream von AWS Labs CLI Agent Orchestrator und behält kompatible interne `cao`-Bezeichner bei, wo sie bestehendes Verhalten bewahren. Es ist kein direkter Ersatz für nicht verwandte Agentenprodukte wie OpenHands oder Hermes. Wählen Sie es für lokale native CLI-Abläufe und dauerhafte Supervisor-/Worker-Steuerung, nicht für gehostete Mandantentrennung oder breite Plattformabstraktion.
