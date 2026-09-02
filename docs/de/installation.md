---
slug: installation
source: docs/INSTALLATION.md
source_sha256: sha256:d5c33606b8b96ba951a941945b5ddc516900ef968a4b15e4e0d40ca40df19fd3
---

# Installation

Dieser Leitfaden erklärt den unterstützten lokalen Installationsweg und weshalb ThreadCells aus einem verifizierten Kandidaten installiert wird. Wenn du nur die Befehle möchtest, nutze den [Schnellstart](../QUICK_SETUP.md).

## Unterstützte Basis

Die aktuelle technische Vorschau unterstützt einen einzelnen Ubuntu/Debian-Linux-Host. ThreadCells erwartet ein vertrauenswürdiges Betreiberkonto und einen lokalen Git-Checkout. Andere Linux-Distributionen können funktionieren, sind aber nicht die unterstützte Basis; macOS und Windows können die Web UI remote aufrufen, sind aber keine unterstützten ThreadCells-Hosts.

## Voraussetzungen

Installiere oder prüfe:

- Python 3 und `venv`-Unterstützung;
- Git;
- tmux;
- Node.js und npm zum Bauen der paketierten Web UI;
- gängige POSIX-Dienstprogramme, die von den Release- und Dienstskripten verwendet werden;
- eine unterstützte Provider-CLI, installiert und authentifiziert für das Konto, das ThreadCells ausführt.

Prüfe die wichtigen Befehle:

```bash
python3 --version
git --version
tmux -V
node --version
npm --version
```

ThreadCells kann Adapter registrieren, deren CLIs fehlen. Das ist kein Installationsfehler; nur Provider, die du starten möchtest, müssen bereit sein. Siehe [Provider](PROVIDERS.md).

## Wo der Zustand liegt

Standardmäßig liegt der operative Zustand unter:

```text
~/.aws/cli-agent-orchestrator/
```

Der historische Verzeichnisname bleibt aus Kompatibilitätsgründen erhalten. Er kann die SQLite-Datenbank, Logs, verwaltete Worktrees, Agentenkontext, Anhänge, Provider-Artefakte und weiteren Laufzeitzustand enthalten. Setze `CAO_HOME_DIR` vor dem ersten Start, um einen anderen absoluten Ort zu wählen.

Die installierte Anwendung und ihr Laufzeitzustand sind verschieden:

- der **Kandidat/die Installation** enthält versionierten Code und statische Web-Assets;
- das **Zustands-Root** enthält die Datenbank, veränderliche Betreiber-Daten und optionale restriktive, ThreadCells-eigene Secret-Dateien wie das Telegram-Bot-Token;
- Provider-CLIs können ihre eigenen Zugangsdaten und Rollout-Historie an anderer Stelle speichern.

Erstelle vor dem Ersetzen einer Installation ein Backup des veränderlichen Zustands. Committe niemals Laufzeitzustand oder Provider-Zugangsdaten.

## Warum ein lokaler Kandidat?

Ein Kandidat ist ein Release-förmiges Verzeichnis, das aus genau einer Source-Revision gebaut wurde. Sein Manifest und seine Prüfsummen lassen dich verifizieren, was laufen wird, bevor eine Installation verändert wird. Staging und Promotion können den alten Kandidaten dann für ein Rollback bewahren.

Diese Disziplin ist bewusster als direkt aus einem sich ändernden Checkout auszuführen, verhindert aber, dass Web UI, Python-Code, Docs und Build-Identität stillschweigend aus unterschiedlichen Revisionen stammen.

## Kandidaten bauen

Im Repository-Stamm:

```bash
python3 scripts/build_local_candidate.py --output "$PWD/threadcells-candidate"
candidate="$PWD/threadcells-candidate/threadcells-0.3.4a0-local"
python3 scripts/verify_local_candidate.py --candidate "$candidate"
```

Erwartetes Ergebnis: Der Verifizierer akzeptiert Manifest, Prüfsummen, paketierte Dokumentation und Anwendungsdateien. Installiere keinen Kandidaten, der die Verifizierung nicht besteht.

## Vorschau und Installation

Wähle ein absolutes Präfix, das das Laufzeitkonto ausführen kann. Das Repository-lokale Präfix unten ist für eine Evaluierung praktisch:

```bash
"$candidate/scripts/install-threadcells.sh" --source "$candidate" --dry-run
"$candidate/scripts/install-threadcells.sh" --source "$candidate" --prefix "$PWD/.threadcells"
```

Der Probelauf kommt absichtlich zuerst. Prüfe Quelle und Ziel, führe dann die tatsächliche Installation aus.

## Installierte CLI verifizieren

```bash
"$PWD/.threadcells/venv/bin/threadcells" info
"$PWD/.threadcells/venv/bin/threadcells" doctor
"$PWD/.threadcells/venv/bin/threadcells" providers list
```

`doctor` ist schreibgeschützt. Behebe fehlende erforderliche Systemdienstprogramme. Die Provider-Ausgabe sollte zwischen einem bekannten Adapter und einer installierten und nutzbaren CLI unterscheiden.

## Lokal starten

```bash
"$PWD/.threadcells/venv/bin/threadcells-server" --host 127.0.0.1 --port 9889
```

In einer weiteren Shell:

```bash
curl -fsS http://127.0.0.1:9889/health
```

Öffne `http://127.0.0.1:9889`. Prüfe Settings → About und bestätige, dass Version und Revision dem verifizierten Kandidaten entsprechen.

Nutze für eine dauerhafte Installation den kanonischen Dienst-/Bereitstellungsmechanismus des Repositorys, der in [Deployment](DEPLOYMENT.md) beschrieben ist. Improvisiere keine öffentliche Bind-Adresse.

## Anfangsfehler

- **`python3 -m venv` schlägt fehl:** Installiere das Python-venv-Paket der Distribution.
- **`tmux` fehlt:** Installiere es vor dem Starten von Agenten; die Terminal-Persistenz hängt davon ab.
- **Web-Assets lassen sich nicht bauen:** Nutze die unterstützte Node/npm-Basis, installiere gesperrte Abhängigkeiten und baue den Kandidaten neu.
- **Provider meldet CLI not installed:** Installiere den kanonischen Befehl dieses Providers für den Laufzeitbenutzer oder wähle einen bereits bereiten Provider.
- **Provider ist installiert, aber nicht authentifiziert:** Durchlaufe den eigenen Login-Flow des Providers als Laufzeitbenutzer und wiederhole dann den Preflight.
- **Port 9889 ist belegt:** Stoppe den konkurrierenden lokalen Prozess oder wähle einen anderen Loopback-Port und nutze ihn konsistent.
- **Browser auf einem anderen Rechner kann nicht verbinden:** Das ist bei einem Loopback-Listener erwartet. Nutze [Remotezugriff](REMOTE_ACCESS.md).

## Grenzen der Entfernung

Das Entfernen eines Installationspräfixes entfernt nicht sicher den operativen Zustand, Provider-Zugangsdaten, Git-Repositories, Worktrees, Backups oder Dienstdefinitionen. Stoppe ThreadCells, erstelle ein verifiziertes Backup und identifiziere jede dieser Kategorien getrennt. Nutze Housekeeping für berechtigte Laufzeitartefakte; lösche das Zustands-Root nicht rekursiv als Abkürzung zur Deinstallation.

Als Nächstes: [Dein erstes Projekt und dein erster Agent](FIRST_AGENT.md).
