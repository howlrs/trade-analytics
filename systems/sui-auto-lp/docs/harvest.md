# ハーベスト（Claim）挙動ガイド

## Harvest-only 方式

Cetus CLMM の `collectRewarderTransactionPayload` を使用して、ポジションに蓄積された手数料（fees）とリワード（rewards）をウォレットに claim する。

**ポジションへの再投入（compound）は行わない**。claim した資金はウォレットに留まる。

---

## スケジューリング

### Wall-clock 固定スケジュール

偶数時 UTC（0:00, 2:00, 4:00, ..., 22:00）に wall-clock 固定で実行される。

- `scheduleNextHarvest()` が次の偶数時を計算し、`setTimeout` でスケジュール
- 相対的なインターバル（2時間後）ではなく、壁時計の偶数時にアラインする
- 実行完了後、次の偶数時を再スケジュール

### スキップ条件

- `PAUSED=true` の場合、ハーベストをスキップ

---

## 評価フロー

1. **fetchFees**: ポジションの未回収手数料を取得（feeA = USDC, feeB = SUI）
2. **fetchRewards**: ポジションの未回収リワードを取得（CETUS, SUI 等）
3. **USD 換算**:
   - feeA (USDC): そのまま USD
   - feeB (SUI): `feeB × coinBPriceInCoinA(pool)` で USDC 換算
   - CETUS リワード: `getCetusUsdPrice()` で USD 換算
   - SUI リワード: SUI/USDC 価格で換算
4. **threshold 判定**: 合計 USD が閾値以上なら実行

---

## 閾値

| パラメータ | デフォルト | 説明 |
|---|---|---|
| `harvestThresholdUsd` | $0.50 | 合計手数料+リワードがこの額以上で実行 |
| ガス最低残高 | 0.05 SUI | ウォレットの SUI がこの額未満なら実行しない |

---

## 実行

`sdk.Rewarder.collectRewarderTransactionPayload` を呼び出す:

- `collect_fee: true` — 手数料とリワードを1つのトランザクションで回収
- 全 `rewarder_coin_types` を指定
- dry-run → 成功なら本番実行

---

## リバランスとの関係

リバランスの close ステップで `collect_fee: true` を指定するため、close 時に手数料が自動回収される。

- close による回収後、`feeTracker` がリセットされる
- 次のハーベストサイクルでは新たに蓄積された手数料のみが対象

---

## Idle Deploy との独立性

- harvest で claim した資金はウォレットに留まる
- idle fund deployment はリバランス完了後にのみ発動する（定期的には実行しない）
- したがって harvest 収穫物が idle deploy に消費されることはない

---

## ログキーワード

| キーワード | 説明 |
|---|---|
| `Harvest evaluation` | 手数料・リワードの評価結果（`totalUsd`, `shouldHarvest`） |
| `Next harvest` | 次回ハーベスト予定時刻 |
| `harvest_execute` | ハーベスト実行イベント |
| `harvest_skip` | 閾値未満でスキップ |
| `harvest_error` | ハーベスト実行エラー |
