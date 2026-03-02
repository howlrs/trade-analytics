# 運用レビュー手順書

定期的にボットの運用状況を確認し、評価するための手順とテンプレート。

## ログ取得コマンド

### 基本ログ取得

```bash
# 直近12時間のログ（全量）
GCE_PASSPHRASE=$(grep GCE_PASSPHRASE .env | cut -d= -f2) bash deploy/logs.sh --since "12 hours ago"

# 直近24時間のログ
GCE_PASSPHRASE=$(grep GCE_PASSPHRASE .env | cut -d= -f2) bash deploy/logs.sh --since "24 hours ago"
```

### 重要イベントだけ抽出

```bash
# Harvest/リバランス/エラーのみ
GCE_PASSPHRASE=$(grep GCE_PASSPHRASE .env | cut -d= -f2) bash deploy/logs.sh --since "12 hours ago" \
  -g 'Harvest|Next harvest|Rebalance eval.*true|range.out|New position|close|open.*position|Scheduler|error|warn|Funds|Idle|Volatility: insuff'
```

### リバランス・スワップコスト関連ログ

```bash
# リバランス完了ログ（swapFree, totalGas を確認）
GCE_PASSPHRASE=$(grep GCE_PASSPHRASE .env | cut -d= -f2) bash deploy/logs.sh --since "12 hours ago" \
  -g 'Rebalance completed'

# Idle fund deployment（遊休資金投入時のスワップ）
GCE_PASSPHRASE=$(grep GCE_PASSPHRASE .env | cut -d= -f2) bash deploy/logs.sh --since "12 hours ago" \
  -g 'Idle deploy'

# 日次リミット（ソフトリミット: range-out以外をブロック）
GCE_PASSPHRASE=$(grep GCE_PASSPHRASE .env | cut -d= -f2) bash deploy/logs.sh --since "12 hours ago" \
  -g 'Daily rebalance limit'

# Profitability gate（ブレークイーブン判定）
GCE_PASSPHRASE=$(grep GCE_PASSPHRASE .env | cut -d= -f2) bash deploy/logs.sh --since "12 hours ago" \
  -g 'breakeven'

# ポジション資金（Pre-open balances で規模確認）
GCE_PASSPHRASE=$(grep GCE_PASSPHRASE .env | cut -d= -f2) bash deploy/logs.sh --since "12 hours ago" \
  -g 'Pre-open'
```

### サービス状態確認

```bash
GCE_PASSPHRASE=$(grep GCE_PASSPHRASE .env | cut -d= -f2) bash deploy/logs.sh status
```

### リアルタイム追従

```bash
GCE_PASSPHRASE=$(grep GCE_PASSPHRASE .env | cut -d= -f2) bash deploy/logs.sh -f
```

### SSH接続不能時のフォールバック

SSHがタイムアウトする場合、シリアルポートログを使用する:

```bash
# VM状態確認
gcloud compute instances describe sui-auto-lp --zone=us-central1-a --project=crypto-bitflyer-418902 --format="value(status)"

# シリアルポートログ（重要イベントのみ）
gcloud compute instances get-serial-port-output sui-auto-lp \
  --zone=us-central1-a --project=crypto-bitflyer-418902 --start=0 2>&1 \
  | grep -v 'Rebalance evaluation.*shouldRebalance.*false' \
  | grep -v 'Specify --start='
```

## 評価テンプレート

以下をベースに、ログから数値を埋めて評価を作成する。

---

### 運用レビュー: YYYY-MM-DD

**評価期間**: HH:MM UTC ~ HH:MM UTC (XXh)

#### サービス状態

| 項目 | 状態 |
|---|---|
| SSH接続 | OK / NG (フォールバック方法) |
| プロセス | PID XXXXX, 継続稼働 / 再起動あり |
| エラー | X件 (詳細) |

#### リバランス

| 時刻 (UTC) | トリガー | 理由 | 新レンジ |
|---|---|---|---|
| (なし or 詳細) | threshold / range_out | Price within X% of edge | tick XXXX-YYYY |

#### リバランス・スワップコスト分析

**ログソース**: `Rebalance completed`, `Idle deploy`, `breakeven`, `Pre-open`

| # | 時刻 (UTC) | swapFree | ガス (SUI) | Idle deploy スワップ | スワップ額 | 推定コスト (0.25%) |
|---|---|---|---|---|---|---|
| 1 | HH:MM | true/false | X.XXXX | a2b=true/false | XXX.XX SUI/USDC | $X.XX |

**コスト集計**:

| コスト項目 | 金額 |
|---|---|
| リバランス本体スワップ | $X.XX (swapFree=true なら $0) |
| Idle deploy スワップ (0.25%) | $X.XX |
| ガス (全リバランス合計) | $X.XX |
| **リバランス総コスト** | **$X.XX** |
| レンジアウト機会損失 (OOR時間 × 時間収益) | $X.XX |

**Profitability gate**:

| 指標 | 値 |
|---|---|
| ブレークイーブン | X.Xh (上限: 48h) |
| 観測時間収益 | $X.XX/h |
| ポジション規模 | $X,XXX |
| データソース | observed / estimated |

**コスト対収益**:

| 指標 | 値 |
|---|---|
| 期間収益 | $XX.XX |
| リバランス総コスト | $X.XX |
| コスト/収益比 | XX.X% |
| 差引純収益 | $XX.XX |

> **計算方法**:
> - スワップコスト = スワップ量 × プール手数料率 (0.25%)
> - SUI のスワップ量は 9桁 (1e9 = 1 SUI)、USDC は 6桁 (1e6 = 1 USDC)
> - SUI 価格 = 1 / (coinB/coinA price)。ログの `currentPrice` から算出
> - ガス = totalGas (MIST) / 1e9 × SUI価格
> - swapFree=true の場合、リバランス本体のスワップコストは $0
> - Idle deploy のスワップは遊休資金投入時のみ発生。リバランスの固有コストではない

#### Harvest サイクル

| 時刻 (UTC) | アクション | 金額 | 内訳 | 結果 |
|---|---|---|---|---|
| HH:00 | Harvest / Skip | $X.XX | fees $X.XX + rewards $X.XX | 成功 / 失敗 |

**期間内収穫合計**: $XX.XX

#### 収益指標

| 指標 | 値 |
|---|---|
| 時間あたり収益 | $X.XX/h |
| 日換算 | $XX.XX/day |
| 年率換算 (参考) | XXX% APR |
| ポジション規模 | ~$X,XXX |

#### ポジション価値・IL 分析

| 指標 | 値 |
|---|---|
| SUI 価格変動 | $X.XX → $X.XX (±X.X%) |
| ポジション構成 | USDC XX% / SUI XX% |
| 期初ポジション価値 | $X,XXX |
| 現在ポジション価値 | $X,XXX |
| 評価額変動 | ±$XXX (±X.X%) |
| 手数料収入 | +$XX.XX |
| 手数料カバー率 | XX% (手数料 / 評価減) |

> **構成比の解釈**: SUI下落→SUI heavy、SUI上昇→USDC heavy が CLMM の正常動作。
> SUI 下落時は LP が SUI を蓄積（tick が upper 側へ）、SUI 上昇時は USDC を蓄積（tick が lower 側へ）。
> 詳細は [docs/price-direction.md](price-direction.md) 参照。

#### 前回比較 (任意)

| 指標 | 前回 | 今回 | 変化 |
|---|---|---|---|
| 時間あたり収益 | $X.XX/h | $X.XX/h | +X% / -X% |
| Harvest回数 | X回 | X回 | |
| リバランス回数 | X回 | X回 | |

#### ボラティリティ

- σ/hour 範囲: X ~ XX
- データ不足期間: HH:MM ~ HH:MM UTC (プール活動低下)
- tick幅: 480 (最小) を維持 / 変動あり

#### 注意事項・所見

1. (特記事項があれば記載)
2. (例: fix_amount_a フォールバック発生、実運用に影響なし)
3. (例: 週末でボラティリティ低下、収益率は平日比 -XX%)

#### 総評

(1-2文で全体評価)

---

## 評価の観点

### 正常性チェック

- [ ] サービスが稼働中 (PID変更なし or 再起動理由が明確)
- [ ] エラーログが0件 or 既知の無害なもののみ
- [ ] Harvest サイクルが正常に回っている (2h間隔)
- [ ] ボラティリティエンジンが動作 (キャッシュフォールバックは許容)

### 収益性チェック

- [ ] 時間あたり収益が前回と大きく乖離していないか
- [ ] Skip が連続していないか (3回以上連続は活動低下サイン)
- [ ] リバランスコストが収益を圧迫していないか (コスト/収益比 < 50% が目安)
- [ ] swapFree=true が有効か (false の場合、ポジション規模 × 0.25% のコストが発生)
- [ ] Idle deploy のスワップ量が過大でないか (ポジション規模の 50% 超は要注意)
- [ ] ブレークイーブンが短時間か (< 12h が理想、48h 上限)

### リスクチェック

- [ ] リバランスが過剰に発生していないか (1日3回ソフトリミット、range-outは例外で通過)
- [ ] レンジアウトが頻発していないか
- [ ] ガス残高が十分か (最低1.0 SUI)
- [ ] Idle deploy のスワップ比率が `maxIdleSwapRatio` (20%) を超えていないか
- [ ] 日次リバランスカウントが再起動後も正しく永続化されているか

### IL・価格変動チェック

- [ ] SUI 価格の方向（上昇/下落/横ばい）を確認
- [ ] ポジション構成比（USDC/SUI 比率）が価格方向と整合しているか
  - SUI 下落 → SUI heavy（LP が SUI を蓄積）= 正常
  - SUI 上昇 → USDC heavy（LP が USDC を蓄積）= 正常
- [ ] 手数料収入 > 価格変動による損失 か（手数料がカバーしている割合）
- [ ] 詳細は [docs/price-direction.md](price-direction.md) の「価格変動時の CLMM ポジション挙動」参照

### 外部要因

- 曜日 (土日はボラティリティ低下傾向)
- 市場イベント (大きな価格変動、ニュース)
- プロトコルアップデート (Cetus SDK, Sui ネットワーク)

## 手動オペレーション

### 遊休資金の追加投入

ウォレットに遊休資金がある場合、既存ポジションに追加投入できる:

```bash
# ドライラン
DRY_RUN=true npx tsx scripts/add-funds.ts

# 本番実行
DRY_RUN=false npx tsx scripts/add-funds.ts
```

- Aggregator vs Direct Pool を自動比較し、安い方でスワップ
- ガスリザーブ 1.0 SUI を自動確保
- スワップ後に addLiquidity を実行

### 分析スクリプト（事後検証）

```bash
# リバランスROI分析（各リバランスの費用対効果）
npx tsx scripts/analyze-rebalance-roi.ts --last 7

# 反実仮想シミュレーション（thresholdリバランスのスキップ判定）
npx tsx scripts/simulate-skip.ts --last 14

# 手数料収益ヒートマップ（曜日×時間帯の収益可視化）
npx tsx scripts/revenue-heatmap.ts --last 14
```

### 一時停止・再開

```bash
# VM上の .env を変更（再起動不要、最大30秒で反映）
# 詳細は docs/operations.md 参照
```
