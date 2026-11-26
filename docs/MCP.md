# Claude から tera-quant-lab を使う

MCP サーバーを設定すると、Claude Desktop や Claude Code から、ウォッチリストと保有資産の構成、価格アラート、株価、財務、ニュース、為替レートを取得できます。バックテスト戦略の確認、保存済み結果の参照、その場でのバックテスト計算にも対応しています。読み取り専用で、保有銘柄や設定の変更、バックテスト結果の保存はできません。

## 前提

- tera-quant-lab のアプリを起動してください。既定の接続先は `http://127.0.0.1:8000` です。
- Claude を使う PC にリポジトリと `uv` が必要です。リポジトリで `uv sync` を実行しておいてください。MCP SDK は開発用依存グループに入っているため、`uv sync --no-dev` ではインストールされません。
- ウォッチリスト、保有銘柄、価格アラート、バックテストの実行・保存済み結果には、アプリのユーザー名とパスワードが必要です。公開銘柄データや戦略一覧だけを使う場合は資格情報を省略できます。

## Claude Desktop

Windows では `%APPDATA%\Claude\claude_desktop_config.json` に次の設定を追加します。既存の `mcpServers` があれば、その中に `tera-quant-lab` の項目を追加してください。パスと資格情報は環境に合わせて置き換えます。

```json
{
  "mcpServers": {
    "tera-quant-lab": {
      "command": "uv",
      "args": ["--directory", "C:\\path\\to\\tera-quant-lab", "run", "python", "-m", "tera_mcp"],
      "env": {
        "TQL_BASE_URL": "http://127.0.0.1:8000",
        "TQL_USERNAME": "your-username",
        "TQL_PASSWORD": "your-password"
      }
    }
  }
}
```

設定後に Claude Desktop を再起動してください。

## Claude Code

PowerShell での登録例です。パスと資格情報を置き換えて実行します。

```powershell
claude mcp add --transport stdio tera-quant-lab -e 'TQL_BASE_URL=http://127.0.0.1:8000' -e 'TQL_USERNAME=your-username' -e 'TQL_PASSWORD=your-password' -- uv --directory 'C:\path\to\tera-quant-lab' run python -m tera_mcp
```

登録後に `claude mcp list` で確認できます。バックテスト以外の HTTP タイムアウトは既定で30秒です。変更する場合は、環境変数 `TQL_TIMEOUT_SECONDS` に秒数を指定します。バックテストの HTTP タイムアウトは少なくとも120秒です。

## Proxmox LXC のアプリにつなぐ

`TQL_BASE_URL` を Claude を使う PC からアクセスできる LXC 上のアプリの URL に変更します。可能であれば HTTPS を使ってください。MCP サーバーは Claude を使う PC 側で動き、設定した URL に HTTP でアクセスします。

## 資格情報とデータの扱い

上記の例ではアプリのパスワードが Claude の設定ファイルに平文で保存されます。Claude Code の登録コマンドを直接入力すると、シェルの履歴にも残る場合があります。資格情報を含む設定やコマンドの共有には注意してください。ツール呼び出しで取得したアプリのデータ（保有銘柄やメモなど）は Claude（Anthropic）に送られます。MCP のツールは読み取り専用です。

## ウォッチリストの保有資産

株数や円の現金を入力したウォッチリストは、`get_watchlist` で評価額と構成比を確認できます。リストIDを省略するとデフォルトリストを参照します。別のリストを指定するには `list_watchlists` でIDを確認します。

`items` に株数、円換算評価額、現金を含む合計に対する比率、タグ、メモが含まれます。`portfolio` に株式と現金の合計、地域・通貨・セクターの構成、最大保有比率、上位5銘柄比率、USD/JPY、集計から除外した銘柄が含まれます。資産を未入力のリストでは `portfolio` は `null` です。取得原価と損益は設計上管理対象外です。

`get_portfolio` は廃止しました。旧保有銘柄はアプリ起動時にデフォルトリストへ移行されます。ブラウザーはキャッシュを先に表示し、銘柄情報を順次更新します。MCPでは取得時点を把握するために `stale` と `cached_at`、評価範囲を把握するために `excluded` を確認してください。

## 質問例

- 「ポートフォリオを見て、銘柄や通貨の集中と評価額の気になる点を挙げて」
- 「HPQ の売上高、純利益、EPS の推移を年度ごとに要約して」
- 「既定のウォッチリストで、PER・PBR と配当利回りを比較して。データの取得日時も示して」
- 「日経平均（`^N225`）の直近1年の騰落率と、期間中の高値・安値の日付を教えて」
- 「9433.T の直近ニュースを5件、発行元と公開日時付きでまとめて」
- 「HPQ の2024年から2025年の移動平均クロス戦略を計算し、ベンチマークと最大ドローダウンを比較して」

## トラブルシューティング

- **アプリに接続できない**場合は、アプリが起動していることと、Claude を使う PC から `TQL_BASE_URL` にアクセスできることを確認します。LXC の場合はアプリの待ち受けアドレスとファイアウォールも確認してください。
- **HTTP 401 が返る**場合は、アプリにログインできる資格情報が `TQL_USERNAME` と `TQL_PASSWORD` に設定されているか確認し、Claude を再起動します。セッション切れの場合、MCP サーバーは自動で一度再ログインします。
- **ツールが表示されない**場合は、リポジトリのパス、Claude から起動できる `uv` のパス、開発用依存のインストール状況を確認してください。`uv` が PATH に見つからなければ、設定の `command` に `uv.exe` の絶対パスを指定します。
