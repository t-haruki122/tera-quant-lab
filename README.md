# Stock Data Platform

株式分析・為替確認・ウォッチリスト管理をまとめて扱える、FastAPI + シンプルなフロントエンドで構成された投資支援アプリです。

## このプロジェクトの目的

このアプリは、以下の目的で作成しています。

- ポートフォリオ管理＆ダッシュボード
- 投資戦略のバックテスト用ソフトの土台

## できること（概要）

- 株価・企業情報・財務情報・投資指標・ニュースの取得
- 為替レートの取得
- ユーザー登録/ログインとウォッチリスト管理
- API利用統計の確認
- Python完結型CLIによるバックテスト実行

## ドキュメント構成

- フロントエンド詳細: [docs/FRONTEND.md](docs/FRONTEND.md)
- バックエンドAPI詳細: [docs/API.md](docs/API.md)
- バックテストCLI詳細: [docs/backtestCLI.md](docs/backtestCLI.md)

## セットアップ

パッケージ管理には [uv](https://docs.astral.sh/uv/) を使います。

```bash
# uv のインストール（未導入の場合）
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# 仮想環境 (.venv) の作成と依存パッケージ・開発ツールのインストール
uv sync

# サーバー起動
uv run uvicorn app.main:app --reload --reload-dir app
```

`uv run` はプロジェクト直下の `.venv` を自動で使うため、仮想環境の有効化（activate）は不要です。
`uv sync` は pytest・ruff などの開発ツールもインストールします。

```bash
uv run pytest
uv run ruff check
# フロントエンド検証（Node.js 22、追加のnpm依存なし）
node --test "tests/frontend/**/*.test.mjs"
```

フロントエンドの `.js` は ES Modules です。Node.js 22.17 以降は構文を自動検出できるため、
`package.json` や追加ローダーなしで上記コマンドを実行できます。
`tests/frontend/index.js` がディレクトリ指定の実行入口になり、`.test.mjs` を読み込みます。
テストのDOM shimはブラウザー向けの描画文字列を検証します。

pip を使う場合も `pip install -r requirements.txt` で導入できます。
`requirements.txt` は `uv.lock` から生成されるファイルです。依存関係は `pyproject.toml` で管理します。

設定は `.env.example` を参考に `.env` で指定します。フロントエンドはAPIと同一オリジンで配信されるため、`CORS_ORIGINS` は既定で空欄（CORSミドルウェアなし）です。
別オリジンのフロントエンドを使う場合だけ `CORS_ORIGINS=http://a.example, http://b.example` のように許可先を指定してください。JSON配列も使え、前後の空白と空要素は除去します。
認証はBearerヘッダーを使い、Cookie等のCORS資格情報は許可しません。許可メソッドはGET/POST/PUT/DELETE/OPTIONS、指定可能なヘッダーはAuthorizationとContent-Typeです。`*` と資格情報の併用は拒否します。

ログイン・登録のレート制限は既定で有効です。`RATE_LIMIT_*` 設定と429応答の詳細は [APIガイド](docs/API.md) を参照してください。

## アクセス先

- アプリ: `http://localhost:8000/`
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

## 画面イメージ

### 銘柄リスト比較
![銘柄リスト比較](docs/images/ScreenShots_2.png)

### 詳細ビュー
![詳細ビュー](docs/images/ScreenShots_1.png)

### システム統計
![システム統計](docs/images/ScreenShots_3.png)

## データベースのマイグレーション

起動時に Alembic の `upgrade head` が自動実行されます。従来の `create_all` で作成したDBも移行できます。キャッシュの重複は最新の取得日時（同時刻なら最大ID）を残して整理し、ユーザーデータは保持します。

開発時の手動適用: `uv run alembic upgrade head`

モデル変更時のリビジョン作成: `uv run alembic revision --autogenerate -m "変更内容"`

`CACHE_RETENTION_DAYS`（既定30日）より古いキャッシュを起動時と書き込み時（プロセス内で最大1時間に1回）に削除します。ユーザー関連テーブルは削除対象に含みません。為替キャッシュと同時取得の共有はプロセス内に限られます。
