# リバランス挙動ガイド

## リバランスの3ステップ

リバランスは常に以下の3ステップで実行される:

1. **Close**: 現在のポジションから全流動性を除去（`collect_fee: true` で手数料も回収）
2. **Swap**: 新しいレンジに最適な比率にトークンをスワップ（swap-free モードでは省略可能）
3. **Open**: 新しいレンジでポジションを開設し、流動性を投入

---

## トリガー種別

| トリガー | 条件 | クールダウン | 収益性ゲート |
|---|---|---|---|
| **range-out** | 現在価格がLP範囲外 | 30分待機 + 方向別（下落60分/上昇30分） | 2時間以上range-out後はバイパス |
| **threshold** | 価格がレンジ端から `rebalanceThreshold`（10%）以内 | `minTimeInRangeSec`（2時間、新ポジションのみ） | breakeven モデルで判定 |
| **range-fit** | 現在のレンジ幅がボラティリティ最適幅の2倍以上 | 6時間クールダウン（pre-fit 幅を記録） | `maxBreakevenHours` で判定 |
| **time-based** | インフラは存在するが現在未使用 | — | — |

---

## ガードレール一覧

### クールダウン（非対称）

SUI 価格の方向によってクールダウン時間が異なる:

| 方向 | クールダウン | 理由 |
|---|---|---|
| SUI 下落（tick 上昇） | 3600秒（60分） | ポジションは 100% SUI heavy → 底値での売りを回避、反発を待つ |
| SUI 上昇（tick 下降） | 1800秒（30分） | ポジションは 100% USDC heavy → 早期に新レンジで収益再開 |

### waitAfterRangeout

| パラメータ | デフォルト | 説明 |
|---|---|---|
| `waitAfterRangeoutSec` | 1800秒（30分） | レンジアウト検出後の待機時間。20-30%は自己修復する |

### 日次上限（ソフトリミット）

| パラメータ | デフォルト | 説明 |
|---|---|---|
| `maxRebalancesPerDay` | 3 | 1日あたりの最大リバランス回数 |

- **ソフトリミット**: range-out トリガーは上限到達後も通過する
- カウントは `state.json` に永続化され、再起動後も保持される
- 日付は UTC ベースでリセット

### minTimeInRange

| パラメータ | デフォルト | 説明 |
|---|---|---|
| `minTimeInRangeSec` | 7200秒（2時間） | 新ポジション開設後の最低レンジ内時間 |

threshold トリガーのみに適用。range-out は即発動可能。

### 収益性ゲート

リバランスのコスト（スワップ手数料 + ガス）と期待収益を比較し、赤字リバランスを回避する。

**計算モデル:**

1. **観測データあり**: `breakeven = swapCost / observedHourlyFeeUsd`
2. **データ不足（フォールバック）**: `dailyFee = positionValue × fallbackDailyVolumeRatio(2%) × poolFeeRate(0.25%)`

**閾値**: `maxBreakevenHours`（48時間）を超えるリバランスはブロック。

**バイパス条件:**
- range-out が2時間以上継続 → ゲートをバイパス
- 観測時間収益が 10% APY 超 → ゲートをスキップ

### range-fit ガード

| パラメータ | デフォルト | 説明 |
|---|---|---|
| range-fit クールダウン | 21600秒（6時間） | range-fit トリガー後、narrower レンジへのリバランスを6時間抑制 |

- range-fit 発動時に pre-fit の幅をスナップショットし、即座に narrow → range-out の振動を防止

---

## Swap-Free モード

`swapFreeRebalance=true`（デフォルト）時の動作:

1. close 時にポジションから返却されたトークンの比率をそのまま使用
2. **ratio-correction swap**: 比率が新レンジの最適比率から大きくずれている場合のみ、上限付きスワップを実行

| パラメータ | デフォルト | 説明 |
|---|---|---|
| `swapFreeMaxRatioSwap` | 0.20（20%） | ratio-correction スワップの上限 |
| range-out 時の緩和 | 0.50（50%） | range-out トリガー時はスワップ上限を50%に緩和 |

**swap fallback**: ratio-correction swap 後もポジション開設に失敗した場合、通常スワップにフォールバックする。

---

## 資金隔離（Delta 方式）

ウォレット内の既存資金とポジション由来の資金を分離する仕組み:

1. **preClose snapshot**: close 前のウォレット残高を記録
2. **postClose**: close 後のウォレット残高を取得
3. **delta = postClose - preClose**: ポジションから返却された資金のみを使用
4. **GAS_RESERVE**: delta から 1.0 SUI を予約し、ウォレット残高を維持

**安全ガード:**
- delta ≤ 0（live モード） → ABORT（安全停止）
- delta ≤ 0（dry-run） → `estimatePositionAmounts` で推定値を使用

---

## Recovery モード

ポジションの liquidity が 0 の場合に自動検知される:

- close ステップをスキップ（liquidity がないため）
- ウォレット全残高を使用して新ポジションを開設
- 手動でポジションを解除した後の復旧に使用

---

## Idle Fund Deployment

リバランス完了後、ポジションに投入しきれなかった遊休資金を追加投入する:

- **発動条件**: リバランス完了後のみ（定期的には実行しない）
- **最大イテレーション**: 5回ループ
- **各サイクル**: 残高確認 → スワップ（ratio 調整）→ addLiquidity
- **スワップ上限**: `maxIdleSwapRatio`（45%）— 超過分は部分投入
- **終了条件**: idle 資金 < 1 USDC かつ < 0.1 SUI
- **独立性**: harvest 収穫物はウォレットに留まり、idle deploy は消費しない
- **ベストエフォート**: 失敗してもリバランス結果に影響しない

---

## エッジケース・障害パターン

### close 成功 → swap 失敗

資金はウォレット内に安全に残る。次のチェックサイクルで recovery mode として検知される。

### close + swap 成功 → open 失敗

swap fallback を試行。それでも失敗した場合、資金はウォレット内に留まる。次サイクルで recovery mode が発動。

### RPC 遅延

close 後のバランス更新に最大20秒の settlement polling を実行（5s + 5s + 10s の3回リトライ）。

### 急変動中のリバランス

- クールダウンの非対称性が保護を提供（SUI 下落60分 / 上昇30分）
- `waitAfterRangeoutSec`（30分）で20-30%の自己修復をキャッチ

### Circuit breaker

5回連続で同一ポジションのリバランスが失敗した場合、そのポジションを永久スキップ。手動介入が必要。

### Exponential backoff

連続失敗時は `60秒 × failureCount`（最大300秒）のバックオフを適用。

---

## ボラティリティエンジン

`strategy: 'dynamic'` 使用時に、リアルタイムのボラティリティからレンジ幅を動的に決定する。

### 計算プロセス

1. 直近 `volLookbackHours`（2時間）の SwapEvents を取得（最大1000件、50件/ページ）
2. 連続するスワップ間の tick 変動から標準偏差（σ/hour）を算出
3. σ をティアにマッピング:

| σ (tick/hour) | tick幅 |
|---|---|
| < 40 | 480 ticks |
| 40 - 80 | 720 ticks |
| 80 - 120 | 960 ticks |
| ≥ 120 | 1200 ticks |

### キャッシュ・フォールバック

- **5分キャッシュ**: 同一結果を5分間再利用
- **10エントリ履歴**: 安定カウンティング（range-fit トリガー用）
- **3段フォールバック**: キャッシュ → 前回値 → percentage ベース計算

---

## パラメータ一覧

| パラメータ | デフォルト | 説明 |
|---|---|---|
| `rebalanceThreshold` | 0.03（推奨: 0.10） | レンジ端からのリバランス閾値 |
| `checkIntervalSec` | 30 | プール状態チェック間隔（秒） |
| `slippageTolerance` | 0.01（1%） | スリッページ上限 |
| `maxSwapCostPct` | 0.01（1%） | スワップコスト上限（ポジション価値比） |
| `swapFreeRebalance` | true | スワップスキップモード |
| `swapFreeMaxRatioSwap` | 0.20（20%） | ratio-correction スワップ上限 |
| `maxIdleSwapRatio` | 0.45（45%） | idle deploy 時のスワップ上限 |
| `waitAfterRangeoutSec` | 1800（30分） | レンジアウト後の待機時間 |
| `maxRebalancesPerDay` | 3 | 日次リバランス上限（ソフト） |
| `minTimeInRangeSec` | 7200（2時間） | 新ポジション最低レンジ内時間 |
| `maxBreakevenHours` | 48 | 収益性ゲート閾値（時間） |
| `MIN_SUI_FOR_GAS` | 0.15 SUI | ガス残高最低要件 |
| `GAS_RESERVE` | 1.0 SUI | ポジション資金からの予約額 |
| `volLookbackHours` | 2 | ボラティリティ計算のルックバック |
| `volTickWidthMin` | 480 | 最小 tick 幅 |
| `volTickWidthMax` | 1200 | 最大 tick 幅 |
| `FEE_FETCH_INTERVAL_SEC` | 120（2分） | fee RPC 呼び出し間隔 |
| `COOLDOWN_UP_SEC` | 1800（30分） | 上昇時クールダウン |
| `COOLDOWN_DOWN_SEC` | 3600（60分） | 下落時クールダウン |
