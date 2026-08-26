---
slug: provenance
source: docs/PROVENANCE.md
source_sha256: sha256:c57ec06cc83c35daec17670906144bc3460e4d341620e698af229eedc2b3eb00
---
# Herkunft

ThreadCells ist ein unabhängiger Downstream, der von AWS Labs CLI Agent Orchestrator abgeleitet wurde. Die beibehaltene Upstream-Lizenz und -Zuordnung befinden sich in den Dateien `LICENSE` und `NOTICE` des Quellkandidaten. Dieses Repository behält kompatible interne `cao`-Namen und -Befehle bei, wo sie vorhandenes Verhalten bewahren; öffentliche Produktoberflächen verwenden ThreadCells. Es wird keine AWS-Förderung oder -Unterstützung impliziert.

Jeder lokale Kandidat zeichnet seine commitete Quellrevision, sein Dateimanifest, SHA-256-Prüfsummen und Evidenz zu direkten Abhängigkeiten in `candidate-manifest.json`, `SHA256SUMS` und `sbom.cdx.json` auf. Die SBOM ist Evidenz für deklarierte/aufgelöste direkte Abhängigkeiten, keine Lizenzfreigabe, Schwachstellenbewertung oder Aussage zur Upstream-Parität. Eine öffentliche Distribution erfordert weiterhin die Genehmigung des Owners für das öffentliche Repository, den Sicherheitskontakt, die Markenherkunft und die Abhängigkeits-/Lizenzprüfung.
