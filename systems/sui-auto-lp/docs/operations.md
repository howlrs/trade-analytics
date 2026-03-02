# 日常運用・監視・緊急対応

## 一時停止・再開

`.env` の `PAUSED` フラグで制御する。サービス再起動不要（次のチェックサイクル、最大30秒以内に反映）。

```bash
# 一時停止（リバランス・ハーベストをスキップ）
gcloud compute ssh sui-auto-lp --zone=us-central1-a \
  --command="sudo sed -i '/^PAUSED=/d' /opt/sui-auto-lp/.env && echo 'PAUSED=true' | sudo tee -a /opt/sui-auto-lp/.env"

# 再開
gcloud compute ssh sui-auto-lp --zone=us-central1-a \
  --command="sudo sed -i '/^PAUSED=/d' /opt/sui-auto-lp/.env && echo 'PAUSED=false' | sudo tee -a /opt/sui-auto-lp/.env"

# 現在の状態を確認
gcloud compute ssh sui-auto-lp --zone=us-central1-a \
  --command="grep '^PAUSED=' /opt/sui-auto-lp/.env"
```

一時停止中はログに `Bot is paused (PAUSED=true in .env), skipping rebalance check` と出力される。

---

## ヘルスチェック

### コマンド

```bash
npm run health            # 基本チェック
npm run health:verbose    # 詳細出力（全データ項目を展開）
npm run health:json       # JSON 出力（自動監視・ログ保存用）

# ファイルに保存
npm run health:json > logs/health-$(date +%Y%m%d-%H%M%S).json
```

### チェック項目

| 項目 | 内容 | FAIL 条件 |
|---|---|---|
| `ENV` | 環境変数の設定 | `SUI_PRIVATE_KEY` 未設定 |
| `POOL` | プール状態・SUI 価格 | RPC 接続不可 |
| `GAS` | ガス用 SUI 残高 | < 1.0 SUI |
| `WALLET` | ウォレット全残高 | — |
| `POS:xxxx` | 各ポジションの状態 | レンジ外 |
| `FUNDS` | 資金合計（ポジション + ウォレット） | — |
| `EVENT_LOG` | 今日のイベントログ統計 | — (エラー数で WARN) |
| `BOT_ALIVE` | bot.log の更新時刻 | 120秒以上更新なし |
| `COST_EST` | 次回リバランスの推定コスト | — |

### 出力例

```
======================================================================
  Sui Auto LP - Health Check
  2026-02-15T14:42:35.448Z
======================================================================

  [OK]   ENV
         POSITION_IDS configured: 1 position(s)

  [OK]   POOL
         SUI price: $1.0156, tick: 69236

  [OK]   GAS
         SUI for gas: 10.1331 SUI ($10.29)

  [OK]   WALLET
         USDC: 8.3329, SUI: 10.1331 ($18.62)

  [OK]   POS:0x5b6f696a
         $19.85 | USDC:9.83 SUI:9.86 | In range (48.2%↓ 51.8%↑)

  [OK]   FUNDS
         Position: $19.85 + Wallet: $18.62 = Total: $38.47

  [OK]   COST_EST
         Next rebalance est. cost: $0.0320

----------------------------------------------------------------------
  RESULT: ALL OK (9 checks passed)
======================================================================
```

---

## 日報（Daily Report）

```bash
npm run report                            # 今日の日報
npx tsx scripts/daily-report.ts 2026-02-15  # 特定日
npm run report:all                        # 全日付の一括表示
```

### イベントログの場所

```bash
ls logs/                                   # ログファイル一覧
cat logs/events-2026-02-15.jsonl           # 生ログ（JSONL 形式）
```

---

## ログ監視

### deploy/logs.sh の使い方

```bash
GCE_PASSPHRASE=$(grep GCE_PASSPHRASE .env | cut -d= -f2) bash deploy/logs.sh                          # 直近50行
GCE_PASSPHRASE=$(grep GCE_PASSPHRASE .env | cut -d= -f2) bash deploy/logs.sh --since "1 hour ago"     # 時間指定
GCE_PASSPHRASE=$(grep GCE_PASSPHRASE .env | cut -d= -f2) bash deploy/logs.sh --since "1 hour ago" -g Harvest  # grep
GCE_PASSPHRASE=$(grep GCE_PASSPHRASE .env | cut -d= -f2) bash deploy/logs.sh -f                       # リアルタイム追従
GCE_PASSPHRASE=$(grep GCE_PASSPHRASE .env | cut -d= -f2) bash deploy/logs.sh status                   # サービス状態
```

### 重要 grep キーワード一覧

| キーワード | 用途 |
|---|---|
| `Rebalance completed` | リバランス実行履歴 |
| `Cooldown` | クールダウン待機（価格が戻ればリバランス回避 = コスト節約） |
| `range-out\|out of range` | レンジ外検知（方向: up/down） |
| `breakeven\|Profitability gate` | 収益性ゲートで見送ったケース |
| `Volatility engine\|sigma` | ボラティリティエンジンの判定結果 |
| `Harvest\|Next harvest` | ハーベスト実行・スケジュール |
| `Idle deploy` | 遊休資金投入 |
| `Daily rebalance limit` | 日次リミット到達 |
| `Pre-open` | ポジション資金（規模確認） |
| `error\|warn` | エラー・警告 |
| `open\|close` | ポジション開閉 |

### grep の注意点

`deploy/logs.sh -g` は内部で `grep -E` を使用する:

- 単一キーワード: `-g "Harvest"` — OK
- OR 条件: `-g 'Harvest|error|warn'` — **シングルクォートで囲む**
- 複雑すぎるパターンは失敗しやすい。キーワードごとに分割して個別実行を推奨

### リバランス分析（ログ grep）

```bash
# リバランス実行履歴
GCE_PASSPHRASE=$(grep GCE_PASSPHRASE .env | cut -d= -f2) bash deploy/logs.sh --since "24 hours ago" -g "Rebalance completed"

# クールダウンで待機したケース
GCE_PASSPHRASE=$(grep GCE_PASSPHRASE .env | cut -d= -f2) bash deploy/logs.sh --since "24 hours ago" -g "Cooldown"

# レンジ外検知
GCE_PASSPHRASE=$(grep GCE_PASSPHRASE .env | cut -d= -f2) bash deploy/logs.sh --since "24 hours ago" -g 'range-out|out of range'

# 収益性ゲートで見送り
GCE_PASSPHRASE=$(grep GCE_PASSPHRASE .env | cut -d= -f2) bash deploy/logs.sh --since "24 hours ago" -g 'breakeven|Profitability gate'

# ボラティリティエンジン
GCE_PASSPHRASE=$(grep GCE_PASSPHRASE .env | cut -d= -f2) bash deploy/logs.sh --since "24 hours ago" -g 'Volatility engine|sigma'
```

### スワップルート分析（Aggregator vs Direct Pool）

```bash
# 見積もり比較結果
GCE_PASSPHRASE=$(grep GCE_PASSPHRASE .env | cut -d= -f2) bash deploy/logs.sh --since "24 hours ago" -g "Swap quote comparison"

# 実際の実行メソッド・フォールバック有無
GCE_PASSPHRASE=$(grep GCE_PASSPHRASE .env | cut -d= -f2) bash deploy/logs.sh --since "24 hours ago" -g '"Swap executed"'
```

**ログフィールド解説:**

| フィールド | 説明 |
|---|---|
| `winner` | 見積もりで有利だった方（`aggregator` / `direct_pool` / `tie`） |
| `diffBps` | 差分（basis points）。正 = Aggregator有利、負 = Direct Pool有利 |
| `method` | 実際に実行したメソッド |
| `fallback` | `true` = winner の実行が失敗し、もう一方で実行した |
| `aggRoute` | Aggregator が選んだルートパス |

---

## 稼働検証チェックリスト

ボットの運用が正しく行われていることを確認するための手順。

### Step 1: ヘルスチェック実行

```bash
npm run health
```

- [ ] 全項目が `[OK]` または許容可能な `[WARN]`
- [ ] `GAS`: SUI 残高 > 0.15 SUI
- [ ] `POS`: 管理ポジションが `In range` 状態
- [ ] `BOT_ALIVE`: bot.log が最近更新されている
- [ ] `FUNDS`: 資金合計が想定額と一致

### Step 2: ログ確認

```bash
tail -20 logs/bot.log
grep '"error"\|"FAIL"\|"halt"' logs/events-$(date +%Y-%m-%d).jsonl
```

- [ ] `[error]` や `CRITICAL` が直近にない
- [ ] `scheduler_halt` イベントがない
- [ ] リバランスチェックが定期的に実行されている

### Step 3: 日報で運用状況を確認

```bash
npm run report
```

- [ ] Rebalance Errors: 0
- [ ] Harvest Errors: 0
- [ ] Gas Usage が想定範囲内

### Step 4: オンチェーン検証（手動）

```bash
sui client balance
sui client objects | grep -i position
```

### Step 5: コスト検証

```bash
npm run health:verbose
```

- [ ] 推定リバランスコスト < 1日のLP手数料収入見込み
- [ ] スワップコスト（0.25% × スワップ額）が想定範囲

---

## 定期運用スケジュール

| 頻度 | 作業 | コマンド |
|---|---|---|
| 毎日 | ヘルスチェック | `npm run health` |
| 毎日 | 日報確認 | `npm run report` |
| 週次 | 詳細ログレビュー | `npm run health:verbose` |
| 月次 | コスト分析・戦略見直し | 全日報一括 + JSON ヘルスチェック |
| 随時 | ボット稼働確認 | `deploy/logs.sh -f` |

---

## SSH 接続不能時のフォールバック

SSH がタイムアウトする場合、シリアルポートログを使用する:

```bash
# VM 状態確認
gcloud compute instances describe sui-auto-lp --zone=us-central1-a \
  --project=crypto-bitflyer-418902 --format="value(status)"

# シリアルポートログ（重要イベントのみ）
gcloud compute instances get-serial-port-output sui-auto-lp \
  --zone=us-central1-a --project=crypto-bitflyer-418902 --start=0 2>&1 \
  | grep -v 'Rebalance evaluation.*shouldRebalance.*false' \
  | grep -v 'Specify --start='
```

---

## 緊急対応パターン

### 急変動（range-out 頻発）時の対応

1. ログで range-out 頻度とクールダウン回避を確認
2. リバランス回数が過剰なら `PAUSED=true` で一時停止
3. 市場が落ち着いてから `PAUSED=false` で再開
4. 必要に応じてレンジ幅パラメータを拡大

### 過剰リバランス検知

- 日次リバランス回数が3回以上（ソフトリミット到達）
- 対応: `PAUSED=true` → 市場状況を確認 → パラメータ調整後に再開

### リバランスが全く発生しない場合

1. `PAUSED=false` であることを確認
2. ポジションがレンジ内にあることを確認（`npm run health`）
3. threshold 設定が適切か確認（`rebalanceThreshold` が小さすぎないか）
4. ログで `Rebalance evaluation` が出ているか確認（スケジューラが動作しているか）
5. circuit breaker が発動していないか確認

### ガス不足時の対応

1. `npm run health` で GAS 残高を確認
2. ウォレットに SUI を送金（最低 0.15 SUI 必要、推奨 1.0 SUI 以上）
3. ボットは自動的にガスチェックをパスして再開

### Circuit breaker 発動時の対応

5回連続失敗でポジションがスキップされた場合:

1. ログでエラー原因を調査
2. 原因を解消（RPC 障害、SDK バージョン不整合等）
3. サービスを再起動して circuit breaker をリセット

```bash
GCE_PASSPHRASE=$(grep GCE_PASSPHRASE .env | cut -d= -f2) bash deploy/logs.sh status
# 必要なら再起動
gcloud compute ssh sui-auto-lp --zone=us-central1-a \
  --command="sudo systemctl restart sui-auto-lp"
```

---

## 評価指標

24時間以上の稼働データから以下を確認する:

| 指標 | 確認方法 | 目安 |
|---|---|---|
| リバランス回数/日 | 日報 (`npm run report`) | 3回/日以下が目標 |
| レンジ内滞在率 | レンジ外検知数 × クールダウン時間 / 24h | 90%以上 |
| クールダウン回避率 | Cooldown ログ数 / range-out ログ数 | 高いほどコスト節約 |
| 純利益 | 手数料収入 − リバランスコスト | プラスであること |
| スワップコスト | swap-free 時は $0、通常時は 0.25% × 額 | swap-free 推奨 |
