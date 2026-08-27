---
slug: projects-and-worktrees
source: docs/PROJECTS_AND_WORKTREES.md
source_sha256: sha256:330c4175df07b3a91dc7d9e0c88bbf91d6c3bb7b2bb76fd4340828260562dd02
---
# プロジェクトと管理済み worktree

ThreadCells プロジェクトは、登録済みの Git リポジトリです。セッション、プロファイル、統計、ワークフローに安定した所属先を与えます。ThreadCells はリポジトリを登録しただけで安全にすることはありません。クリーンな状態から始め、付与する書き込み境界を理解してください。

## プロジェクトを登録する

Spawn Agent のプロジェクトセレクターで既存のリポジトリを選ぶか、サポート対象のプロジェクトコントロールからリポジトリを追加します。絶対正規パスを使い、ThreadCells ランタイムユーザーが読み取れることを確認してください。

最初のエージェントの前に実行します。

```bash
git -C /path/to/project status --short
git -C /path/to/project worktree list
```

期待される結果: ThreadCells が後で作成するものと、既存の変更や worktree を区別できます。既存の未コミット作業はオペレーターのものです。エージェントは破棄してはいけません。

## 管理済み worktree が存在する理由

プロンプトが無関係でも、一つのチェックアウトに二人の書き込み者がいると互いの編集を上書きできます。管理済み Git worktree は、リポジトリのオブジェクトデータベースを共有しながら、限定された各書き込み者に独自のチェックアウトとブランチを与えます。

```text
Canonical repository
  ├── operator checkout
  ├── supervisor context
  ├── developer worktree
  └── reviewer worktree or read-only context
```

ThreadCells は一時ディレクトリを匿名として扱わず、関係を記録します。これによりクリーンアップと結果の帰属がより安全になります。

## 書き込み権限

書き込み権限を保持するコンテキストだけが、管理済み worktree を変更すべきです。レビュアーは追跡されない第二の書き込み者にならずに diff を調べ、安全なチェックを実行できます。

エージェントがアクティブな間は、管理済み worktree を手作業で編集してはいけません。緊急介入が必要な場合は、まず書き込み者を停止または協調させ、何を変更したかを記録してください。

## 作業を戻す

永続的な結果には変更ファイルとチェックを記載すべきですが、コードに関する真実の源泉は引き続き Git です。通常のリポジトリ手順でマージまたは cherry-pick する前に、worktree の状態、diff、コミットを確認してください。

ThreadCells は公開権限を付与しません。ワーカーの成功結果は、push、tag、deploy、履歴書き換えを許可しません。

## クリーンアップ

Housekeeping は、worktree がアクティブな端末、ワークフロー、書き込みリース、未取り込み結果によって保護されなくなったことを証明できる場合にのみ、管理済み worktree を削除します。未知の所有権は fail closed になります。

ディスク使用量が高い場合は、先に Housekeeping を計画してください。worktree ディレクトリを直接削除してはいけません。Git メタデータと ThreadCells の状態に不整合が残る可能性があります。

## よくある間違い

- 既存の変更を記録せずに dirty なリポジトリから開始する。
- 二つのエージェントに同じチェックアウトへの書き込み権限を与える。
- worktree をセキュリティサンドボックスとして扱う。
- 結果とコミットを取り込む前に worktree を削除する。
- 管理されたブランチが自動的にマージまたは push されたと考える。

worktree の結果がスーパーバイザーに届く仕組みは、[ワークフローと永続的な結果](WORKFLOWS_AND_RESULTS.md)を参照してください。
