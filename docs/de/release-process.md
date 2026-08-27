---
slug: release-process
source: docs/RELEASE_PROCESS.md
source_sha256: sha256:66966038765eab40e9e11f4cbd81cc126fc199a63e95390194c3d88dbdd598b6
---
# Release-Prozess

Erstellen Sie mit `scripts/build_local_candidate.py --output <new-directory>` aus einem sauberen, committeten Baum einen isolierten lokalen Kandidaten. Er paketiert die generierten Docs/UI und ein lokales Wheel. Prüfen Sie `SHA256SUMS`, kontrollieren Sie `candidate-manifest.json`, `sbom.cdx.json` und `EVIDENCE.md`, und führen Sie anschließend die dokumentierte saubere Installation mit einem neuen Präfix durch. Die Veröffentlichung eines Tags, Remote-Branches, Pakets, Images oder öffentlichen Releases ist niemals eine gewöhnliche Implementierungsmaßnahme.

## Release-Checkliste

1. Schließen Sie die Implementierung und eine unabhängige integrierte Überprüfung ab.
2. Führen Sie gezielte Tests sowie einen aussagekräftigen Produktionsbuild-/Browser-Umriss aus.
3. Führen Sie `git diff --check` und die Prüfung der öffentlichen Oberfläche aus.
4. Committen Sie den exakt akzeptierten Baum.
5. Erstellen Sie den Kandidaten aus diesem Commit, niemals aus einem Worktree mit nicht committeten Änderungen.
6. Prüfen Sie Manifest, Prüfsummen, SBOM, Build-Identität, Docs-Routen und die saubere Installation.
7. Bewahren Sie vor der lokalen Promotion die vorherige Laufzeitumgebung und ein Datenbank-Backup auf.
8. Behandeln Sie jeden öffentlichen Push, jedes Tag, Paket, Image oder Release als eigenständige, vom Eigentümer genehmigte Maßnahme.

Release-Evidenz belegt, was getestet und paketiert wurde; sie genehmigt nicht selbst die Veröffentlichung und bescheinigt auch nicht jede Lizenz- oder Sicherheitseigenschaft von Abhängigkeiten.

## OCI-Release-Distribution

Genehmigte veröffentlichte Alpha-Releases verfügen außerdem über ein öffentliches OCI-Distributionsartefakt unter `ghcr.io/iunknown404i/threadcells-release-bundle`. Es enthält das verifizierte Release-Archiv, das Python-Wheel, Prüfsummenverzeichnisse, das Kandidatenmanifest, die SBOM und die Metadaten des Release-Bundles für ein exaktes Release-Tag und eine exakte Quellrevision.

Dieses Paket ist ein Distributions-Bundle, kein Docker-Image und keine unterstützte Container-Deployment-Umgebung. Verwenden Sie nach der Prüfung seiner Prüfsummen den normalen Prozess für Kandidateninstallation und Deployment; versuchen Sie nicht, das OCI-Artefakt als ThreadCells-Dienst auszuführen.

`.github/workflows/publish-release-bundle.yml` veröffentlicht bei einem genehmigten GitHub Release oder durch einen expliziten Backfill-Dispatch. Es akzeptiert annotierte `v0.X.Y-alpha`-Tags mit einem vorhandenen Nicht-Entwurfs-Prerelease, behält die Kompatibilität mit unveränderlichen historischen `v0.X.Y-alpha.N`-Tags bei, erstellt die exakte getaggte Quelle neu und prüft sie, verweigert das Ersetzen eines nicht passenden Versions-Tags und aktualisiert ausschließlich `latest-alpha`. ThreadCells veröffentlicht während der technischen Vorschau kein unqualifiziertes `latest`-Tag.

## Konvention für Versionslinien

ThreadCells folgt der normalen SemVer-Prerelease-Reihenfolge. Während der Alpha-Vorschau erhöht jede neue Veröffentlichung die normale semantische Version und behält `alpha` als Prerelease-Stufe bei. Die Python-Paketierung normalisiert ein Alpha ohne Suffix wie `v0.3.3-alpha` zu `0.3.3a0`.

- `v0.1.0-alpha.1` war das erste öffentliche Alpha-Release.
- `v0.1.0-alpha.2` ist eine unveränderliche veröffentlichte technische Vorschau.
- `v0.2.0-alpha.1` ist die konsolidierte Release-Linie für Mehrsprachigkeit und Zuverlässigkeit.
- `v0.3.0-alpha.1` ergänzt Lifecycle-Konsistenz, dauerhafte Erstellungsreihenfolge, Full Cleanup und eine systemische Routing-Richtlinie.
- `v0.3.0-alpha.2` korrigiert die Workflow-Composer-Zustellung und macht den Terminal-Exit für ausführbare Workflow-Autorität endgültig.
- `v0.3.3-alpha` ergänzt die authentifizierte Oberfläche um Englisch/Russisch und führt einen einzigen kanonischen, sprachgeordneten Docs-Bestand für App und öffentliche Website ein.
- Eine spätere Alpha-Veröffentlichung erhöht die semantische Version bewusst und behält die Stufe `alpha` ohne numerisches Suffix bei.

Verschieben Sie niemals ein bestehendes Tag. Änderungen allein an der Repository-Governance lösen weder eine Versionsänderung noch ein Release aus. Aktualisieren Sie alle kanonischen versionstragenden Oberflächen gemeinsam erst dann, wenn der nächste wesentliche Implementierungs-Umriss für die Veröffentlichung bereit ist.
