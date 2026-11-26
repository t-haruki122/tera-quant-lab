# Backtest CLI Guide

プロジェクト全体説明へ戻る: [README.md](../README.md)

## 概要

Pythonだけで完結するバックテストCLIです。
ローカルで稼働中の `py_stock_data_api`（既定: `http://localhost:8000`）から株価や財務データを取得し、戦略検証をターミナルで実行します。

## 実行前提

- Python 3.12以上
- FastAPIサーバー起動済み
- [uv](https://docs.astral.sh/uv/) で依存パッケージ導入済み（`uv sync`、詳細は [README.md](../README.md#セットアップ)）

## デスクトップGUI

CLIと同じ `app/backtest` エンジンを利用します。API呼出し・描画・保存はスクリプト側です。

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
- `--max-per`: 年次EPSを使う時点整合PER上限フィルター（現在のPERは使わない）
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

## 約定と費用（Batch 5）

既定の `--execution next_open` は、バーtの終値までの情報で判断し、t+1の始値で約定します。
最終バーのシグナルは未約定です。`--execution close` は比較用の旧方式で、
シグナルと同じバーの終値で約定し、最終バーのシグナルも約定します。
週足・月足の「次のバー」は翌週・翌月です。

整数株・買いのみ・全額投資（買える株数を切り捨て）・同時1ポジションです。
約定バーで現金と株数を変更し、各バー終値で時価評価します。終端では強制決済しません。
始値が欠損・非有限・0以下なら、その約定バーの終値を代用します。
実際に代用した約定回数を `open_fallbacks` と警告に記録します（買持ち側は別集計）。

- `--fee-rate`（既定0）: 各約定代金に掛ける手数料率。0以上1未満。
- `--slippage-bps`（既定0）: 買値=元価格×(1+bps/10000)、売値=元価格×(1-bps/10000)。
  0以上10000未満。手数料は補正後価格に適用します。
- `--risk-free`（既定0）: 年率無リスク金利。小数指定（0.02=2%）、-1より大きい有限値。
- `--eps-lag-days`（既定90）: 年度末からEPS利用開始までの暦日数。0以上の整数。
- `--metrics-csv`（既定 `metrics.csv`）: 全指標を銘柄ごとに1行で保存。

取引CSVには判断日 `signal_date`、約定バー日付 `date`、補正後価格、手数料、
スリッページ金額、始値代用の有無、実現損益、約定後現金を記録します。
週足・月足の日付は期間最終取引日のラベルで、始値は期間最初の取引価格です。
チャートのマーカーは実際の約定バーと約定価格を表示します。

## 年次EPSと時点整合PER

`/stock/{symbol}/financials/history?limit=10`（APIの上限）を銘柄ごとに1回取得します。
従来の `period`（年ラベル）、`revenue`、`net_income` に加え、`period_end`（決算年度末日）、
`eps`、`shares` を後方互換で追加しました。
yfinanceの年次損益計算書からDiluted EPSを優先し、なければBasic EPSを使用します。
直接EPSがなければ、その年度のNet IncomeをDiluted Average Shares
（なければBasic Average Shares）で割ります。現在の発行済株式数や現在のPERは使いません。
直接EPSを取得できる場合、`shares` は同じ種類の加重平均株式数です。
EPSがない旧APIでも、年度の純利益と株数があればエンジンで同様に導出します。
年度ラベルだけなら、その年の12月31日を保守的な年度末とみなします。

各バーでは `period_end + eps_lag_days <= バー日付` を満たす最新年度を選び、
PER=そのバーの終値/年次EPS とします。EPSが正、かつPERが上限以下なら買いを許可します。
EPSが0以下なら買いを阻止します。EPS欠損も買いを阻止し、シグナルがない日や準備期間を含む
全バー中の欠損数を `missing_eps_bars` と警告で返します。
最新年度のEPSが欠損なら古い年度に戻しません。売りはPERフィルターで阻止しません。
`--max-per` 未指定なら財務取得とフィルターは不要です。

**制約:** 年次EPSのみの近似です。実際の決算発表日、四半期変動、当時の開示版や改訂履歴を
再現せず、固定の報告ラグを使います。提供元の履歴は数年に限られることがあり、
古い期間では欠損により買いが抑制されます。株価とEPSの通貨・分割調整基準の不一致も
完全には補正しません。厳密な当時開示情報の再現ではありません。

## 戦略とパラメータ

`--strategy` の既定は `sma_cross`。`--param key=value` を繰り返して指定します。
不明なキー、不正な型や値は、使用可能なキー・型・既定値とともにエラーになります。
全戦略で `max_per: float=None` を利用できます。

| 戦略 | 型・既定値 | 売買条件 |
|---|---|---|
| sma_cross | short_window: int=20, long_window: int=60 | 短期SMAが長期SMAを上抜けで買い、下抜けで売り（前バーは等号可） |
| rsi_reversion | period: int=14, lower: float=30, upper: float=70 | Wilder RSI < lowerで買い、RSI > upperで売り |
| bollinger | window: int=20, std_dev: float=2, exit: str=middle | 終値 < 下限で買い、終値 >= 中央線で売り。exit=upperなら上限以上で売り |
| momentum | period: int=20, threshold: float=0, exit_threshold: float=0 | NバーROC > thresholdで買い、ROC < exit_thresholdで売り |

各期間は正の整数、SMAはshort < long、RSIは0 <= lower < upper <= 100、
Bollingerのstd_devは正、ROCはexit_threshold <= thresholdです。
ROC=close[t]/close[t-N]-1（0.05=5%）。Bollingerは当日を含むSMA±std_dev×母標準偏差（ddof=0）。
RSIは最初のperiod個の価格差の平均上昇幅・平均下落幅を初期値にし、その後Wilder平滑化します。
平均下落幅0ならRSI=100（横ばいも100）です。必要期間がそろう前はシグナルを出しません。
条件が連続成立しても、保有中の買いと未保有時の売りは無視します。

既存の `--short-window` / `--long-window` はsma_crossに、`--max-per` はPERパラメータに対応します。
同じキーの `--param` があればそちらを優先します。

```bash
uv run --frozen python scripts/backtest_cli.py --symbol AAPL --start-date 2023-01-01 --end-date 2024-12-31 --strategy rsi_reversion --param period=14 --param lower=25 --param upper=75 --execution next_open --slippage-bps 5 --fee-rate 0.001
```

GUIは戦略選択、`key=value, key=value` 形式のParams欄、Execution、Slippage bpsに対応し、
全指標と警告を表示します。GUIの金利・EPSラグは既定値、グリッドはCLIのみです。
GUIモジュールのimportだけではウィンドウを作りません。

## 指標の定義と端数

F=年換算係数（1d:252、1wk:52、1mo:12）、N=入力バー数、E0=初期資金、Ei=i番目の終値資産。
最初のバーも1期間と数え、初回約定の費用を含めるため初期資金からのリターンも計算します。
暦日数では年率換算しません。

| 表示・CSVキー | 定義 |
|---|---|
| return_pct | (EN/E0-1)×100（総リターン） |
| cagr | ((EN/E0)^(F/N)-1)×100 |
| annualised_volatility | population_std(ri)×sqrt(F)×100、ri=Ei/E(i-1)-1 |
| sharpe | mean(xi)/population_std(ri)×sqrt(F)、xi=ri-((1+risk_free)^(1/F)-1) |
| sortino | mean(xi)/sqrt(mean(min(xi,0)^2))×sqrt(F) |
| total_trades | 完了した往復取引数（売り約定数）。買い約定数ではない |
| win_rate | 損益>0の往復数/完了往復数×100。損益0は勝ちに含めない |
| profit_factor | 正の実現損益合計/負の実現損益合計の絶対値 |
| average_win / average_loss | 正/負の実現損益の平均（負けは負の値、通貨単位） |
| exposure | 終値時点で保有株数>0のバー数/N×100 |
| max_drawdown | max(1-Ei/max(E0,...,Ei))×100 |
| max_drawdown_duration | 過去ピーク未満が連続する最長バー数。回復で0に戻り、未回復期間も含む |
| benchmark_return_pct / benchmark_cagr / benchmark_max_drawdown | 買持ち比較の同名指標 |

実現損益=売却代金-売り手数料-買い代金-買い手数料。スリッページは約定価格に含みます。
未決済損益は勝率・PF・平均損益に含めません。計算・CSVは丸めず、画面のみ桁を制限します。

取引なし: total_trades=0、win_rate=0、平均勝ち/負け=None。
PFは負けなし・勝ちありならinf、勝ちも負けもなければNone（損益0のみもNone）、負けのみなら0です。
標準偏差0（数値誤差許容1e-15以下）ならSharpe=None、下方偏差0（同許容）ならSortino=None。
NoneはCLI/GUIでNone、CSVでは空欄、infはinfです。空系列はエラー。1バーでも同じ式を適用します。

買持ちは最初のバーの終値で無条件の買いシグナルを出します。
next_openなら2バー目の始値、closeなら最初の終値で1回買い、終端売却はしません。
同じ資金・整数株・手数料・スリッページを使用し、PERは適用しません。
1バーだけのnext_openでは買いません。

## グリッド探索

`--grid key=v1,v2,...` を繰り返すと直積を実行します（最大500組）。
価格は銘柄につき1回、必要な財務履歴も1回取得して共有します。
全組を事前検証し、不正な組があれば実行前にエラーとします。
`--rank-by` は既定sharpeで、symbol以外の全指標キーが使用可能です。
通常は大きい順、volatility/DD/DD duration/欠損・代用回数は小さい順です。
None/NaNは末尾、同値は入力順、infは有効値です。
標準出力と `--grid-csv PATH` に全組の順位・パラメータ・指標を出力します。
取引CSV・指標CSV・チャート・Portfolio Summaryは各銘柄の首位の組を対象とします。
複数銘柄では個別に順位を付け、資金は従来どおり均等分割します。
同じ標本での最適化であり、将来成績を保証しません。

```bash
uv run --frozen python scripts/backtest_cli.py --symbol AAPL --start-date 2023-01-01 --end-date 2024-12-31 --grid short_window=5,10,20 --grid long_window=40,60 --rank-by sharpe --grid-csv grid.csv
```

## 履歴APIの週足・月足指標

要求intervalに応じ、開始日より日足365日・週足5年（365×5日）・月足18年（366×18日）を追加取得します。
拡張開始日は1900-01-01より前にはしません。取得元・キャッシュは日足ですが、
週次/月次OHLCVに集計してからSMA/RSIを計算し、要求開始日/終了日で応答をトリムします。
長い取得期間はSMA_200の準備用で、提供元や上場年数が不足すれば指標はnullのままです。
応答日付は集計期間の最終取引日。終了日が期間途中なら、その時点までの部分バーです。

## ポートフォリオ

Web画面の「ポートフォリオ」またはCLIの `--mode portfolio` で、同じ戦略・パラメータを2〜10銘柄に適用します。戦略が買いを出すと有効、売りを出すと無効になり、シグナルのないバーではその状態が続きます。すべての取引は共有の現金残高で行います。

- `equal` は全銘柄数、`equal_active` は買いが有効な銘柄数で約定時評価額を割り、1銘柄あたりの配分を求めます。無効になった銘柄は売却します。
- リバランスを省略すると、新規の買いと売りでポジションが変わります。`--rebalance-every 21` のように指定すると、初日から数えて21バーごとに目標額に近づけます。売却を先に処理し、買い注文の合計が残高を超える日は買付予算を比例配分します。すべて整数株で、約定ごとに手数料とスリッページを計上します。
- リバランスは保有比率の乖離幅が目標比率の5%を超えるときに行います。目標が50%なら、既定では2.5パーセントポイントを超える乖離が対象です。`--rebalance-threshold-pct` で許容乖離率を指定し、`0` で毎回調整します。売買シグナルは乖離率にかかわらず処理します。
- 取引表とCSVの「理由」は「シグナル」または「リバランス」です。取引回数、勝率、プロフィットファクター、平均利益、平均損失はシグナルによる売り決済から計算します。資産推移と損益寄与にはすべての約定を反映します。
- 日付は全銘柄の取引日の和集合です。休場銘柄は直近の終値で評価し、その日はシグナルも約定も発生しません。翌バー始値の注文と休場中のリバランスは、その銘柄に次のバーがある日に処理します。取引開始前の銘柄の評価額はゼロです。
- 損益寄与は最終保有額とその銘柄の売買に伴う現金増減の合計です。買い持ちは初期資金を全銘柄に均等配分し、各銘柄の最初のシグナルで1回買います。
- 株価と会社プロフィールから銘柄の取引通貨を確認します。判明した通貨が複数ある場合は、通貨と銘柄を示して実行を中止します。通貨が不明な銘柄がある場合は警告を表示し、取得した価格で計算します。

```powershell
uv run --frozen python scripts/backtest_cli.py --mode portfolio --symbols AAPL,MSFT --start-date 2023-01-01 --end-date 2024-12-31 --allocation equal_active --rebalance-every 21
```

## ウォークフォワード

Web画面の「ウォークフォワード」またはCLIの `--mode walkforward` で、単一銘柄の期間を学習（IS）と、その直後の検証（OOS）に分けます。バー数を `分割数 + 1` で割った整数部分が各検証期間の長さで、余りは最初の学習期間に含めます。ローリングは学習期間を同じバー数で移動し、アンカーは開始位置を固定したまま学習期間を伸ばします。

学習期間ごとにグリッド探索し、首位のパラメータで直後の検証期間を実行します。全候補の学習指標が `None` または `NaN` の分割は、先頭のパラメータを使用したことを分割表とCLIに表示します。指標の準備には期間より前の履歴を使い、学習時の価格は学習期間末までに限定します。各検証期間は現金で開始し、前の検証期間の最終評価額を次の元手にします。区切りの保有は終値の評価額で現金に換算し、その換算に手数料や約定は計上しません。連結曲線の買い持ちは最初の検証期間から最終日まで保有します。

分割は2〜50件、各検証期間は2バー以上です。分割数と候補組み合わせ数の積は最大500です。Webではさらに1分割あたり `BACKTEST_GRID_MAX_COMBOS`（既定100）の上限を適用します。利益が出た検証期間の割合と、学習・検証の両方で指標が有限だった分割での平均悪化幅を表示します。悪化幅は高いほど良い指標ならIS−OOS、低いほど良い指標ならOOS−ISです。

```powershell
uv run --frozen python scripts/backtest_cli.py --mode walkforward --symbol AAPL --start-date 2020-01-01 --end-date 2024-12-31 --folds 4 --window anchored --grid short_window=10,20 --grid long_window=40,60 --rank-by sharpe
```

## Webで結果を保存・比較

実行後に「結果を保存」で名前とメモを入力できます。「保存済みの結果」から開く、編集、削除、比較ができます。保存済みの個別結果は `#/backtest?run=7` のようなリンクで開けます。保存したアカウントでログインして開くと、結果と実行時の設定が表示され、その設定で再実行できます。

比較は2〜5件を選び、各曲線の最初の値を100として日付をそろえます。各結果の期間外は空白のままです。同じ期間内で取引日やサンプリング日が異なる場合は、その結果の前後の点を実線で結びます。比較表の★は、累積リターン、年率リターン、シャープレシオの最大値と、最大ドローダウンの最小値を示します。同値にも付きます。グリッド探索の比較では首位の組み合わせを使います。

各結果には曲線500点と取引1000件までを保存し、保存後のサイズは512 KiBまでです。曲線の間引きでは最初と最後の点を保持します。指標は間引き前の頻度や全取引に依存するため、保存時にクライアントから受信した指標を使い、APIと画面でその出所を表示します。保存件数は `BACKTEST_MAX_SAVED_RUNS`（既定50）までです。
