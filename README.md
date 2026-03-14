# Stock Data API

株価分析・アルゴリズム研究のための REST API。

## 機能

- **株価取得**: 現在価格・過去データ（yfinance）
- **財務情報**: 売上、純利益、EPS、PE比率
- **ニュース**: 関連ニュースの検索（GNews）
- **キャッシュ**: SQLite による自動キャッシュ

## セットアップ

```bash
# 依存パッケージのインストール
pip install -r requirements.txt

# 環境変数の設定（任意）
cp .env.example .env

# サーバー起動
python -m uvicorn app.main:app --reload
```

## API エンドポイント

| メソッド | パス | 説明 |
|---------|------|------|
| GET | `/stock/{symbol}` | 現在の株価 |
| GET | `/stock/{symbol}/history` | 過去の株価データ |
| GET | `/stock/{symbol}/financials` | 財務情報 |
| GET | `/stock/{symbol}/news` | 関連ニュース |

### クエリパラメータ（history）

| パラメータ | 説明 | 例 |
|-----------|------|---|
| `start_date` | 開始日 | `2025-01-01` |
| `end_date` | 終了日 | `2025-01-31` |
| `interval` | データ間隔 | `1d`, `1wk`, `1mo` |

## API ドキュメント

サーバー起動後、以下のURLでSwagger UIにアクセスできます:

```
http://localhost:8000/docs
```

## 使用例

```bash
# 現在株価
curl http://localhost:8000/stock/AAPL

# 過去株価
curl "http://localhost:8000/stock/AAPL/history?start_date=2025-01-01&end_date=2025-01-31"

# 財務情報
curl http://localhost:8000/stock/AAPL/financials

# ニュース
curl http://localhost:8000/stock/AAPL/news
```
