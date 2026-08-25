---
slug: control-plane-artifacts
source: docs/CONTROL_PLANE_ARTIFACTS.md
source_sha256: sha256:bbb3ff2ed634050407d78c0fff79f097ecc2c8f29b783d422a52469c624fd8b7
---
# Control-Plane-Artefakte und KI-Workflow

ThreadCells veröffentlicht JSON-Schema-Draft-2020-12-Dokumente für ProfileDefinition V1, ProviderConfiguration V1, AdapterManifest V1 und AdapterCapabilities V1 unter `schemas/v1` im lokalen Kandidaten und unter `cli_agent_orchestrator/public_schemas/v1` im Wheel.

Verwenden Sie `threadcells profiles schema|example` oder `threadcells providers schema|example`, um ein Ausgangsdokument abzurufen. Validieren Sie es vor dem Import. Feldfehler sind stabile JSON-Pointer-Datensätze statt wiedergegebener Rohwerte. Importe über UI, CLI und API rufen denselben Dienst auf und erstellen unveränderliche Revisionen.

## KI-gestützter Artefakt-Workflow

1. Rufen Sie das relevante Schema, Beispiel und den sicheren Generierungs-Prompt von `/api/v1/profiles/ai-prompt` oder `/api/v1/providers/ai-prompt` ab.
2. Bitten Sie das Modell, genau ein JSON-Objekt zurückzugeben. Geben Sie keine Zugangsdaten, privaten Pfade, ausführbaren Befehle, Shell-Flags oder ungeprüften MCP-Befehle an.
3. Prüfen Sie Kennungen, Provider-Referenzen, Autorität, Tools, Timeouts und Anweisungen manuell.
4. Führen Sie `validate` aus; beheben Sie jedes JSON-Pointer-Problem.
5. Importieren Sie erst nach Operatorprüfung. Importe, die Platzhalter-Tools oder andere privilegierte Autorität erfordern, benötigen den separaten Pfad für vertrauenswürdige Operatoren.
6. Verwenden Sie vor dem Start die aufgelöste Vorschau und exportieren Sie nach dem Import, um das redigierte kanonische Artefakt zu bestätigen.

KI-generiertes JSON ist nicht vertrauenswürdige Eingabe. Ein plausibles Dokument installiert keinen Adaptercode, registriert keine MCP-Fähigkeit, verleiht keine Owner-Autorisierung und umgeht keine Repository-Richtlinie. Eingebaute Profile bleiben unveränderlich, und Exporte enthalten niemals Provider-Zugangsdaten oder Startberechtigungen.
