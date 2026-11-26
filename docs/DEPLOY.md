# デプロイ手順

Proxmox VE の Debian / Ubuntu LXC に配置する例です。環境変数は `.env` の値より優先されます。`python -m app` は常に Uvicorn を1ワーカーで起動します。レート制限、同時取得の共有、統計、バックテストのジョブ枠がプロセス単位で管理されるため、1インスタンスで運用してください。

## 銘柄ディレクトリ

日本の JPX 上場銘柄一覧と米国の Nasdaq Trader Symbol Directory を個人利用向けに実行時に取得し、SQLite に保存します。一覧ファイルはリポジトリに同梱していません。

| 環境変数 | 既定値 | 動作 |
| --- | --- | --- |
| `SYMBOL_DIRECTORY_ENABLED` | `true` | 起動時のバックグラウンド更新を有効にします。保存済みの一覧の読み込みと Yahoo Finance による候補補完はこの設定にかかわらず動作します。 |
| `SYMBOL_DIRECTORY_MAX_AGE_DAYS` | `7` | 起動時に各市場のデータが未取得、またはこの日数より古ければ更新します。 |

手動で更新するには、リポジトリのルートで次を実行します。コマンドは設定済みのデータベースを使い、市場ごとの更新件数を表示します。取得や解析に失敗した市場は保存済みの一覧を使用します。稼働中のアプリへ手動更新を反映するには、更新後にアプリを再起動してください。

```bash
uv run python scripts/update_symbol_directory.py
```

## LXC にインストールする

1. LXC 内でデプロイ作業をする一般ユーザーに `sudo` を用意し、Python 3.12 の環境を `uv` で作ります。`<リポジトリURL>` を実際のURLに置き換えます。すでに用意したリポジトリも使えます。

   ```bash
   sudo apt update
   sudo apt install -y ca-certificates curl git sqlite3
   curl -LsSf https://astral.sh/uv/install.sh | sh
   export PATH="$HOME/.local/bin:$PATH"
   sudo install -d -o "$USER" -g "$(id -gn)" -m 0755 /opt/tera-quant-lab
   git clone '<リポジトリURL>' /opt/tera-quant-lab
   cd /opt/tera-quant-lab
   export UV_PYTHON_INSTALL_DIR=/opt/tera-quant-lab/.uv-python
   uv python install 3.12
   uv sync --locked --no-dev --python 3.12
   ```

   `UV_PYTHON_INSTALL_DIR` をサービスからアクセスできる場所に設定することで、`.venv` から `uv` 管理の Python を実行できます。

2. サービス用ユーザー、SQLite データディレクトリ、環境ファイルを作成します。

   ```bash
   sudo useradd --system --user-group --no-create-home --shell /usr/sbin/nologin tera-quant-lab
   sudo install -d -o tera-quant-lab -g tera-quant-lab -m 0750 /var/lib/tera-quant-lab
   sudo install -o root -g root -m 0600 deploy/tera-quant-lab.env.example /etc/tera-quant-lab.env
   .venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(48))'
   sudoedit /etc/tera-quant-lab.env
   ```

   `SECRET_KEY` には生成した値を設定します。LAN から直接接続する場合は `HOST=0.0.0.0`、同じ LXC のリバースプロキシだけが接続する場合は `HOST=127.0.0.1` を使います。直接接続する場合は Proxmox または LXC のファイアウォールで接続元を制限します。

3. systemd のユニットをインストールし、起動を確認します。

   ```bash
   sudo install -m 0644 deploy/tera-quant-lab.service /etc/systemd/system/tera-quant-lab.service
   sudo systemctl daemon-reload
   sudo systemctl enable --now tera-quant-lab
   sudo systemctl status tera-quant-lab
   sudo journalctl -u tera-quant-lab -n 50 --no-pager
   curl --fail --show-error http://127.0.0.1:8000/health
   ```

   `/health` が HTTP 200 と `"database":"ok"` を返せば、SQLite に接続できています。マイグレーションの現在のリビジョンは `migration` に入ります。アプリのエラー画面にリクエスト ID が表示された場合は、`sudo journalctl -u tera-quant-lab -g 'request_id=ここにID'` で対応するログを探せます。

### SQLite の配置とバックアップ

サービスの既定例は `/var/lib/tera-quant-lab/stock_data.db` です。絶対パスを使う場合、Linux は `sqlite+aiosqlite:////var/lib/tera-quant-lab/stock_data.db`、Windows は `sqlite+aiosqlite:///C:/data/stock_data.db` と指定します。`sqlite+aiosqlite:///./stock_data.db` は起動時の作業ディレクトリを基準にします。アプリは起動時に親ディレクトリを作成します。サービスユーザーにディレクトリの書き込み権限を付け、systemd の `ReadWritePaths` も配置先に合わせてください。

SQLite が動作中でも `.backup` で一貫したスナップショットを作れます。更新前にもバックアップし、別の安全なストレージに保管してください。

```bash
sudo install -d -m 0700 /var/backups/tera-quant-lab
sudo sqlite3 /var/lib/tera-quant-lab/stock_data.db \
  ".backup '/var/backups/tera-quant-lab/stock_data-$(date +%F-%H%M%S).db'"
```

### yfinance のキャッシュ

`YFINANCE_CACHE_DIR` が空欄なら yfinance の既定の場所にタイムゾーン、Cookie、ISIN のキャッシュを保存します。指定した場合は起動時にディレクトリを作成します。systemd の例では `CacheDirectory` が書き込み可能な `/var/cache/tera-quant-lab` を用意します。Docker の例では永続ボリューム内の `/data/yfinance-cache` を使用します。

### 更新

デプロイ作業用ユーザーで実行します。起動時に Alembic が `upgrade head` を実行します。

```bash
cd /opt/tera-quant-lab
git pull --ff-only
export UV_PYTHON_INSTALL_DIR=/opt/tera-quant-lab/.uv-python
uv sync --locked --no-dev --python 3.12
sudo systemctl restart tera-quant-lab
curl --fail --show-error http://127.0.0.1:8000/health
```

## Docker で動かす

Compose の例では `.env` は任意で、`APP_ENV` が未指定なら `production` で起動します。起動には32文字以上のランダムな `SECRET_KEY` が必要です。`.env.example` を `.env` にコピーして使う場合は `SECRET_KEY` を設定し、`APP_ENV=development` を `APP_ENV=production` に変更します。

`docker compose up --build -d` で起動します。Compose の例ではホストのポート 8000 を公開し、SQLite を名前付きボリューム `data` に保持します。ホスト内のリバースプロキシだけに公開する場合は、ポートマッピングを `127.0.0.1:8000:8000` に変更します。コンテナへの直接接続元として見えるプロキシの IP を `TRUSTED_PROXIES` に設定してください。

`docker compose ps` でヘルスチェックを確認できます。更新時はバックアップ後に `git pull --ff-only` と `docker compose up --build -d` を実行します。バックアップのために SQLite の `.backup` を使う場合は、SQLite CLI を備えた保守環境に同じボリュームをマウントしてください。

## リバースプロキシ

同じ LXC の nginx に転送する設定例があります。`server_name` は実際のホスト名に変更し、HTTPS を使う場合は nginx 側に証明書を設定します。nginx は `X-Forwarded-For` に接続元を追加し、`X-Forwarded-Proto` に受信時のスキームを設定します。 [nginx 設定例](../deploy/nginx.conf.example)

アプリ側の `TRUSTED_PROXIES` に、実際に通過するプロキシの IP または CIDR をカンマ区切りで指定します。同一 LXC 上の例は `127.0.0.1` です。アプリは直接の接続元が信頼リスト内の場合に転送チェーンを右から検証し、最初に現れる信頼外の IP をクライアントとして記録します。途中に不正な値がある場合は直接の接続元を使います。LAN のクライアントが直接アクセスできる範囲を信頼リストに含めると、偽の転送ヘッダーを送信できます。

## 本番チェックリスト

- `APP_ENV=production` と32文字以上のランダムな `SECRET_KEY` を設定します。既定のキーまたは短いキーを使うと起動に失敗します。
- 同一オリジンで使う場合は `CORS_ORIGINS` を空欄にします。別オリジンのフロントエンドが必要な場合は実際のオリジンを列挙します。
- `TRUSTED_PROXIES` には管理下のプロキシだけを指定します。プロキシを使わない場合は空欄にします。
- `HSTS_ENABLED=true` は HTTPS でのアクセスを確認してから設定します。LAN 内で HTTP を使う場合は既定の `false` を維持します。
- `CONTENT_SECURITY_POLICY` が空欄なら同梱 UI 向けの既定ポリシーを使います。スクリプトは同一オリジンと jsDelivr、フォントは Google Fonts を許可します。追加のリソースが必要な場合は1行のポリシー全体を設定します。タグ色は JavaScript の CSSOM で設定します。ニュースは記事へのリンクを表示します。
- 既定の CSP は `/docs`、`/redoc`、Swagger の OAuth2 リダイレクトの HTML に限り、ドキュメント用のインラインスクリプトやスタイルを許可します。`CONTENT_SECURITY_POLICY` を指定するとこれらのページも含めて上書きされます。
- 本番環境で API ドキュメントの公開が不要な場合は `DOCS_ENABLED=false` にします。`/docs`、`/redoc`、Swagger の OAuth2 リダイレクト、`/openapi.json` が無効になります。
- データ取得と Web バックテストのレート制限は乱用防止用です。既定はユーザーまたは接続元 IP ごとに60秒あたり600リクエストで、利用量の割り当てには使いません。
