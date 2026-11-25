# Backtest CLI Guide

プロジェクト全体説明へ戻る: [README.md](../README.md)

## 概要

Pythonだけで完結するバックテストCLIです。
ローカルで稼働中の `py_stock_data_api`（既定: `http://localhost:8000`）から株価や財務データを取得し、戦略検証をターミナルで実行します。

## 実行前提

- Python 3.12以上
- FastAPIサーバー起動済み
- [uv](https://docs.astral.sh/uv/) で依存パッケージ導入済み（`uv sync`、詳細は [README.md](../README.md#セットアップ)）

## デスクトップGUI（実装中）

CLIロジックを利用するデスクトップGUIを追加しました。

```bash
uv run python scripts/backtest_gui.py
```

現時点の対応範囲:

- 単一銘柄のバックテスト実行
- 非同期実行（UIをブロックしない）
- 実進捗バー（0-100%）表示
- キャンセル要求
- メトリクス表示
- チャート表示
- 取引詳細テーブル表示（日時/売買/価格/株数/手数料/損益/取引後現金/取引後総資産）
- 取引履歴CSV保存
- チャートPNG保存

GUIは単一銘柄のみ対応しており、認証情報は不要です。パスワードの対話入力も行いません。

## 実行例

```bash
# 単一銘柄で実行
uv run python scripts/backtest_cli.py \
  --symbol AAPL \
  --start-date 2023-01-01 \
  --end-date 2024-12-31 \
  --interval 1d \
  --initial-capital 1000000 \
  --short-window 20 \
  --long-window 60 \
  --fee-rate 0.001

# ウォッチリストで実行（ユーザーIDはログイン結果から取得）
uv run python scripts/backtest_cli.py \
  --username taro \
  --watchlist-id 2 \
  --start-date 2023-01-01 \
  --end-date 2024-12-31 \
  --interval 1d \
  --initial-capital 1000000
```

## 主なオプション

- `--symbol`: 単一銘柄シンボル（例: `AAPL`）
- `--watchlist-id`: ウォッチリストID（`--username` または `--user-id` が必要）
- `--user-id`: ウォッチリスト取得用ユーザーID。`--username` 指定時は省略可、`--token` / `TQL_TOKEN` 使用時は必須
- `--username`: ログインユーザー名（ウォッチリスト用）。パスワードは環境変数 `TQL_PASSWORD`、未設定時はgetpassによる非表示入力
- `--token`: 取得済みのJWT（`--username` と同時指定不可）。未指定時は環境変数 `TQL_TOKEN` を使用
- `--start-date`, `--end-date`: 検証期間（`YYYY-MM-DD`）
- `--interval`: データ間隔（`1d`, `1wk`, `1mo`）
- `--initial-capital`: 初期資金
- `--fee-rate`: 手数料率（例: `0.001` = `0.1%`）
- `--short-window`, `--long-window`: SMA期間
- `--max-per`: PER上限フィルター
- `--fetch-financial-history`: 財務履歴も取得
- `--trade-csv`: 取引履歴CSVの保存先（既定: `trade_history.csv`）
- `--save-chart`: 売買ポイントと資産推移チャートを保存
- `--chart-file`: チャート画像ファイル名（既定: `result_chart.png`）
- `--show-chart`: チャートを画面表示
- `--base-url`: APIベースURL（既定: `http://localhost:8000`）
- `--timeout`: APIタイムアウト秒
- `--max-retries`: API再試行回数

## ウォッチリストの認証

ウォッチリスト取得には本人のBearerトークンが必要です。`--username` を指定すると `/user/login` にログインし、以後のAPIリクエストに `Authorization: Bearer <access_token>` を付けます。
`--username` を明示した場合は環境変数 `TQL_TOKEN` よりログインを優先します。
`--user-id` を省略するとログインレスポンスの `id` を使用します。両方を指定し、ログインユーザーのIDと異なる場合はエラーになります。
単一銘柄の `--symbol` モードでは認証もパスワード入力も不要です。

Windows PowerShellでの例:

```powershell
# パスワードは環境変数から取得（省略すると非表示で入力を要求）
$env:TQL_PASSWORD = 'your-password'
uv run python scripts/backtest_cli.py --username taro --watchlist-id 2 --start-date 2023-01-01 --end-date 2024-12-31
Remove-Item Env:TQL_PASSWORD

# /user/login または /user/register の access_token を再利用
$env:TQL_TOKEN = '<access_token>'
uv run python scripts/backtest_cli.py --user-id 1 --watchlist-id 2 --start-date 2023-01-01 --end-date 2024-12-31
Remove-Item Env:TQL_TOKEN

# --token '<access_token>' でも指定可能
```

`--token` / `TQL_TOKEN` 使用時は `--user-id` に認証ユーザーのIDを指定してください。他人のIDは `403`、不正・期限切れトークンは `401` になります。
`429` とサーバーエラー（`5xx`）、タイムアウト・接続エラーは再試行します。それ以外の `4xx` は待機・再試行せずエラーを返します。`401` / `403` ではサーバーの `detail` があれば表示します。
JWTは既定で24時間有効です。期限切れ時は再ログインして新しいトークンを取得してください。
登録時のパスワードは8文字以上です（既存ユーザーのログインは旧パスワードのまま可能）。

## 出力

- コンソール: 最終資産額、リターン率、勝率、最大ドローダウン等
- CSV: 取引履歴（`date`, `side`, `price`, `shares`, `pnl` など）
- 画像（任意）: 売買ポイント付き価格チャートと資産推移
