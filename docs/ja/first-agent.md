---
slug: first-agent
source: docs/FIRST_AGENT.md
source_sha256: sha256:0695738b5c690bf05b93bbd5a0afd0e1ab38857a7f488141af3244ce66dae948
---
# 最初のプロジェクトとエージェント

このチュートリアルでは、意図的に小さなエージェントを一つ起動し、その端末と結果を見つける場所を示します。先に[クイックセットアップ](../QUICK_SETUP.md)を完了し、ThreadCells サーバーを実行したままにしてください。

## 1. 安全なリポジトリを準備する

最初の実行には、使い捨てまたはクリーンな Git リポジトリを使います。ThreadCells はリポジトリでプロジェクトを識別し、その隣に管理済み worktree を作成できます。

```bash
mkdir -p /tmp/threadcells-first-project
cd /tmp/threadcells-first-project
git init
printf '# First project\n' > README.md
git add README.md
git commit -m 'Create first project'
```

期待される結果: `git status --short` は何も出力しません。クリーンな状態から始めると、エージェントの変更を簡単に確認できます。

## 2. ThreadCells を開く

ThreadCells を実行しているマシンで `http://127.0.0.1:9889` を開きます。ホストがリモートの場合は、先に[リモートアクセス](REMOTE_ACCESS.md)で説明する SSH トンネルを確立してください。

**Spawn Agent** を開き、リポジトリをプロジェクトとして選択し、インストール済みのプロバイダーを選びます。**CLI not installed** と表示されたプロバイダーは起動できません。想定するプロバイダーが利用できない場合は、[Providers](PROVIDERS.md)を参照してください。

この最初のタスクには汎用ワーカープロファイルを選びます。次のような範囲を限定したプロンプトを入力します。

```text
Add a short Usage section to README.md. Do not change any other file.
Run git diff --check and report the changed file.
```

エージェントを起動します。

## 3. 端末を監視する

新しいエージェントは **Agents** の下に表示されます。その端末は実際の tmux セッションなので、プロバイダーのネイティブ出力は表示されたまま再接続できます。ThreadCells は、その端末に関するプロジェクト、プロファイル、プロバイダー、セッションの識別情報を記録します。

期待される結果: 状態が starting から running に変わり、プロバイダー出力が表示され、モデルがターンを生成している間は 1 件のアクティブなプロバイダー実行が容量に反映されます。

エージェントがまったく開始しない場合は、プロバイダーの利用可否ラベルと容量カードを確認してください。[Troubleshooting](TROUBLESHOOTING.md)には症状別のチェックがあります。

## 4. 作業を確認する

エージェントが終了したら、永続的な結果とリポジトリの diff を確認します。端末がプロバイダーの最終メッセージに達したことは証跡ですが、マージ、公開、デプロイの許可ではありません。

```bash
cd /tmp/threadcells-first-project
git status --short
git diff -- README.md
```

エージェントが管理済み worktree で作業した場合は、元のリポジトリパスではなく ThreadCells が示す worktree パスを使ってください。worktree は、コミットが意図的に調整されるまで同時書き込み者を分離します。

## 5. 監督を試す

一人のワーカーの仕組みを理解したら、別の小さなタスクでスーパーバイザープロファイルを起動します。一つの実装タスクと一つの独立レビューを割り当てるよう依頼してください。関係は次のようになります。

```text
Owner
  └── Supervisor
        ├── Developer
        └── Reviewer
              ↓
        Durable results return to the supervisor
```

スーパーバイザーは、これらの結果を取り込み、最上位ワークフローを完了する責任を持ち続けます。ワーカーの終了はスーパーバイザーのミッションを閉じません。

## 次の手順

- UI で使われる名称を学ぶ: [基本概念](CONCEPTS.md)。
- カスタムプロファイルを作成する前にプロファイルを理解する: [Profiles](PROFILES.md)。
- 委任が端末の完了をまたいで存続する仕組みを学ぶ: [ワークフローと永続的な結果](WORKFLOWS_AND_RESULTS.md)。
- マシンの規模は保守的に見積もる: [容量とリソースモデル](RESOURCE_MODEL.md)。
