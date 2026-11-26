# API Guide

プロジェクト全体説明へ戻る: [README.md](../README.md)

## 概要

バックエンドは FastAPI ベースの REST API で、株式データ・為替・ユーザー管理・統計情報を提供します。

## エンドポイント一覧

| 分類 | 主な機能 | 主なエンドポイント |
|---|---|---|
| 銘柄検索 | 会社名・銘柄コード・ティッカーから日米の銘柄を検索 | `GET /symbols/search` |
| 株価 | 現在価格、過去価格の取得 | `GET /stock/{symbol}`、`GET /stock/{symbol}/history` |
| 企業分析 | 財務情報、企業プロフィール、投資指標の取得 | `GET /stock/{symbol}/financials`、`GET /stock/{symbol}/profile`、`GET /stock/{symbol}/indicators` |
| ニュース | 銘柄関連ニュースの取得 | `GET /stock/{symbol}/news` |
| 為替 | 通貨ペアの現在レート取得 | `GET /forex/{pair}` |
| 経済指標 | 主要な市況データと日次終値の推移 | `GET /market/overview` |
| ユーザー機能 | ユーザー登録/ログイン、銘柄リスト管理、タグ管理 | `POST /user/register`、`POST /user/login`、`/user/{user_id}/lists...` |
| Claude 連携 | APIトークンの発行・無効化、読み取り専用 MCP | `/user/{user_id}/api-tokens`、`/mcp` |
| ウォッチリストの保有資産 | 株数・現金の設定、円換算評価、構成比 | `GET /user/{user_id}/lists/{list_id}/summary` |
| 価格アラート | アラートの管理・判定 | `/user/{user_id}/alerts`、`POST /user/{user_id}/alerts/check` |
| バックテスト | 戦略一覧、単一実行、グリッド探索、ポートフォリオ、ウォークフォワード、結果保存 | `GET /backtest/strategies`、`POST /backtest/run`、`POST /backtest/grid`、`POST /backtest/portfolio`、`POST /backtest/walkforward`、`/backtest/runs` |
| 運用 | API利用統計と稼働状態の取得 | `GET /stats`、`GET /health` |

## 主な機能詳細

### 銘柄検索

`GET /symbols/search?q=トヨタ&limit=10` は認証不要です。`q` は前後の空白を除いて1〜50文字、`limit` は1〜20件（既定10件）です。全角と半角、英字の大小、ひらがなとカタカナの違いを揃え、ティッカー・日本の銘柄コードの完全一致、ティッカーの前方一致、会社名の前方一致・部分一致・類似一致の順に候補を返します。

```json
{"items":[{"symbol":"7203.T","name":"トヨタ自動車","market":"JP","segment":"プライム","sector":"輸送用機器","is_etf":false,"source":"jpx"}]}
```

`market` は `JP` または `US` です。`segment` と `sector` は情報がない場合に `null` になります。`source` は JPX の `jpx`、Nasdaq Trader の `nasdaqtrader`、キャッシュ済みの企業プロフィールの `profile`、Yahoo Finance の `yahoo` のいずれかです。ローカル候補が3件未満か銘柄ディレクトリが空のときは Yahoo Finance に検索語を送り、候補を補います。Yahoo Finance が応答できない場合も HTTP `200` でローカル候補を返します。

### 1. 株価・分析データ

- 現在株価: `GET /stock/{symbol}`
  - 取引通貨を取得できた場合は `currency` も返します。
- 過去株価: `GET /stock/{symbol}/history`
- 財務情報: `GET /stock/{symbol}/financials`
- 財務履歴: `GET /stock/{symbol}/financials/history`
  - 財務情報と財務履歴の `currency` は売上高・純利益の報告通貨です。報告通貨が不明な場合は取引通貨を使用し、どちらも不明な場合は `null` を返します。
- 企業プロフィール: `GET /stock/{symbol}/profile`
- 投資指標: `GET /stock/{symbol}/indicators`
  - D/Eレシオ（`debt_to_equity`）はパーセント単位です。たとえば `133.16` は自己資本に対する負債の比率が 133.16%（約1.33倍）であることを示します。
  - PER または PBR が 0 以下の場合、投資指標とウォッチリスト概要のミックス指数（`mix_index`）は `null` になります。
- 関連ニュース: `GET /stock/{symbol}/news`

東証銘柄は「7203 株価」のような語句で日本語ニュースを検索します。検索期間は全市場で `NEWS_PERIOD`（既定 `30d`、空欄で無期限）を使い、該当記事が0件なら期間を外して一度だけ再検索します。配信元は `publisher` で返し、既存クライアント向けの `source` にも同じ値が入ります。

`history` の主なクエリパラメータ:

| パラメータ | 説明 | 例 |
|---|---|---|
| `start_date` | 開始日 (`YYYY-MM-DD`) | `2025-01-01` |
| `end_date` | 終了日 (`YYYY-MM-DD`) | `2025-01-31` |
| `interval` | 取得間隔 | `1d`, `1wk`, `1mo` |

開始日は終了日以前にしてください。開始日だけを指定した場合は UTC の当日までの期間を検証します。`HISTORY_MAX_SPAN_YEARS` の既定は20年で、上限を超える期間は HTTP `422` です。
株価、ウォッチリスト、バックテストの銘柄は前後の空白を除いて大文字に揃えます。英数字または `^` で始め、英数字と `^ . = -` を使って20文字以内で指定してください。

`history` の各行には日付 `date`、四本値 `open`、`high`、`low`、`close`、出来高 `volume`、および次のテクニカル指標が含まれます。

| フィールド | 計算方法 |
|---|---|
| `sma_25`, `sma_50`, `sma_75`, `sma_200` | 指定本数の終値の単純移動平均 |
| `rsi_14` | 14本の RSI（Wilder 方式） |
| `macd` | 終値の EMA(12) − EMA(26) |
| `macd_signal` | MACD の EMA(9) |
| `macd_hist` | MACD − シグナル |
| `bb_upper`, `bb_middle`, `bb_lower` | 20本の終値の単純移動平均と、そこから母標準偏差（`ddof=0`）の ±2 倍に当たる上下限。バックテストのボリンジャーバンドと同じ定義 |

EMA は最初の N 本の単純平均で開始し、以降は重み `2 / (N + 1)` で更新します。週足・月足では集約後の終値で各指標を計算します。開始日を指定すると、計算に必要な過去のバーも取得し、レスポンスは指定期間に絞ります。上場間もない銘柄などで必要本数が揃わない値は `null` です。ボリンジャーバンドは20本目、MACD は26本目、シグナルとヒストグラムは34本目から値が入ります。
レスポンスでは MACD とボリンジャーバンドを小数第4位に、SMA と RSI を小数第2位に丸めます。

### 2. 為替データ

- 為替レート: `GET /forex/{pair}`
- 通貨コードを英字6文字で指定します。`USDJPY` は内部で `USDJPY=X` に補完されます。
- 為替レートはSQLiteに保存し、TTLは1時間です。キャッシュはアプリの再起動後も利用できます。

### 経済指標

`GET /market/overview?cached=false` は認証不要で、株価履歴などと同じデータ読み取りレート制限が適用されます。既定の `cached=false` では、株価・為替・日次履歴の既存キャッシュと有効期間を利用し、必要なデータを更新します。ドル円（`JPY=X`）の現在値は `GET /forex/USDJPY` とキャッシュを共有します。`cached=true` は SQLite に保存済みのデータだけを返し、有効期限を過ぎたデータも `stale: true` で返します。

レスポンスの `as_of` は生成時刻（UTC）です。`indicators` には日本、米国、為替、商品、金利・リスク、暗号資産の指標が固定順で入ります。各要素のフィールドは次のとおりです。

| フィールド | 説明 |
|---|---|
| `symbol`、`name`、`group`、`note` | Yahoo のティッカー、日本語名、分類、補足。TOPIX は連動ETF `1306.T` の価格を使用します。 |
| `unit`、`currency` | 表示単位は `points`、`jpy`、`usd`、`usd_per_oz`、`usd_per_barrel`、`yield_pct` のいずれか。通貨のない指標の `currency` は `null`。 |
| `value`、`previous_close`、`change` | 現在値、前日終値、前日比の値幅。欠損時は `null`。 |
| `change_pct` または `change_pt` | 通常は百分率、米10年国債利回り（`^TNX`）ではパーセントポイント。利回りの `value: 4.25` は 4.25% を表します。 |
| `as_of`、`sparkline` | 各指標の市場時刻（取得できなければ取得時刻）と、日次終値の最大30件（古い順）。未取得時は `null` と `[]`。 |
| `stale`、`error` | 有効期限の過ぎたデータを含むかどうかと、更新できなかったデータの説明。個別の取得失敗でも一覧は HTTP `200` で返ります。 |

経済指標の `GET /stock/{symbol}` は日本語名 `name` と表示単位 `unit` を、`GET /stock/{symbol}/history` は `unit` を追加で返します。

### 3. ユーザー・ウォッチリスト

- ユーザー登録: `POST /user/register`
- ログイン: `POST /user/login`
- パスワード変更 `POST /user/{user_id}/password`
- 全セッションの失効 `POST /user/{user_id}/logout-all`
- アカウント削除 `DELETE /user/{user_id}`
- JSONバックアップ `GET /user/{user_id}/export`、`POST /user/{user_id}/import`
- デフォルトリスト取得: `GET /user/{user_id}/default-list`
- リスト一覧/作成: `GET/POST /user/{user_id}/lists`
- リスト詳細/更新/削除: `GET/PUT/DELETE /user/{user_id}/lists/{list_id}`
- ウォッチリスト概要 `GET /user/{user_id}/lists/{list_id}/summary`
- リストのCSV出力 `GET /user/{user_id}/lists/{list_id}/export.csv`
- 銘柄追加: `POST /user/{user_id}/lists/{list_id}/items`
- 銘柄削除: `DELETE /user/{user_id}/lists/{list_id}/items/{symbol}`
- 銘柄の株数・リストの現金を設定するAPIは「ウォッチリストの保有株数と現金」を参照してください。
- タグ更新: `PUT /user/{user_id}/lists/{list_id}/items/{symbol}/tags`
- 銘柄メモ取得/保存: `GET/PUT /user/{user_id}/memo/{symbol}`

#### 認証トークン

`POST /user/register` と `POST /user/login` は JSON の `username`、`password` を受け取り、次の形式を返します。
新規登録のパスワードは8文字以上です。8文字未満の場合は HTTP `400` と `detail: "パスワードは8文字以上にしてください"` を返します。既存ユーザーのログインにはこの最小文字数を適用しません。

```json
{"id": 1, "username": "taro", "access_token": "<JWT>", "token_type": "bearer"}
```

`/user/{user_id}/...` の操作には、取得したセッション JWT を `Authorization: Bearer <JWT>` ヘッダーで送信してください。後述する読み取り専用 API では APIトークンも使えます。
未指定・不正・期限切れのトークンは `401`、トークンのユーザーとURLの `user_id` が異なる場合は `403` です。
自分の `user_id` を指定しても、他人の `list_id` は読み書きできません。メモもユーザーごとに分離されます。
株価・分析・為替・統計の取得には認証不要です。

#### 読み取り専用 APIトークン

次の管理 API は本人のセッション JWT が必要です。

| メソッドとパス | 動作 |
| --- | --- |
| `GET /user/{user_id}/api-tokens` | トークン一覧を返します。 |
| `POST /user/{user_id}/api-tokens` | `{"name":"Claude Code","expires_in_days":90}` を受け取り、`201` で作成します。 |
| `DELETE /user/{user_id}/api-tokens/{token_id}` | トークンを即時に無効化して `204` を返します。本人の一覧にない ID は `404` です。 |

`name` は前後の空白を除いて1〜50文字、`expires_in_days` は `null`（期限なし）、`30`、`90`、`365` のいずれかです。作成時に有効なトークンが10件ある場合は `409` です。作成・一覧で返すメタデータは `id`、`name`、`token_prefix`、`created_at`、`last_used_at`、`expires_at`、`expired` です。日時は UTC で、最終使用と有効期限は `null` の場合があります。作成レスポンスに限り `token` として全文を返します。トークンはアカウント削除で削除され、JSON バックアップの対象外です。

`Authorization: Bearer <APIトークン>` を使える REST エンドポイントは次のとおりです。無効化済み・期限切れのトークンは `401` です。`GET /user/me` は `{"id": 1}` の形式で認証中のユーザー ID を返します。

- `GET /user/me`
- `GET /user/{user_id}/lists`
- `GET /user/{user_id}/lists/{list_id}/summary`
- `GET /user/{user_id}/alerts`
- `GET /backtest/strategies`
- `GET /backtest/runs`
- `GET /backtest/runs/{run_id}`
- `GET /symbols/search`
- `GET /stock/{symbol}`
- `GET /stock/{symbol}/history`
- `GET /stock/{symbol}/profile`
- `GET /stock/{symbol}/indicators`
- `GET /stock/{symbol}/financials`
- `GET /stock/{symbol}/financials/history`
- `GET /stock/{symbol}/news`
- `GET /forex/{pair}`
- `POST /backtest/run`（結果の保存を伴わない単一バックテスト）

バックテストのグリッド探索、ポートフォリオ、ウォークフォワード、および保存済み結果の変更にはセッション JWT が必要です。

#### Streamable HTTP MCP

`/mcp` と `/mcp/` は APIトークンで認証する MCP の接続先です。毎回 `Authorization: Bearer <APIトークン>` を送信します。未指定、無効なトークン、セッション JWT は `401` と `WWW-Authenticate: Bearer` を返します。異なる `Origin` は `403` です。クライアントの設定手順は [MCP ガイド](MCP.md) にあります。

```bash
curl -X POST http://localhost:8000/user/login \
  -H "Content-Type: application/json" \
  -d '{"username":"taro","password":"your-password"}'

# レスポンスの access_token と id を使用
curl http://localhost:8000/user/1/lists \
  -H "Authorization: Bearer <access_token>"
```

JWTの署名方式はHS256、有効期間は既定で1440分（24時間）です。期限切れ後は再ログインしてください。
JWT の `ver` がアカウントのトークンバージョンと一致する間だけ認証できます。従来の `ver` がないトークンはバージョン 0 として扱います。失効したトークンも `WWW-Authenticate: Bearer` ヘッダー付きの `401` を返します。
`.env` の `SECRET_KEY` には、運用前に十分長いランダムな秘密値を必ず設定してください（例: `uv run python -c "import secrets; print(secrets.token_urlsafe(48))"`）。
開発用の既定値のまま起動するとWARNINGが出ます。有効期間は `ACCESS_TOKEN_EXPIRE_MINUTES` で変更できます。
パスワードはArgon2で保存し、旧SHA-256形式のパスワードはログイン成功時に自動移行します。

フロントエンドはユーザー情報とトークンをlocalStorageに保存して送信します。トークンを送信したリクエストの `401` 時は保存情報とキャッシュを消去してログイン画面を表示します。未ログイン時の認証失敗では入力内容を保持してエラーを表示します。
ログイン・ログアウト時にもGETキャッシュを消去します。

#### アカウント管理

以下の操作は所有者の Bearer トークンが必要です。認証失敗は `401`、別のユーザーへのアクセスは `403` です。

| リクエスト | JSON 本文の例 | 成功時 |
|---|---|---|
| `POST /user/1/password` | `{"current_password":"old-password","new_password":"new-password"}` | `200`。ログインと同じ形式で新しいトークンを返し、変更前のトークンをすべて失効させます。 |
| `POST /user/1/logout-all` | なし | `204`。操作に使用したトークンを含め、すべてのトークンを失効させます。 |
| `DELETE /user/1` | `{"current_password":"current-password"}` | `204`。ユーザー、リスト（株数・現金を含む）、タグ、メモ、価格アラート、保存済みバックテストをまとめて削除します。 |

パスワードの不一致や新しいパスワードの文字数不足は日本語の `detail` を含む `400`、必須項目の欠落や型違いは `422` です。現在のパスワードを検証する操作はログインと同じ失敗回数・IP 試行回数の制限を使い、上限到達時に `429` を返します。

#### リストの管理と概要

`POST /user/1/lists` と `PUT /user/1/lists/2` は `{"name":"配当株"}` を受け取り、`200` で `id`、`name`、`created_at`、`updated_at` を返します。名前の前後の空白を除き、1〜50文字を受け付けます。範囲外は `400`、同じユーザーの別のリストと名前が一致する場合は `409` です。

`GET /user/1/lists` は `lists` と `default_list_id` を返します。デフォルトは最も早く作成されたリストです。デフォルトリスト（最後の1件を含む）を `DELETE /user/1/lists/2` で削除しようとすると `400` です。ほかのリストを削除すると `200` と `{"message":"リストを削除しました"}` を返します。対象が見つからない場合は `404` です。

#### ウォッチリストの保有株数と現金

| メソッドとパス | 本文と応答 |
|---|---|
| `PUT /user/{user_id}/lists/{list_id}/items/{symbol}/shares` | `{"shares":1.2345}`。0より大きく10億以下で、小数第4位まで。消去は `{"shares":null}` |
| `PUT /user/{user_id}/lists/{list_id}/cash` | `{"cash_jpy":50000}`。0円以上10兆円以下の整数。消去は `{"cash_jpy":null}` |

更新できた場合は保存した値を `200` で返します。対象のリストや銘柄が見つからなければ `404`、入力が不正なら日本語のメッセージと `422` を返します。

`GET /user/1/lists/2/summary` は、リストの `cash_jpy`、銘柄ごとの `shares`、`value_jpy`（円換算評価額）、`weight_pct`（現金を含む合計に対する比率）、タグ、メモとマーケットデータを返します。株数が `null` の銘柄はウォッチ対象です。全銘柄の株数が未入力で現金が `null` または `0` なら `portfolio` は `null` です。株数か正の現金があれば、以下を `portfolio` に返します。

| フィールド | 内容 |
|---|---|
| `total_value_jpy`、`stocks_value_jpy`、`cash_jpy`、`cash_ratio_pct` | 円換算合計、株式評価額、現金（未入力は集計上0円）、現金比率 |
| `by_region` | 日本株（JPY建て）、米国株（USD建て）、その他、現金の順で `label`、`value_jpy`、`pct` |
| `by_currency` | JPY、USD の順で `label`、`value_jpy`、`pct`。現金は JPY に含む |
| `by_sector` | 評価額の大きい順の `label`、`value_jpy`、`pct`。比率は評価できた株式が分母。企業プロフィールのセクターがなければ「不明」 |
| `concentration` | 最大保有銘柄 `largest`（`symbol`、`pct`）、上位5銘柄の合計比率 `top_5_pct`、除外も含む保有銘柄数 `holding_count` |
| `usd_jpy` | 使用した為替の `rate`、`stale`、`cached_at`。使用可能なレートがなければ `null` |
| `excluded` | 株価、取引通貨、為替の欠損などで集計から除外した `symbol` と `reason` |

JPY建ては株数×株価、USD建ては株数×株価×USD/JPYで評価します。その他の通貨は `value_jpy` と `weight_pct` が `null` になり、`warning` を返します。構成比と集中度の分母は、評価できた株式と現金の合計です。分母が0の比率は `null` です。評価できた株式が0件の場合も、最大保有銘柄と上位5銘柄比率は `null` です。取得原価、取得日、損益は設計上管理対象外です。

`?cached=true` を付けるとDBのキャッシュだけを読みます。期限切れのデータは `stale: true` と `cached_at` 付きで返し、株価・企業プロフィール・投資指標のいずれかのキャッシュがなければ `pending: true` を返します。キャッシュのない項目は `null` です。通常の概要取得では銘柄に応じて外部取得を試み、一部の失敗は各行の `error` に表示します。通常取得のデータ取得枠の消費は銘柄数にかかわらず1回です。 `WATCHLIST_SUMMARY_CONCURRENCY`（既定値8）で1リクエスト内の同時取得銘柄数を設定します。

`GET /stock/{symbol}/snapshot` は株価・会社情報・投資指標を並行取得し、ダッシュボード用の項目をまとめて返します。部分取得時も `200` を返し、取得失敗の項目は省略して `error` に日本語メッセージを入れます。ダッシュボードはキャッシュの概要を描画してからこのAPIで銘柄を順次更新し、円換算の集計はキャッシュの概要を再取得して反映します。

旧 `/user/{user_id}/portfolio` 配下のAPIは廃止しました。マイグレーション `0007` は旧保有銘柄を各ユーザーのデフォルトリストに追加するか、登録済み銘柄の株数に合算します。株数は旧データの精度を保ったまま移行します。旧保有銘柄の取得単価、取得日、保有メモを破棄してテーブルを削除します。ダウングレードでは旧テーブルを空で再作成し、新しい株数・現金列を削除します。


#### JSONバックアップとCSV出力

`GET /user/1/export` は `200` と `Content-Disposition: attachment; filename="watchlists-2026-11-25.json"` を返します。ファイル名の日付は UTC です。JSON にはリスト、銘柄、タグ、メモ、保有銘柄、価格アラート、保存済みバックテストが含まれます。

```json
{
  "version": 4,
  "exported_at": "2026-11-25T12:00:00Z",
  "username": "taro",
  "lists": [{
    "name": "配当株",
    "cash_jpy": 50000,
    "items": [{"symbol": "7203.T", "shares": 10, "tags": ["自動車"], "added_at": "2026-11-25T08:00:00Z"}]
  }],
  "memos": [{"symbol": "7203.T", "memo": "決算を確認", "updated_at": "2026-11-25T09:00:00Z"}],
  "alerts": [{"symbol": "7203.T", "kind": "above", "threshold": 3000, "enabled": true, "note": null,
    "created_at": "2026-11-25T10:00:00Z", "last_triggered_at": null, "last_triggered_market_date": null,
    "trigger_count": 0, "armed": true}],
  "backtest_runs": []
}
```

追加日時を記録する前から存在した銘柄の `added_at` は `null` です。銘柄メモはリストに入っていない銘柄も含めます。

`POST /user/1/import?mode=merge` の本文には、エクスポートした JSON をそのまま指定できます。データはURLのアカウントに取り込みます。本文の `username` はバックアップ元の記録です。

- `merge`（既定）は未登録のデータを追加します。同じリスト名の内容を統合し、既存の空でないメモを保持します。既存メモが空（空白のみを含む）の場合、取り込むメモが空でなければ更新します。バージョン4の株数と現金は未入力欄に取り込み、設定済みの値を保持します。アラートはティッカー、条件、基準値、有効状態、メモが一致する件数を照合し、既存の通知履歴を保持します。
- `replace` はバックアップに含まれる種類のデータを置き換えます。バージョン1はリストとメモ、バージョン2からは価格アラート、バージョン3からは保存済みバックテストも対象です。どちらのモードも1回のトランザクションで確定します。
- バージョン2・3の旧 `holdings` はデフォルトリストに取り込み、同じティッカーの株数を合算します。旧取得単価・取得日・保有メモは破棄します。同じ旧バックアップを複数回 `merge` するとその都度株数が加算されます。
- 銘柄の表記を正規化します。リストや銘柄メモの項目が銘柄形式やリスト名の条件を満たさない場合は、理由付きでスキップします。価格アラートの件数には登録上限が適用されます。
- 保存済みバックテストは名前、種類、作成日時、メモ、リクエスト、結果が一致する項目を照合します。取り込み時に新しいIDを採番し、バックテストの保存件数・サイズの上限を適用します。

```json
{
  "imported": {"lists": 1, "items": 1, "tags": 1, "memos": 1, "holdings": 1, "alerts": 1, "backtest_runs": 0},
  "skipped": [{"entry": "配当株 / A/B", "reason": "symbol は英数字または ^ . = - で20文字以内にしてください"}]
}
```

上記は `200` の応答例です。`USER_IMPORT_MAX_BYTES`（既定値 `33554432`、正の整数）を超える本文は `413` です。JSON の構文、必須項目、型、バージョン、モードが正しくない場合や、定義外の項目がある場合は `422` です。

`GET /user/1/lists/2/export.csv` は `200` と `Content-Disposition: attachment; filename="watchlist-2-2026-11-25.csv"` を返します。UTF-8 BOM付きで、列は `symbol,name,tags,memo,shares` の順です。企業名はキャッシュにあれば出力します。表計算ソフトで数式として解釈される値には、引用符の内側にタブを付けます。リストが見つからない場合は `404` です。

```csv
"symbol","name","tags","memo","shares"
"7203.T","Toyota","自動車, 高配当","決算を確認","10.0"
```

#### 価格アラート

| メソッドとパス | 操作 |
|---|---|
| `GET /user/{user_id}/alerts` | アラート一覧を返す |
| `POST /user/{user_id}/alerts` | アラートを追加する（`201`） |
| `PUT /user/{user_id}/alerts/{alert_id}` | アラートの条件や有効状態を更新する |
| `DELETE /user/{user_id}/alerts/{alert_id}` | アラートを削除する（`204`） |
| `POST /user/{user_id}/alerts/check` | 有効なアラートを判定して `fired` と `skipped` を返す |

作成・更新では `symbol`、`kind`（`above`、`below`、`change_pct`）、`threshold`（0より大きい数値）を指定します。`enabled` の既定値は `true` で、`note` は2,000文字以内です。`above` は価格が基準値以上、`below` は基準値以下、`change_pct` は当日の騰落率の絶対値が指定したパーセント以上で成立します。`PRICE_ALERT_MAX_PER_USER`（既定値100件）到達時は `400` です。

成立時は `last_triggered_at` と `trigger_count` を更新し、判定値 `observed_value` 付きで `fired` に返します。`above` と `below` では、`ALERT_COOLDOWN_MINUTES`（既定値60分）の間は再通知を抑制します。一度発火すると `armed` が解除され、価格が反対側に戻ったことを次回以降の判定で確認すると再設定されます。銘柄・条件・基準値の変更でも `armed` を再設定し、クールダウンは直近の通知時刻から続きます。

`change_pct` は銘柄の取引日につき最大1回通知し、通知した取引日を `last_triggered_market_date` に保存します。提供元の市場時刻を取引所のタイムゾーンで日付に変換します。タイムゾーンが得られない場合は市場時刻のUTC日付を使用し、市場時刻が得られない場合は判定時点のUTC日付につき最大1回通知します。休場中も同じ市場時刻の株価が返る限り再通知しません。銘柄を変更すると記録済みの取引日を解除します。

キャッシュ代用の古い株価、取得に失敗した株価、判定値が欠けた株価は理由付きで `skipped` に返します。この場合、通知状態を更新しません。判定はチェックAPIの呼び出し時に実行します。

両機能で、他のユーザーのURLへのアクセスは `403`、本人のURLから所有していない項目を更新・削除すると `404` です。評価とアラート判定はそれぞれデータ取得枠を1回消費し、上限時は `429` を返します。銘柄の同時取得数は `WATCHLIST_SUMMARY_CONCURRENCY`（既定値8）で制限します。

保有銘柄とアラートを追加するときや、更新でティッカーを変更するときは株価サービスで銘柄を確認します。銘柄が見つからない場合は `404` です。提供元の一時的な障害や利用上限で確認できない場合は保存できます。

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
| `RATE_LIMIT_DATA_REQUESTS` | `600` | 銘柄検索・株価・分析・為替・ウォッチリスト概要・バックテストの共通リクエスト上限 |
| `RATE_LIMIT_DATA_WINDOW_SECONDS` | `60` | データリクエストを数える期間（秒） |

回数・期間は正の整数を指定してください。データ取得ルートとバックテストの実行・探索ルートは、有効なトークンがあればユーザー ID、なければ接続元 IP で同じ枠を使用します。キャッシュから応答した場合も枠を消費します。プロキシ経由で接続元 IP を判定するには `TRUSTED_PROXIES` を設定してください。接続元が取得できない場合は共通の `unknown` キーを使います。 [デプロイ手順](DEPLOY.md)

既定の60秒あたり600リクエストは乱用防止の上限です。ウォッチリストの再取得時に HTTP `429` が返った場合は、表示中の項目を保持します。
状態はロックで保護したプロセス内メモリに保持し、リクエストと認証完了時に期限切れの時刻・空キーを削除します。再起動でリセットされ、複数ワーカー間では共有されません。並行して検証中のログインはまだ失敗に数えず、IP試行上限が同時実行数も制限します。

### 4. 統計・キャッシュ

- 利用統計: `GET /stats` で API 呼び出し回数やキャッシュヒット情報を取得
- SQLite を使ったキャッシュで外部APIアクセスを最適化

キャッシュはテーブルごとに `CACHE_RETENTION_DAYS`（既定30日）と「そのテーブルで最長のTTL + `CACHE_STALE_MAX_DAYS`」（既定の猶予は7日）の長い方を保持期間とします。データ提供元の通信障害、レート制限、5xx応答時に猶予内のキャッシュを返す場合は、`stale: true` と取得日時 `cached_at` を付けます。

データ提供元の失敗を HTTP `502`、`503`、`429` で返す場合、応答には日本語の案内が入り、エラーの詳細はリクエストID付きのサーバーログに記録されます。

`GET /stats` は認証不要です。従来の `api_calls`、`cache_hits`、`total_requests`、`hit_rate_percent`、`server_requests`、`server_errors`、`error_rate_percent`、`avg_response_time_ms`、`top_endpoints`、`uptime_seconds` に加え、次の項目を返します。

| フィールド | 内容 |
|---|---|
| `today` | UTC当日の `date`、`hits`、`errors`（HTTP 400以上）、`total_ms`（総応答時間） |
| `last_7_days` | 当日を含む7日分の日次集計。UTC日付の昇順で、アクセスのない日も0で埋めます |
| `cache_hit_rate` | 既存カウンターから算出した `cache_hits / (cache_hits + api_calls)`（0～1）。取得実績が0件の場合は `null` |

日次集計は保存済みの値と今回のプロセスで未保存の増分を合算します。集計を返す `GET /stats` 自体は次回の集計から反映されます。`/`、`/health`、`/static/` の配信リクエストは集計から除外します。その他のアクセス先は `/stock/{symbol}/history` などのルートテンプレートで集計され、テンプレートのないURLは `unmatched` にまとめられます。増分は `STATS_FLUSH_SECONDS`（既定60秒）ごとと正常終了時に保存し、90日より古い日次行を削除します。

`GET /health` は認証不要でレート制限の対象外です。データベース接続を確認し、正常時は HTTP `200`、接続失敗時は HTTP `503` を返します。応答に `status`、`version`、`uptime_seconds`、`database`、`migration` が含まれます。

全応答に `X-Request-ID` が付きます。リクエスト側で `A–Z`、`a–z`、数字、`._-` を使った64文字以内の値を送れば同じIDが返ります。5xxを調べるときは応答のIDでサーバーログを検索してください。

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
応答の `equity_curve` は比較に使う首位の設定の曲線です。

#### ポートフォリオとウォークフォワード

| エンドポイント | リクエスト | 応答の追加項目 |
|---|---|---|
| `POST /backtest/portfolio` | 単一実行の `symbol` を `symbols`（重複のない2〜10銘柄の配列）に置き換え、`allocation`（`equal` または `equal_active`）、`rebalance_every`（正のバー数または `null`）、`rebalance_threshold_pct`（目標比率に対する許容乖離率、既定5）を指定 | `contributions` に銘柄別損益寄与、曲線各行の `holdings` に銘柄別評価額、取引の `reason` に `signal` または `rebalance` |
| `POST /backtest/walkforward` | グリッド探索の項目に `folds`（2〜50、既定3）と `window`（`rolling` または `anchored`）を追加 | `folds` に学習・検証の期間、選択パラメータ、IS指標、OOS指標、先頭候補を使用したときの `warning`。`robustness` に利益が出たOOS分割の割合と指標の平均悪化幅 |

両方の応答に `metrics`、`benchmark_metrics`、`trades`、`warnings`、`equity_curve` が含まれます。グリッドと両モードの曲線は最大2000点、約定は最大5000件です。ポートフォリオの履歴は最多3銘柄を並行取得し、取得できない場合は銘柄名を含む `422` を返します。判明した通貨が複数ある場合は通貨名と銘柄を含む `422`、通貨が不明な場合は警告を返します。ポートフォリオの決済統計はシグナルによる売りを対象とします。ウォークフォワードの曲線と全体指標はOOS期間を連結して計算します。 [計算方法とCLI](backtestCLI.md)

#### 保存済みバックテスト

Bearerトークンで保存したアカウントの結果を操作します。

| 操作 | リクエスト・応答 |
|---|---|
| `POST /backtest/runs` | `kind`（`single`、`grid`、`portfolio`、`walkforward`）、`request`、`result`、任意の `name` と `note` を指定。成功時は `201` と概要 |
| `GET /backtest/runs` | `runs` に新しい順の概要一覧 |
| `GET /backtest/runs/{id}` | 概要に検証済みの `request` と保存用の `result` を加えた詳細 |
| `PATCH /backtest/runs/{id}` | `name` または `note` を更新し、概要を返す。メモの消去は `{"note":null}` |
| `DELETE /backtest/runs/{id}` | 削除して `204` |

名前は最大120文字、メモは最大2000文字です。名前を省略すると、単一実行は銘柄・戦略・作成日、グリッド探索とウォークフォワードはさらに種類を含めて生成します。ポートフォリオは種類・銘柄数・戦略・作成日を使います。保存用曲線は最大500点、取引は先頭1000件です。保存後の結果の上限は512 KiBで、超過は `413`、件数超過は `409` です。別の利用者のIDや存在しないIDは `404` です。指標は実行応答からクライアントが送った値で、`metrics_source: "client"` がその出所を示します。

| 環境変数 | 既定値 | 動作 |
|---|---:|---|
| `BACKTEST_MAX_SPAN_YEARS` | 20 | 開始日からの最大期間（暦年） |
| `BACKTEST_GRID_MAX_COMBOS` | 100 | グリッドの組み合わせ上限（設定可能範囲1～500） |
| `BACKTEST_TIMEOUT_SECONDS` | 30 | データ取得と計算を合わせた実行上限（秒） |
| `BACKTEST_MAX_CONCURRENT_PER_USER` | 1 | 1プロセス内のユーザーごとの同時実行上限。4つの実行・探索ルートで共有 |
| `BACKTEST_MAX_SAVED_RUNS` | 50 | ユーザーごとの保存済みバックテストの件数上限 |

認証エラーはHTTP `401`、入力や組み合わせ上限の違反は `422`（`detail` に検証エラー）、同時実行上限は `429`（`Retry-After` 付き）、時間超過は `504` です。

```json
{"detail": "バックテストが制限時間を超えました。期間や条件を見直してください。"}
```

## APIドキュメントUI

- OpenAPI 定義: `http://localhost:8000/openapi.json`

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
