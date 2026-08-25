---
slug: provenance
source: docs/PROVENANCE.md
source_sha256: sha256:c57ec06cc83c35daec17670906144bc3460e4d341620e698af229eedc2b3eb00
---
# 来歴

ThreadCells は AWS Labs CLI Agent Orchestrator から派生した独立した downstream です。保持されている上流ライセンスおよび帰属表示は、ソース候補の `LICENSE` と `NOTICE` ファイルにあります。このリポジトリは、既存の動作を維持するために必要な互換性のある内部 `cao` 名とコマンドを保持します。公開製品サーフェスでは ThreadCells を使用します。AWS のスポンサーシップまたは推奨を示唆するものではありません。

各ローカル候補は、コミット済みソースリビジョン、ファイルマニフェスト、SHA-256 チェックサム、および直接依存関係の証跡を `candidate-manifest.json`、`SHA256SUMS`、`sbom.cdx.json` に記録します。SBOM は宣言済み／解決済みの直接依存関係の証拠であり、ライセンスクリアランス、脆弱性評価、または上流との同等性に関する表明ではありません。公開配布には引き続き、公開リポジトリ、セキュリティ連絡先、ブランディングの来歴、依存関係／ライセンスレビューについてオーナー承認が必要です。
