---
slug: getting-started
source: QUICK_SETUP.md
source_sha256: sha256:78db799819c59fb095bd3d94baf70231351df948c7841832e9456449e40bd69e
---
# ThreadCells クイックセットアップ

これは、ソースチェックアウトからローカル ThreadCells サーバーまでの最短のサポート対象パスです。不変のローカル候補をビルドして内容を検証し、現在のリポジトリ配下にインストールして、ループバック上だけで待ち受けます。

前提条件、失敗の説明、サービスインストールについては、完全な[インストールガイド](docs/INSTALLATION.md)を使ってください。

## 1. ホストを確認する

ThreadCells は現在、Python 3、Git、tmux、Web ビルド用の Node.js/npm、少なくとも一つのサポート対象プロバイダー CLI を備えた Ubuntu/Debian Linux を対象としています。Codex が主にテストされているプロバイダーです。

リポジトリのルートから実行します。

```bash
python3 --version
git --version
tmux -V
node --version
npm --version
```

## 2. 候補をビルドして検証する

```bash
python3 scripts/build_local_candidate.py --output "$PWD/threadcells-candidate"
candidate="$PWD/threadcells-candidate/threadcells-0.3.0a3-local"
python3 scripts/verify_local_candidate.py --candidate "$candidate"
```

期待される結果: 候補マニフェスト、ファイル、チェックサム、パッケージ済み Web UI の検証が成功します。候補は自己完結したリリース形状のディレクトリです。不変に保つことで、実行中のビルドを識別でき、ロールバックも現実的になります。

## 3. プレビューしてからインストールする

```bash
"$candidate/scripts/install-threadcells.sh" --source "$candidate" --dry-run
"$candidate/scripts/install-threadcells.sh" --source "$candidate" --prefix "$PWD/.threadcells"
```

期待される結果: dry run は何も変更せずに対象を説明し、その後のインストールで Python 環境と ThreadCells コマンドを含む `.threadcells` が作成されます。

## 4. 診断を実行する

```bash
"$PWD/.threadcells/venv/bin/threadcells" doctor
```

エージェントを起動する前に、失敗した必須チェックを解決してください。任意のプロバイダーが未導入のままでもかまいません。UI には **CLI not installed** と表示されます。

## 5. サーバーを起動する

```bash
"$PWD/.threadcells/venv/bin/threadcells-server" --host 127.0.0.1 --port 9889
```

`http://127.0.0.1:9889` を開きます。

期待される結果: Home が読み込まれ、Settings → About に実行中ビルドの識別情報が表示され、このドキュメントは Docs で利用できます。

最初の実行では、ホストとポートを必ずループバック専用にしてください。別のコンピューターから使う場合も、リスナーを `0.0.0.0` に変更せず、[リモートアクセス](docs/REMOTE_ACCESS.md)を使います。

運用モデルは意図的に短くしています。セッションを作成し、エージェントまたはスーパーバイザーを選び、仕事を渡し、ワークフローを監視し、明示的なオーナー判断または最終レビューでのみ介入します。プロバイダーの完了だけではオープンなワークフローは閉じません。

## 6. 実用的な作業を始める

[最初のプロジェクトとエージェント](docs/FIRST_AGENT.md)に従ってください。付属の[安全なスターター例](examples/threadcells-starter/README.md)も、公開やサービス変更を行わない、範囲が限定されたスーパーバイザー／開発者／レビュアー演習です。

## 停止と再開

フォアグラウンドのサーバーは `Ctrl-C` で停止します。エージェント端末は tmux ベースのためブラウザー接続より長く残る場合がありますが、サーバーが中断してもワークフローが完了したと考えてはいけません。同じインストール済み `threadcells-server` を再起動し、Agents を開いて現在の状態と永続的な結果を確認します。

## 次に読むもの

- [基本概念](docs/CONCEPTS.md)
- [Providers](docs/PROVIDERS.md) と [Profiles](docs/PROFILES.md)
- [容量とリソースモデル](docs/RESOURCE_MODEL.md)
- [Housekeeping](docs/HOUSEKEEPING.md)
- [Telegram 通知](docs/TELEGRAM_NOTIFICATIONS.md)
- [バックアップと復元](docs/BACKUP_AND_RESTORE.md)
- [セキュリティモデル](docs/SECURITY_MODEL.md)
