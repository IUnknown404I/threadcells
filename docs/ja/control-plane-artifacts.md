---
slug: control-plane-artifacts
source: docs/CONTROL_PLANE_ARTIFACTS.md
source_sha256: sha256:bbb3ff2ed634050407d78c0fff79f097ecc2c8f29b783d422a52469c624fd8b7
---
# コントロールプレーン成果物と AI ワークフロー

ThreadCells は、ローカル候補の `schemas/v1` と wheel の `cli_agent_orchestrator/public_schemas/v1` に、ProfileDefinition V1、ProviderConfiguration V1、AdapterManifest V1、AdapterCapabilities V1 用の JSON Schema Draft 2020-12 ドキュメントを公開します。

開始用ドキュメントを取得するには `threadcells profiles schema|example` または `threadcells providers schema|example` を使います。インポート前に検証してください。フィールドの失敗は、反映された生の値ではなく安定した JSON ポインターの記録として返されます。UI、CLI、API のインポートはすべて同じサービスを呼び出し、不変のリビジョンを作成します。

## AI 支援による成果物ワークフロー

1. `/api/v1/profiles/ai-prompt` または `/api/v1/providers/ai-prompt` から、該当するスキーマ、例、安全な生成プロンプトを取得します。
2. JSON オブジェクトを一つだけ返すようモデルに依頼します。認証情報、非公開パス、実行可能なコマンド、シェルフラグ、未レビューの MCP コマンドを与えないでください。
3. 識別子、プロバイダー参照、権限、ツール、タイムアウト、指示を手動で確認します。
4. `validate` を実行し、すべての JSON ポインターの問題に対処します。
5. オペレーターのレビュー後にのみインポートします。ワイルドカードのツールまたはその他の特権的な権限を必要とするインポートには、信頼済みオペレーター用の別経路が必要です。
6. 起動前に解決済みプレビューを使い、インポート後にエクスポートして、マスク済みの正規成果物を確認します。

AI が生成した JSON は信頼できない入力です。もっともらしいドキュメントであっても、アダプターコードのインストール、MCP 機能の登録、オーナー権限の付与、リポジトリポリシーの回避はできません。組み込みプロファイルは不変のままであり、エクスポートにプロバイダー認証情報や起動権限が含まれることはありません。
