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
| バックテスト | 戦略一覧、単一実行、グリッド探索 | `GET /backtest/strategies`、`POST /backtest/run`、`POST /backtest/grid` |
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

`GET /stats` は認証不要です。従来の `api_calls`、`cache_hits`、`total_requests`、`hit_rate_percent`、`server_requests`、`server_errors`、`error_rate_percent`、`avg_response_time_ms`、`top_endpoints`、`uptime_seconds` に加え、次の項目を返します。

| フィールド | 内容 |
|---|---|
| `today` | UTC当日の `date`、`hits`、`errors`（HTTP 400以上）、`total_ms`（総応答時間） |
| `last_7_days` | 当日を含む7日分の日次集計。UTC日付の昇順で、アクセスのない日も0で埋めます |
| `cache_hit_rate` | 既存カウンターから算出した `cache_hits / (cache_hits + api_calls)`（0～1）。取得実績が0件の場合は `null` |

日次集計は保存済みの値と今回のプロセスで未保存の増分を合算します。集計を返す `GET /stats` 自体は次回の集計から反映されます。`/static/` の配信リクエストは集計から除外します。その他のアクセス先は `/stock/{symbol}/history` などのルートテンプレートで集計され、テンプレートのないURLは `unmatched` にまとめられます。増分は `STATS_FLUSH_SECONDS`（既定60秒）ごとと正常終了時に保存し、90日より古い日次行を削除します。

`GET /stats` のレスポンス例（追加項目）です。

```json
{
  "today": {"date": "2026-01-07", "hits": 8, "errors": 1, "total_ms": 160.5},
  "last_7_days": [
    {"date": "2026-01-01", "hits": 0, "errors": 0, "total_ms": 0},
    {"date": "2026-01-02", "hits": 0, "errors": 0, "total_ms": 0},
    {"date": "2026-01-03", "hits": 0, "errors": 0, "total_ms": 0},
    {"date": "2026-01-04", "hits": 0, "errors": 0, "total_ms": 0},
    {"date": "2026-01-05", "hits": 0, "errors": 0, "total_ms": 0},
    {"date": "2026-01-06", "hits": 3, "errors": 0, "total_ms": 18.4},
    {"date": "2026-01-07", "hits": 8, "errors": 1, "total_ms": 160.5}
  ],
  "cache_hit_rate": 0.75
}
```

### 5. Webバックテスト

#### 戦略一覧

`GET /backtest/strategies` は認証不要です。`strategies` に戦略名と各パラメータの `name`、`type`（`int`、`float`、`str`）、`default`、`description` を返します。制約のあるパラメータには `min`、`max`、`choices` が付きます。`exclusive_min: true` の場合は下限を含みません。`rank_metrics` はグリッド探索の順位に指定できる指標です。

```json
{
  "strategies": [
    {"name": "sma_cross", "params": [
      {"name": "short_window", "type": "int", "default": 20, "description": "短期移動平均のバー数", "min": 1},
      {"name": "long_window", "type": "int", "default": 60, "description": "長期移動平均のバー数", "min": 1},
      {"name": "max_per", "type": "float", "default": null, "description": "買いを許可するPERの上限（空欄で無効）", "min": 0, "exclusive_min": true}
    ]}
  ],
  "rank_metrics": ["return_pct", "sharpe"]
}
```

#### 単一実行

`POST /backtest/run` には `Authorization: Bearer <JWT>` が必要です。

| フィールド | 指定内容 | 既定値 |
|---|---|---|
| `symbol` | 銘柄。1～20文字の英数字および `^`、`.`、`=`、`-`。先頭は英数字または `^`。大文字に正規化 | 必須 |
| `start_date`、`end_date` | `YYYY-MM-DD`。両端を含み、開始日は終了日以前 | 必須 |
| `interval` | `1d`、`1wk`、`1mo` | `1d` |
| `strategy`、`params` | 戦略名とパラメータのオブジェクト。省略したパラメータには戦略の既定値を適用 | `sma_cross`、`{}` |
| `execution` | 翌バー始値 `next_open` または当バー終値 `close` | `next_open` |
| `fee_rate` | 手数料率、0～0.1 | 0 |
| `slippage_bps` | スリッページ、0～1000 bps | 0 |
| `initial_capital` | 初期資金、0より大きい数値 | 1000000 |
| `eps_lag_days` | EPSの利用可能日までの遅延、0～730日の整数 | 90 |
| `risk_free` | 年率無リスク金利、-0.5～1 | 0 |

```json
{
  "symbol": "AAPL", "start_date": "2025-01-01", "end_date": "2025-12-31",
  "interval": "1d", "strategy": "sma_cross", "params": {"short_window": 10, "long_window": 50},
  "execution": "next_open", "fee_rate": 0.001, "slippage_bps": 5, "initial_capital": 100000
}
```

開始日前の株価は指標計算に使います。売買、資産推移、買い持ちの比較は指定期間内で計算します。`max_per` を有効にしたときだけ財務履歴を取得し、利用可能なEPSがないバーでは買いを見送ります。

レスポンスには戦略と買い持ちの指標、同じ日付で比較できる資産推移、約定履歴、警告が含まれます。累積リターン、CAGR、最大ドローダウン、勝率、保有期間の割合などの単位はパーセントです。`total_trades` は決済回数です。未定義の指標、無限大、NaNはJSONの `null` で返します。

```json
{
  "metrics": {"symbol": "AAPL", "return_pct": 8.2, "sharpe": 0.92, "profit_factor": null, "total_trades": 2},
  "benchmark_metrics": {"symbol": "AAPL", "return_pct": 5.1, "sharpe": 0.7, "total_trades": 0},
  "equity_curve": [{"date": "2025-01-02", "equity": 100000, "benchmark_equity": 100000}],
  "trades": [{
    "symbol": "AAPL", "date": "2025-02-04", "signal_date": "2025-02-03", "side": "BUY",
    "price": 220.11, "shares": 100, "fee": 22.011, "slippage": 11, "open_fallback": false,
    "pnl": null, "cash_after": 77966.989
  }],
  "warnings": []
}
```

Web画面の取引CSVは全セルを二重引用符で囲み、セル内の二重引用符を重ねてエスケープします。`=`、`+`、`-`、`@` で始まる値（前に空白がある場合を含む）には、表計算ソフトでの数式評価を抑止するため、セル内の先頭にタブを付けます。負数のセルも対象です。

#### グリッド探索

`POST /backtest/grid` にもBearerトークンが必要です。単一実行の項目に `grid`（パラメータ名から候補値配列へのオブジェクト）と `rank_by`（既定 `sharpe`）を追加します。全候補の直積を評価し、同じ株価と必要に応じた財務履歴を一度ずつ取得します。

```json
{
  "symbol": "AAPL", "start_date": "2025-01-01", "end_date": "2025-12-31",
  "strategy": "sma_cross", "params": {"long_window": 50},
  "grid": {"short_window": [10, 20]}, "rank_by": "return_pct"
}
```

```json
{
  "rank_by": "return_pct",
  "results": [
    {"rank": 1, "params": {"short_window": 10, "long_window": 50, "max_per": null}, "metrics": {"return_pct": 8.2, "sharpe": 0.92}},
    {"rank": 2, "params": {"short_window": 20, "long_window": 50, "max_per": null}, "metrics": {"return_pct": 4.1, "sharpe": 0.48}}
  ]
}
```

上のレスポンス例は指標などを抜粋しています。グリッドの各行には順位、解決済みのパラメータ、戦略の指標が含まれます。

| 環境変数 | 既定値 | 動作 |
|---|---:|---|
| `BACKTEST_MAX_SPAN_YEARS` | 20 | 開始日からの最大期間（暦年） |
| `BACKTEST_GRID_MAX_COMBOS` | 100 | グリッドの組み合わせ上限（設定可能範囲1～500） |
| `BACKTEST_TIMEOUT_SECONDS` | 30 | データ取得と計算を合わせた実行上限（秒） |
| `BACKTEST_MAX_CONCURRENT_PER_USER` | 1 | 1プロセス内でのユーザーごとの同時実行上限。単一実行とグリッドで共有します |

認証エラーはHTTP `401`、入力や組み合わせ上限の違反は `422`（`detail` に検証エラー）、同時実行上限は `429`（`Retry-After` 付き）、時間超過は `504` です。

```json
{"detail": "バックテストが制限時間を超えました。期間や条件を見直してください。"}
```

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
