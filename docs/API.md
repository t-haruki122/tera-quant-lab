# API Guide

プロジェクト全体説明へ戻る: [README.md](../README.md)

## 概要

バックエンドは FastAPI ベースの REST API で、株式データ・為替・ユーザー管理・統計情報を提供します。

## エンドポイント一覧

| 分類 | 主な機能 | 主なエンドポイント |
|---|---|---|
| 株価 | 現在価格、過去価格の取得 | `GET /stock/{symbol}`、`GET /stock/{symbol}/history` |
| 企業分析 | 財務情報、企業プロフィール、投資指標の取得 | `GET /stock/{symbol}/financials`、`GET /stock/{symbol}/profile`、`GET /stock/{symbol}/indicators` |
| ニュース | 銘柄関連ニュースの取得 | `GET /stock/{symbol}/news` |
| 為替 | 通貨ペアの現在レート取得 | `GET /forex/{pair}` |
| ユーザー機能 | ユーザー登録/ログイン、銘柄リスト管理、タグ管理 | `POST /user/register`、`POST /user/login`、`/user/{user_id}/lists...` |
| 運用 | API利用統計の取得 | `GET /stats` |

## 主な機能詳細

### 1. 株価・分析データ

- 現在株価: `GET /stock/{symbol}`
- 過去株価: `GET /stock/{symbol}/history`
- 財務情報: `GET /stock/{symbol}/financials`
- 財務履歴: `GET /stock/{symbol}/financials/history`
- 企業プロフィール: `GET /stock/{symbol}/profile`
- 投資指標: `GET /stock/{symbol}/indicators`
- 関連ニュース: `GET /stock/{symbol}/news`

`history` の主なクエリパラメータ:

| パラメータ | 説明 | 例 |
|---|---|---|
| `start_date` | 開始日 (`YYYY-MM-DD`) | `2025-01-01` |
| `end_date` | 終了日 (`YYYY-MM-DD`) | `2025-01-31` |
| `interval` | 取得間隔 | `1d`, `1wk`, `1mo` |

### 2. 為替データ

- 為替レート: `GET /forex/{pair}`
- 例: `USDJPY` を指定すると内部で `USDJPY=X` に補完されます。

### 3. ユーザー・ウォッチリスト

- ユーザー登録: `POST /user/register`
- ログイン: `POST /user/login`
- デフォルトリスト取得: `GET /user/{user_id}/default-list`
- リスト一覧/作成: `GET/POST /user/{user_id}/lists`
- リスト詳細/更新/削除: `GET/PUT/DELETE /user/{user_id}/lists/{list_id}`
- 銘柄追加: `POST /user/{user_id}/lists/{list_id}/items`
- 銘柄削除: `DELETE /user/{user_id}/lists/{list_id}/items/{symbol}`
- タグ更新: `PUT /user/{user_id}/lists/{list_id}/items/{symbol}/tags`
- 銘柄メモ取得/保存: `GET/PUT /user/{user_id}/memo/{symbol}`

#### 認証トークン

`POST /user/register` と `POST /user/login` は JSON の `username`、`password` を受け取り、次の形式を返します。
新規登録のパスワードは8文字以上です。8文字未満の場合は HTTP `400` と `detail: "パスワードは8文字以上にしてください"` を返します。既存ユーザーのログインにはこの最小文字数を適用しません。

```json
{"id": 1, "username": "taro", "access_token": "<JWT>", "token_type": "bearer"}
```

`/user/{user_id}/...` の全操作（デフォルトリスト、リスト、アイテム、タグ、メモ）には、取得したトークンを `Authorization: Bearer <JWT>` ヘッダーで送信してください。
未指定・不正・期限切れのトークンは `401`、トークンのユーザーとURLの `user_id` が異なる場合は `403` です。
自分の `user_id` を指定しても、他人の `list_id` は読み書きできません。メモもユーザーごとに分離されます。
株価・分析・為替・統計の取得には認証不要です。

```bash
curl -X POST http://localhost:8000/user/login \
  -H "Content-Type: application/json" \
  -d '{"username":"taro","password":"your-password"}'

# レスポンスの access_token と id を使用
curl http://localhost:8000/user/1/lists \
  -H "Authorization: Bearer <access_token>"
```

JWTの署名方式はHS256、有効期間は既定で1440分（24時間）です。期限切れ後は再ログインしてください。
`.env` の `SECRET_KEY` には、運用前に十分長いランダムな秘密値を必ず設定してください（例: `uv run python -c "import secrets; print(secrets.token_urlsafe(48))"`）。
開発用の既定値のまま起動するとWARNINGが出ます。有効期間は `ACCESS_TOKEN_EXPIRE_MINUTES` で変更できます。
パスワードはArgon2で保存し、旧SHA-256形式のパスワードはログイン成功時に自動移行します。

フロントエンドはユーザー情報とトークンをlocalStorageに保存して送信します。トークンを送信したリクエストの `401` 時は保存情報とキャッシュを消去してログイン画面を表示します。未ログイン時の認証失敗では入力内容を保持してエラーを表示します。
ログイン・ログアウト時にもGETキャッシュを消去します。

#### ログイン・登録のレート制限

`POST /user/login` は、接続元IPと小文字化したユーザー名の組み合わせごとに300秒間で失敗5回まで許可し、6回目はパスワード検証前に拒否します。成功するとこの失敗カウンターをリセットします。
さらにユーザー名にかかわらず、IPごとに300秒間で20回まで試行できます（成功・失敗とユーザー名制限で拒否された試行を含む）。IP上限到達後の試行では待機時間を延長しません。
`POST /user/register` はIPごとに3600秒間で5回まで許可します。登録失敗も回数に含みます。

上限到達時はHTTP `429`、`{"detail":"試行回数が多すぎます。しばらくしてから再試行してください。"}`、および次の試行までの秒数を表す整数の `Retry-After` ヘッダー（1以上）を返します。
フロントエンドは認証モーダル内にこのメッセージを表示し、429ではログアウトしません。バックテストCLIは数値の `Retry-After` を最大30秒まで尊重し、欠落・不正値の場合は従来のバックオフを使います。待機中もキャンセルできます。

| 環境変数 | 既定値 | 意味 |
|---|---:|---|
| `RATE_LIMIT_ENABLED` | `true` | `false` で全制限を無効化 |
| `RATE_LIMIT_LOGIN_FAILURES` | `5` | IP・ユーザー名ごとの失敗上限 |
| `RATE_LIMIT_LOGIN_WINDOW_SECONDS` | `300` | 失敗を数える期間（秒） |
| `RATE_LIMIT_LOGIN_IP_ATTEMPTS` | `20` | IPごとのログイン試行上限 |
| `RATE_LIMIT_LOGIN_IP_WINDOW_SECONDS` | `300` | IP試行を数える期間（秒） |
| `RATE_LIMIT_REGISTER_ATTEMPTS` | `5` | IPごとの登録試行上限 |
| `RATE_LIMIT_REGISTER_WINDOW_SECONDS` | `3600` | 登録試行を数える期間（秒） |

回数・期間は正の整数を指定してください。接続元は `request.client.host` を使い、`X-Forwarded-For` は参照しません。接続元が取得できない場合は共通の `unknown` キーを使います。
状態はロックで保護したプロセス内メモリに保持し、リクエストと認証完了時に期限切れの時刻・空キーを削除します。再起動でリセットされ、複数ワーカー間では共有されません。並行して検証中のログインはまだ失敗に数えず、IP試行上限が同時実行数も制限します。

### 4. 統計・キャッシュ

- 利用統計: `GET /stats` で API 呼び出し回数やキャッシュヒット情報を取得
- SQLite を使ったキャッシュで外部APIアクセスを最適化

## APIドキュメントUI

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

## クイック利用例

```bash
# 現在株価
curl http://localhost:8000/stock/AAPL

# 過去株価
curl "http://localhost:8000/stock/AAPL/history?start_date=2025-01-01&end_date=2025-01-31&interval=1d"

# 会社プロフィール
curl http://localhost:8000/stock/AAPL/profile

# 財務履歴（年次）
curl "http://localhost:8000/stock/AAPL/financials/history?limit=8"

# 投資指標
curl http://localhost:8000/stock/AAPL/indicators

# 為替
curl http://localhost:8000/forex/USDJPY

# API統計
curl http://localhost:8000/stats
```
