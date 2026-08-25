---
slug: remote-access
source: docs/REMOTE_ACCESS.md
source_sha256: sha256:6d63e2f9473ae0f156d8e8a207c8bedfb00dcc4581f44727aae2ae48b1819d10
---
# リモートアクセス

ThreadCells はループバック優先です。サーバーは公開インターフェースではなく `127.0.0.1` で待ち受けるべきです。通常の Web UI はオペレーターコンソールであり、一般的なログイン境界を提供しません。

> 生の ThreadCells ポートを公衆インターネットに直接公開してはいけません。

ときどきのアクセスには SSH トンネルを選んでください。恒久的な URL が必要で、ホスト所有者がその認証／プロキシ境界を明示的に承認している場合は、認証済み HTTPS リバースプロキシを使います。

## オプション A: SSH トンネル

ノート PC から ThreadCells ホストへ接続し、ローカルポートを転送します。

```bash
ssh -L 9889:127.0.0.1:9889 user@server
```

その SSH セッションを開いたまま、次にアクセスします。

```text
http://127.0.0.1:9889
```

ブラウザーはノート PC のポート 9889 に接続します。SSH が通信を暗号化し、サーバーの `127.0.0.1:9889` に送ります。ThreadCells は引き続きサーバーのループバックインターフェースだけで待ち受けます。

ローカルポート 9889 が使用中なら、別のローカルポートを使います。

```bash
ssh -L 19889:127.0.0.1:9889 user@server
```

次に `http://127.0.0.1:19889` を開きます。SSH が切断されるとトンネルも終了します。同じコマンドで再接続してください。OpenSSH は、現在の Linux、macOS、Windows のインストールで同じ `-L` 構文を提供します。

## オプション B: Caddy と Authelia

便利な恒久 URL のために、認証と HTTPS を ThreadCells の前段に置きます。

```text
Browser
   ↓ HTTPS
Caddy reverse proxy
   ↓ forward-auth
Authelia login and second factor
   ↓ approved request
ThreadCells at 127.0.0.1:9889
```

Caddy は TLS を終端し、HTTP/WebSocket 通信をプロキシします。Authelia はユーザー認証の境界を提供します。ThreadCells はローカル専用のアップストリームのままです。このセットアップは第二の ThreadCells 認可システムを作り出しません。

### 前提条件

- ホストを指す `threadcells.example.com` と `auth.example.com` の DNS レコード。
- Caddy が利用できる受信 TCP ポート 80 と 443。
- `127.0.0.1:9889` で正常な ThreadCells。
- 公式手順に従ってインストールされた Caddy と Authelia。
- Authelia のストレージ、セッションシークレット、通知機能、および少なくとも一人のユーザーが安全に設定されていること。
- 既存の ThreadCells サービス環境で `THREADCELLS_TRUSTED_PROXY_ORIGINS=https://threadcells.example.com` が設定されていること。

[公式 Caddy インストールガイド](https://caddyserver.com/docs/install)と[公式 Authelia はじめにガイド](https://www.authelia.com/integration/prologue/get-started/)を使ってください。Authelia は [bare-metal](https://www.authelia.com/integration/deployment/bare-metal/) と [container](https://www.authelia.com/integration/deployment/docker/) の両方のデプロイを文書化しています。

### Caddy を Authelia に接続する

Authelia の最新の [Caddy 統合ガイド](https://www.authelia.com/integration/proxies/caddy/)に従ってください。コンパクトな Caddyfile の形は次のとおりです。

```caddyfile
auth.example.com {
    reverse_proxy 127.0.0.1:9091
}

threadcells.example.com {
    forward_auth 127.0.0.1:9091 {
        uri /api/authz/forward-auth
        copy_headers Remote-User Remote-Groups Remote-Email Remote-Name
    }
    reverse_proxy 127.0.0.1:9889 {
        header_up Host 127.0.0.1:9889
    }
}
```

これはサービス間の接続として扱い、完全な Authelia 設定と見なさないでください。Authelia では、公式ガイドを使って公開 URL、cookie domain、アクセス制御ポリシー、ユーザー、通知機能、ストレージ、second factor を設定します。生成されたシークレットはリポジトリの外部に保存してください。`THREADCELLS_TRUSTED_PROXY_ORIGINS` を追加または変更したら ThreadCells を再起動します。この値はパスを含まない HTTPS origin の正確なカンマ区切り allowlist です。任意のプロキシヘッダーを信頼せずに、cookie 認証済みのオペレーター変更が公開ブラウザー origin を受け入れられるようにします。

Caddy の [`forward_auth`](https://caddyserver.com/docs/caddyfile/directives/forward_auth) は、各リクエストが ThreadCells に到達する前に確認します。アップストリームの `Host` 上書きは、Caddy が外部ホスト名と認証境界を担当する間、ThreadCells のループバック専用 Trusted Host 境界を保持します。Caddy の [`reverse_proxy`](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy) は、ライブ端末が使う WebSocket upgrade をサポートします。

### 起動と検証

サービスをリロードする前に設定を検証します。

```bash
caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
sudo systemctl status caddy authelia --no-pager
```

次のすべてを検証します。

- `https://auth.example.com` に期待どおりの Authelia ページが表示される。
- サインアウト中に `https://threadcells.example.com` を訪れると拒否またはリダイレクトされる。
- サインインして設定済みの second factor を完了すると ThreadCells が開く。
- エージェント端末が出力をストリーミングし、ブラウザー更新後に再接続する。
- ホスト上で `curl http://127.0.0.1:9889/health` が引き続き動作する。
- ポート 9889 が公開到達可能ではない。

### よくある問題

- **リダイレクトループ:** Authelia の公開 URL、cookie domain、またはアクセス制御ホストが DNS と一致しません。正確に比較してください。
- **502 Bad Gateway:** Caddy がローカル ThreadCells または Authelia リスナーに到達できません。両方のサービスとループバックポートを確認してください。
- **ログインは成功するが端末がストリーミングしない:** 別のプロキシが WebSocket upgrade ヘッダーを除去せず、リクエストが Caddy の `reverse_proxy` に到達していることを確認します。
- **証明書発行に失敗する:** 公開 DNS と受信ポート 80/443 を確認してください。Caddy の[自動 HTTPS ドキュメント](https://caddyserver.com/docs/automatic-https)が要件を説明しています。

緊急経路として SSH 転送を利用可能なままにしてください。DNS、TLS、外部認証レイヤーを修復しているときにも役立ちます。
