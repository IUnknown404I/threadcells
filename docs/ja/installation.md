---
slug: installation
source: docs/INSTALLATION.md
source_sha256: sha256:c84db2d7488e6445ebcaad6f64b241573735e49d2ec05fc076b30e77bf854f9f
---
# インストール

このガイドでは、サポート対象のローカルインストール手順と、ThreadCells が検証済み候補からインストールされる理由を説明します。コマンドだけが必要な場合は、[クイックセットアップ](../QUICK_SETUP.md)を使ってください。

## サポート対象のベースライン

現在の技術プレビューは単一の Ubuntu/Debian Linux ホストをサポートします。ThreadCells は信頼できるオペレーターアカウントとローカル Git チェックアウトを前提とします。他の Linux ディストリビューションでも動作する場合がありますが、サポート対象のベースラインではありません。macOS と Windows は Web UI にリモートアクセスできますが、ThreadCells ホストとしてはサポートされません。

## 前提条件

次をインストールまたは確認します。

- Python 3 と `venv` サポート。
- Git。
- tmux。
- パッケージ済み Web UI をビルドするための Node.js と npm。
- リリースおよびサービススクリプトで使われる一般的な POSIX ユーティリティ。
- ThreadCells を実行するアカウント用にインストール・認証済みの、サポート対象プロバイダー CLI を一つ。

重要なコマンドを確認します。

```bash
python3 --version
git --version
tmux -V
node --version
npm --version
```

ThreadCells は、CLI が存在しないアダプターを登録できます。これはインストール失敗ではありません。起動する予定のプロバイダーだけが準備済みであれば十分です。[Providers](PROVIDERS.md)を参照してください。

## 状態の保存場所

デフォルトでは、運用状態は次の配下にあります。

```text
~/.aws/cli-agent-orchestrator/
```

この歴史的なディレクトリ名は互換性のため残されています。SQLite データベース、ログ、管理済み worktree、エージェントコンテキスト、添付ファイル、プロバイダー成果物、その他のランタイム状態が含まれる場合があります。別の絶対位置を選ぶには、最初の起動前に `CAO_HOME_DIR` を設定します。

インストール済みアプリケーションとそのランタイム状態は別物です。

- **candidate/install** には、バージョン付きコードと静的 Web アセットが含まれます。
- **state root** には、データベース、変更可能なオペレーターデータ、Telegram bot token など任意の制限的な ThreadCells 所有シークレットファイルが含まれます。
- プロバイダー CLI は独自の認証情報とロールアウト履歴を別の場所に保持する場合があります。

インストールを置き換える前に、変更可能な状態をバックアップしてください。ランタイム状態やプロバイダー認証情報をコミットしてはいけません。

## なぜローカル候補なのか？

候補は、単一の正確なソースリビジョンからビルドされたリリース形状のディレクトリです。そのマニフェストとチェックサムにより、インストールに触れる前に何が実行されるかを検証できます。ステージングと昇格では、以前の候補をロールバック用に保持することもできます。

この規律は変更中のチェックアウトから直接実行するより慎重ですが、Web UI、Python コード、ドキュメント、ビルド識別子が異なるリビジョンから密かに混在することを防ぎます。

## 候補をビルドする

リポジトリのルートから実行します。

```bash
python3 scripts/build_local_candidate.py --output "$PWD/threadcells-candidate"
candidate="$PWD/threadcells-candidate/threadcells-0.3.0a3-local"
python3 scripts/verify_local_candidate.py --candidate "$candidate"
```

期待される結果: 検証ツールがマニフェスト、チェックサム、パッケージ済みドキュメント、アプリケーションファイルを受け入れます。検証に失敗した候補をインストールしてはいけません。

## プレビューとインストール

ランタイムアカウントが実行できる絶対プレフィックスを選びます。以下のリポジトリローカルプレフィックスは評価に便利です。

```bash
"$candidate/scripts/install-threadcells.sh" --source "$candidate" --dry-run
"$candidate/scripts/install-threadcells.sh" --source "$candidate" --prefix "$PWD/.threadcells"
```

dry run は意図的に先に実行します。ソースと対象を確認してから、実際のインストールを実行してください。

## インストール済み CLI を検証する

```bash
"$PWD/.threadcells/venv/bin/threadcells" info
"$PWD/.threadcells/venv/bin/threadcells" doctor
"$PWD/.threadcells/venv/bin/threadcells" providers list
```

`doctor` は読み取り専用です。不足している必須システムユーティリティを解決してください。プロバイダー出力は、既知のアダプターと、インストール済みで利用可能な CLI を区別する必要があります。

## ローカルで起動する

```bash
"$PWD/.threadcells/venv/bin/threadcells-server" --host 127.0.0.1 --port 9889
```

別のシェルで実行します。

```bash
curl -fsS http://127.0.0.1:9889/health
```

`http://127.0.0.1:9889` を開きます。Settings → About を確認し、バージョンとリビジョンが検証した候補と一致することを確認してください。

永続的なインストールには、[Deployment](DEPLOYMENT.md)で説明するリポジトリの正規サービス／デプロイ機構を使います。公開バインドアドレスを即興で設定してはいけません。

## 初期の失敗

- **`python3 -m venv` が失敗する:** ディストリビューションの Python venv パッケージをインストールします。
- **`tmux` がない:** エージェントを起動する前にインストールします。端末の永続性はこれに依存します。
- **Web アセットのビルドに失敗する:** サポート対象の Node/npm ベースラインを使い、固定された依存関係をインストールして、候補を再ビルドします。
- **プロバイダーが CLI not installed と表示する:** ランタイムユーザー用にそのプロバイダーの正規コマンドをインストールするか、すでに準備済みのプロバイダーを選びます。
- **プロバイダーはインストール済みだが未認証:** ランタイムユーザーとしてプロバイダー自身のログインフローを完了し、その後で preflight を繰り返します。
- **ポート 9889 が使用中:** 競合するローカルプロセスを停止するか、別のループバックポートを選んで一貫して使用します。
- **別のマシンのブラウザーが接続できない:** これはループバックリスナーでは想定どおりです。[リモートアクセス](REMOTE_ACCESS.md)を使ってください。

## 削除の境界

インストールプレフィックスを削除しても、運用状態、プロバイダー認証情報、Git リポジトリ、worktree、バックアップ、サービス定義は安全には削除されません。ThreadCells を停止し、検証済みバックアップを作成してから、それぞれのカテゴリを個別に特定してください。対象となるランタイム成果物には Housekeeping を使い、アンインストールの近道として state root を再帰的に削除してはいけません。

次に、[最初のプロジェクトとエージェント](FIRST_AGENT.md)に従ってください。
