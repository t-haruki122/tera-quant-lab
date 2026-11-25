# FRONTEND Guide

プロジェクト全体説明へ戻る: [README.md](../README.md)

## 概要

フロントエンドはビルドステップなしの ES Modules 構成です。`index.html` では `type="module"` で `frontend/app.js` を読み込み、各機能モジュールを `import` して構成しています。

## 主要ファイル

- エントリポイント: `frontend/app.js`
- APIヘルパー: `frontend/js/api.js`
- 共有状態: `frontend/js/state.js`
- 詳細表示: `frontend/js/detail.js`
- 価格チャート: `frontend/js/chart.js`
- 財務チャート: `frontend/js/financial-chart.js`
- リスト管理: `frontend/js/list.js`
- 認証UI: `frontend/js/auth.js`
- アカウント設定 `frontend/js/account.js`
- 統計表示: `frontend/js/stats.js`
- ユーティリティ: `frontend/js/utils.js`

## 実行方法

バックエンドを起動すると、ルート (`/`) で `frontend/index.html` が配信されます。

```bash
uv run uvicorn app.main:app --reload --reload-dir app
```

ブラウザ: `http://localhost:8000/`

## ウォッチリスト

ログイン後、銘柄ダッシュボードのセレクターから表示するリストを選びます。リストを新規作成し、選択中のリストを名前変更できます。削除は確認後に実行し、デフォルトリストでは削除ボタンが無効になります。最後に選んだリストをユーザーごとに localStorage に保存します。

リストの新規作成、名前変更、削除確認、銘柄メモの編集は画面内のダイアログで行います。Esc で閉じ、Enter で送信できます。メモ欄では Shift＋Enter で改行します。

リストの銘柄情報とメモは概要APIで一括取得します。取得できなかった項目がある行にはエラーを表示し、取得済みの値も表示します。タグ絞り込み、`#タグ` 検索、列の並べ替えは選択中のリストに適用されます。「CSVをエクスポート」から選択中のリストを保存できます。

## アカウント設定

ログイン後にユーザーメニューの「アカウント設定」を開きます。

- パスワード変更では現在のパスワードと新しいパスワードを入力し、確認欄との一致を画面でチェックします。変更後は新しいトークンで操作を続けられます。
- 「他の端末からログアウト」は、このブラウザーを含むすべてのセッションを終了します。
- 「JSONをエクスポート」で全リストとメモを保存します。インポートではファイルを選び、既存データへの追加か、確認後にすべて置き換える方法を選びます。処理後に取り込み件数とスキップ理由を表示します。
- アカウント削除にはユーザー名の再入力と現在のパスワードが必要です。削除後はログアウトします。

## メモ

- HTML の inline handler (`onclick` / `onchange`) ではなく、JSモジュール側のイベント登録に統一しています。
