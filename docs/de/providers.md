---
slug: providers
source: docs/PROVIDERS.md
source_sha256: sha256:7f782daac9b50583042705af486afbdcc65d19ed545e0d8addd6e918808d7b0f
---

# Anbieter

Ein Anbieter ist die native CLI eines Coding-Agenten, die den Modellzug tatsächlich ausführt. ThreadCells stellt einen Adapter um diese CLI bereit, sodass Starts, Terminalstatus, Abbruch, Fähigkeitsmeldungen und verfügbare Nutzungstelemetrie eine gemeinsame Form haben.

## Drei unterschiedliche Fakten

Die Anbieteransichten trennen bewusst drei Fakten, die leicht verwechselt werden:

| Fakt | Bedeutung |
| --- | --- |
| Integrierter Adapter | Dieser ThreadCells-Build enthält geprüften Integrationscode für den Anbieter. |
| CLI installiert | Die erforderliche ausführbare Datei befindet sich im `PATH` des Laufzeitbenutzers. |
| Bereit | Der Preflight bewertet die installierte CLI als kompatibel und authentifiziert, oder die CLI kann den Authentifizierungsstatus nicht sicher offenlegen. |

Settings → Providers führt Adapter auf, auch solche, deren externer Befehl fehlt. Spawn Agent verwendet denselben kanonischen Preflight und deaktiviert nachweislich nicht verfügbare Anbieter.

Zum Beispiel ist **Integrierter Adapter · CLI nicht installiert** kein Widerspruch. Es bedeutet, dass ThreadCells den Anbieter bedienen kann, der Host aber derzeit dessen Programm nicht besitzt.

## Integrierte Anbieter

Der aktuelle Build registriert diese Adapter:

| Anbieter | Kanonischer Befehl |
| --- | --- |
| Amazon Q Developer | `q` |
| Claude Code | `claude` |
| Codex | `codex` |
| Gemini CLI | `gemini` |
| GitHub Copilot CLI | `copilot` |
| Kimi CLI | `kimi` |
| Kiro CLI | `kiro-cli` |
| OpenCode CLI | `opencode` |

Die Registrierung ist tatsächliche Produktunterstützung, keine Aufforderung, jede CLI zu installieren. Installieren Sie nur Anbieter, die Sie verwenden möchten, und folgen Sie deren offiziellen Anweisungen und Authentifizierungsablauf.

## Kompatibilitätsmatrix

Diese Matrix beschreibt den Adaptervertrag in diesem Release; sie verspricht nicht, dass jede externe CLI-Version oder jedes Konto auf einem bestimmten Host bereit ist. **Unterstützt** bedeutet, dass der Adapter die Fähigkeit direkt implementiert, **Bedingt** bedeutet, dass das Verhalten von Anbieter-CLI oder Sitzungsmodus abhängt, und **Nicht gemeldet** bedeutet, dass ThreadCells die Daten nicht erfindet.

| Anbieter | Start/Abbruch | Fortsetzen und Persistenz | Strukturierter Abschluss | Nutzungstelemetrie | Modell-/Reasoning-Steuerung | Bereitschaftsprüfung |
| --- | --- | --- | --- | --- | --- | --- |
| Codex | Unterstützt | Bedingtes Fortsetzen; unterstützte Persistenz | Bedingt | Unterstützte anbietereigene Tokenfelder | Unterstützt | Befehl, Version und Authentifizierung |
| Claude Code | Unterstützt | Bedingt | Bedingt | Bedingte anbietereigene Felder | Modellauswahl unterstützt; andere Steuerungen adapterabhängig | Befehl, Version und Authentifizierung |
| Amazon Q Developer | Unterstützt | Bedingt | Bedingt | Nicht gemeldet | Bedingt | Befehl und Version; Authentifizierung nicht geprüft |
| Gemini CLI | Unterstützt | Bedingt | Bedingt | Nicht gemeldet | Bedingt | Befehl und Version; Authentifizierung nicht geprüft |
| GitHub Copilot CLI | Unterstützt | Bedingt | Bedingt | Nicht gemeldet | Bedingt | Befehl und Version; Authentifizierung nicht geprüft |
| Kimi CLI | Unterstützt | Bedingt | Bedingt | Nicht gemeldet | Bedingt | Befehl und Version; Authentifizierung nicht geprüft |
| Kiro CLI | Unterstützt | Bedingt | Bedingt | Nicht gemeldet | Bedingt | Befehl und Version; Authentifizierung nicht geprüft |
| OpenCode CLI | Unterstützt | Bedingt | Bedingt | Nicht gemeldet | Bedingt | Befehl und Version; Authentifizierung nicht geprüft |

Codex ist der Referenz- und Release-Akzeptanzanbieter. Andere integrierte Adapter bleiben nutzbar, wenn ihr öffentlicher Preflight startbar ist; anbietereigenes Verhalten und Authentifizierung können jedoch variieren. Die Live-Fähigkeitsansicht in Settings ist für einen installierten Build maßgeblich.

## Verfügbarkeitsbezeichnungen

ThreadCells normalisiert den Preflight in fünf bedienerorientierte Zustände:

- **Bereit** (`INSTALLED_AND_READY`): installiert, kompatibel und authentifiziert, wenn die Authentifizierung geprüft werden kann.
- **Authentifizierung erforderlich** (`INSTALLED_NOT_AUTHENTICATED`): Der Befehl existiert, aber der Anbieter meldet, dass eine Anmeldung erforderlich ist.
- **Installiert, aber nicht funktionsfähig** (`INSTALLED_BUT_UNHEALTHY`): installiert, aber inkompatibel oder die Integritäts-/Versionsprüfung schlägt fehl.
- **CLI nicht installiert** (`NOT_INSTALLED`): Die kanonische ausführbare Datei wurde für den ThreadCells-Laufzeitbenutzer nicht gefunden.
- **Bereitschaft nicht geprüft** (`UNKNOWN`): installiert und nicht als nicht verfügbar nachgewiesen, aber der Anbieter kann Authentifizierung oder Bereitschaft nicht interaktiv sicher prüfen.

Ein nicht geprüfter Anbieter kann startbar bleiben, wenn sein Befehl installiert und kompatibel ist und nur der Authentifizierungsstatus unbekannt ist. Ein Start kann dennoch an einer anbietereigenen Anmeldeaufforderung scheitern; prüfen Sie sein Terminal und schließen Sie die Anbieter-Authentifizierung außerhalb von ThreadCells ab.

## Sicht des Laufzeitbenutzers prüfen

Die Verfügbarkeit eines Anbieters hängt vom Konto ab, das ThreadCells ausführt, nicht von Ihrer interaktiven Shell. Prüfen Sie zuerst über ThreadCells:

```bash
threadcells providers list
threadcells doctor
```

Prüfen Sie dann als Laufzeitbenutzer die erwartete Binärdatei und ihre Version. Für Codex:

```bash
command -v codex
codex --version
codex login status
```

Verwenden Sie den eigenen Statusbefehl des Anbieters, sofern vorhanden. Kopieren Sie keine persönlichen Anbieter-Anmeldeverzeichnisse in das Dienstkonto. Authentifizieren Sie dieses Konto über den unterstützten Ablauf des Anbieters.

## Settings und Spawn Agent

Settings → Providers ist die Inventar- und Diagnoseansicht. Sie zeigt Adapteridentität, Konfiguration, Fähigkeiten, Befehlspräsenz, Version, Authentifizierungsstatus und eine öffentlich sichere Preflight-Meldung.

Spawn Agent ist die Startansicht. Sie leitet ihren aktiviert/deaktiviert-Status aus demselben Preflight-Ergebnis ab. Wenn die beiden Ansichten nach einer Aktualisierung nicht übereinstimmen, behandeln Sie dies als Produktfehler, statt zu raten, welche Bezeichnung korrekt ist.

## Fähigkeiten sind anbieterspezifisch

Adapter deklarieren, ob Fortsetzen, strukturierter Abschluss, Modellauswahl, Reasoning-Steuerung, Sitzungspersistenz und Nutzung unterstützt, bedingt oder nicht unterstützt sind. ThreadCells simuliert keine nicht unterstützte Funktion.

Codex ist der Referenzadapter und liefert genaue kumulative Nutzungstelemetrie für unterstützte Tokenfelder. Claude Code unterstützt einige Nutzungs- und Abschlussfähigkeiten bedingt. Andere Adapter melden möglicherweise keine Nutzung; ihre Statistics-Felder bleiben nicht verfügbar, statt geschätzt zu werden.

## Konfiguration und Geheimnisse

Anbieterkonfiguration ist deklarativ. Sie kann einen installierten Adapter und adaptereigene Einstellungen auswählen, aber weder einen Binärpfad noch Shell-Befehl, Argumente, Umgebungsvariablen, Passwörter, Token oder Roh-Anmeldedaten importieren.

Undurchsichtige `secret_refs` können ein Geheimnis benennen, das von vertrauenswürdigem Adaptercode aufgelöst wird. Öffentliche Listen- und Exportantworten lassen ihre Werte aus oder schwärzen sie. Anbieter-Adapterpakete sind ausführbarer vertrauenswürdiger Code und müssen vom Hostoperator installiert und geprüft werden.

## Fehlerbehebung

### Anbieter zeigt CLI nicht installiert

Führen Sie `command -v` als Dienstkonto aus und vergleichen Sie dessen `PATH` mit Ihrer Shell. Installieren Sie den kanonischen Anbieterbefehl nur, wenn Sie ihn verwenden möchten, und starten Sie dann den Preflight neu oder aktualisieren Sie ihn.

### Installiert, aber Authentifizierung erforderlich

Führen Sie den offiziellen Anmeldeablauf des Anbieters als Laufzeitbenutzer aus. Der ThreadCells-Preflight authentifiziert nie in Ihrem Namen und aktiviert nie Einstellungen zur Umgehung von Berechtigungen.

### Bereitschaft nicht geprüft

Der Befehl existiert, hat jedoch keine sichere nicht interaktive Bereitschaftsprüfung. Prüfen Sie die Version und führen Sie einen kleinen anbietereigenen Test durch. Ein ThreadCells-Start kann die erste endgültige Bereitschaftsprüfung sein.

### Installiert, aber nicht funktionsfähig

Lesen Sie den sicheren Preflight-Grund. Häufige Ursachen sind ein Fehler des Versionsbefehls, eine bekanntermaßen inkompatible Version oder eine ausführbare Datei, die unerwartet beendet wird. Aktualisieren oder reparieren Sie die externe CLI; ändern Sie nicht die Adapterregistrierung, um sie als bereit zu markieren.

### Start schlägt trotz Bereit fehl

Öffnen Sie die Terminalausgabe. Anmeldedaten können nach dem Preflight abgelaufen sein, ein ausgewähltes Modell kann nicht verfügbar sein oder der Zustand des Anbieterdienstes kann sich geändert haben.

Details zu erweiterten Integrationen finden Sie unter [Erstellen von Anbieteradaptern](PROVIDER_ADAPTERS.md). Informationen dazu, was ein Startprofil steuert, finden Sie unter [Profile](PROFILES.md).
