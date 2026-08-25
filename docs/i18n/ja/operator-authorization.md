---
slug: operator-authorization
source: docs/OPERATOR_AUTHORIZATION.md
source_sha256: sha256:543fc9c31e1ffe8e120aa726c819f0e9180d0f6ca92b28c9b4ce549d0025d4b1
---
# オペレーター認可

オペレーター認可は、Settings における機密性の高いコントロールプレーン変更を保護します。これは通常の Web UI へのアクセスとは別です。エージェント、ターミナル、ドキュメント、統計の閲覧にオペレーターシークレットは必要ありません。

この機能はリモートユーザー認証ではありません。ThreadCells はループバック専用に保ち、別のマシンからアクセスが必要な場合は [リモートアクセス](REMOTE_ACCESS.md) に従ってください。

## 仕組み

ThreadCells はシークレットから導出した検証子を保存し、平文のシークレットは保存しません。サーバーは起動時にその検証子を読み込みます。正しいシークレットを入力すると、短命で安全なオペレーターセッションが作成されます。期限切れ後は保護された変更が再びロックされます。

```text
Verifier configured
      ↓
Settings shows Locked
      ↓ enter operator secret
Unlock operator changes
      ↓
Short-lived authenticated session
      ↓ expires
Locked again
```

オペレーターシークレットの最小長は正確に **5文字** です。4文字は拒否されます。より長くランダムに生成したシークレットを強く推奨します。

## 検証子を作成する

任意の読み取り可能な作業ディレクトリーから、管理ユーザーとしてスタンドアロンコマンドを実行します。

```bash
threadcells operator create-verifier --output /etc/threadcells/operator-verifier.json
```

コマンドはシークレットをエコーせずにプロンプトを表示し、ソルト付き KDF 検証子だけを書き込みます。ThreadCells サービスアカウントからはファイルを読めるようにしつつ、ディレクトリーを変更できないように保護してください。適切なレイアウトの例は次のとおりです。

```bash
sudo chown root:threadcells /etc/threadcells
sudo chmod 0750 /etc/threadcells
sudo chown root:threadcells /etc/threadcells/operator-verifier.json
sudo chmod 0640 /etc/threadcells/operator-verifier.json
```

グループ名は、インストールで使用するサービスアカウントに合わせてください。パス内のすべての親ディレクトリーも信頼できる必要があります。ThreadCells は、サービス所有またはグループ/その他による書き込みが可能なディレクトリーを経由する検証子を拒否します。

シークレットまたは検証子 JSON を、リポジトリー、データベース、ログ、ブラウザーストレージ、テレメトリー、またはアンロック操作以外の API リクエストに入れないでください。

## サーバーを設定する

サーバー環境で絶対検証子参照を設定します。

```bash
THREADCELLS_OPERATOR_VERIFIER_FILE=/etc/threadcells/operator-verifier.json
```

ThreadCells サーバーだけを再起動し、Settings → General → Operator authorization を確認してください。状態は **Configured · Locked** であるべきで、**Not configured** や **Configuration invalid** ではありません。

セッションエンドポイントは安全な状態だけを報告します。

```bash
curl -s http://127.0.0.1:9889/operator/session
```

想定結果には、アンロック前に `"configured": true` と `"authenticated": false` が含まれます。検証子パス、ソルト、ハッシュ、シークレットは返しません。

## 保護された変更をアンロックする

Settings でシークレットを入力し、**Unlock operator changes** を選択します。既定の認証ウィンドウは5分です。UI には期限が表示され、セッション終了時に再びロック状態になります。

保護された設定呼び出しはロック中に失敗し、認証済みセッション中は成功します。ブラウザーはサーバーの短命な安全なセッション Cookie を使い、オペレーターシークレットを永続化しません。

Full Cleanup はこの正確な権限を再利用します。プレビューは読み取り専用の安全確認として利用できますが、実行には現在のオペレーターセッションと標準の永続操作確認が必要です。確認時にシークレットを再入力することはありません。クリーンアップ専用シークレット、URL 認証情報、ブラウザーストレージ値、永続的な平文コピーは存在せず、有効期限、再ロック、レート制限も変わりません。

## シークレットを置き換える

一時的な管理パスに新しい検証子を作成し、所有権と権限を検証してから、設定済みファイルをアトミックに置き換え、ThreadCells を再起動します。置き換え後、既存のオペレーターセッションは無効として扱うべきです。

現在の Web UI は、意図的に未認証のリモートリセットや Settings ベースの検証子ライターを提供しません。CLI によるプロビジョニングは検証子を OS の所有権下に置き、より広いセキュリティサブシステムの作成を避けます。

## Owner XHigh の起動

組み込みの `critical_sol_xhigh_owner` プロファイルは、**Create Session & Spawn Agent**、既存セッションの **Add Agent**、およびローカル CLI から利用できます。両方の Web フローは同じ例外的権限の警告を表示し、明示的な確認とアンロック済みオペレーターセッションを要求し、短命でリビジョン/スコープに結び付いた1回限りの機能を作成して通常の起動経路で消費します。Add Agent はこの機能を既存セッションと、その正規に解決された作業ディレクトリーに結び付けます。オペレーターは任意の置換パスを入力できません。

ローカル CLI 経路には `--owner-xhigh` と明示的な対話確認が必要です。ループバック経由で同じクラスの1回限りの機能を作成して消費します。再利用可能なバイパスやヘッダーの近道はありません。チェックボックス/確認がない、オペレーターシークレットがないか誤っている、スコープが不一致、または許可が再利用されると、フェイルクローズします。認証済み Web クライアントは、対応する起動を行うためだけに不透明な機能を1回受け取ります。オペレーターシークレットは返されません。どちらの値もエージェント/セッションメタデータ、プロバイダープロンプト、ターミナル記録、ログ、ブラウザーストレージにコピーされません。これらの起動経路は、子を認可したり保護された Settings 変更を弱めたりしません。

## トラブルシューティング

- **Not configured:** 環境変数が存在しないか空です。実際のサーバープロセスに届いていることを確認してから再起動してください。
- **Configuration invalid:** 安全な検証理由についてサーバーログを確認してください。JSON スキーマ、絶対パス、可読性、所有者、モード、すべての親ディレクトリーを確認します。パスまたは所有権の問題を隠すためだけに有効な検証子を作り直さないでください。
- **Correct secret rejected:** ジェネレーターとサーバーが同じ検証子ファイルを使用していること、および古いサーバープロセスがまだ実行中でないことを確認してください。
- **Unlock succeeds then immediately locks:** ブラウザー Cookie が受け入れられていることと、システムクロックが正しいことを確認してください。
- **Unlock works locally but protected changes fail through an HTTPS proxy:** ThreadCells サービス環境で `THREADCELLS_TRUSTED_PROXY_ORIGINS` を正確な公開 HTTPS オリジン（例: `https://threadcells.example.com`）に設定してから再起動してください。パス、ワイルドカード、未認証のオリジンを追加しないでください。
- **Verifier creation fails in an unrelated directory:** 現行の ThreadCells ビルドを使用してください。スタンドアロンコマンドが作業ディレクトリーの `.env` を検査してはいけません。

周囲の信頼前提については [セキュリティモデル](SECURITY_MODEL.md) を参照してください。
