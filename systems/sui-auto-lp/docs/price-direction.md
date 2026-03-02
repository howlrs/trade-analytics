# CLMM 価格方向ガイド — 間違えやすいペアの教訓

> **Issue**: [#15 — Fix inverted SUI/USDC price in USD conversion calculations](https://github.com/howlrs/sui-auto-lp/issues/15)

## 背景

Cetus CLMM SDK の価格関数 `sqrtPriceX64ToPrice` の戻り値の方向を誤解し、
全ての USD 換算が約 9% ずれていた。SUI/USDC のような 1:1 に近いペアでは
逆数との差が小さく、目視レビューでは発見できなかった。

## SDK の価格方向

```
sqrtPriceX64ToPrice(sqrtPrice, decimalsA, decimalsB)
  → coinB per coinA を返す
```

| プール | coinA | coinB | 戻り値の意味 | 例 |
|--------|-------|-------|-------------|-----|
| USDC/SUI | USDC (6 dec) | SUI (9 dec) | SUI per USDC | ≈ 1.05 |
| CETUS/SUI | CETUS (9 dec) | SUI (9 dec) | SUI per CETUS | ≈ 0.15 |

**よくある誤解**: 戻り値を「coinA per coinB」（= SUI の USDC 建て価格）と解釈してしまう。

## 正しい使い分け

### tick 比較（レンジ判定）— そのまま使う

```typescript
// 全て同じ単位 (coinB per coinA) なので比較可能
const currentPrice = getCurrentPrice(pool, decimalsA, decimalsB)
const lowerPrice = tickToPrice(position.tickLowerIndex, decimalsA, decimalsB)
if (currentPrice < lowerPrice) { /* out of range */ }
```

### USD 換算 — 逆数にする

```typescript
// coinBPriceInCoinA() = 1 / getCurrentPrice() = USDC per SUI
const suiPriceUsdc = coinBPriceInCoinA(pool, DECIMALS_A, DECIMALS_B)
const suiValueInUsd = suiAmount * suiPriceUsdc
```

## なぜ 1:1 ペアで危険か

| SUI 市場価格 | getCurrentPrice (SUI per USDC) | 逆数 (USDC per SUI) | 差 |
|-------------|-------------------------------|---------------------|-----|
| $0.96 | 1.042 | **0.960** | 8.5% |
| $3.50 | 0.286 | **3.500** | 1124% |

BTC/USDC のような高額ペアでは逆数との差が桁違いになり即座に気づく。
SUI/USDC ($0.96) では差がわずか 8% で、「プール価格のずれかな」と見過ごしやすい。

## 実際の影響 (Issue #15)

```
修正前: 1556 SUI × $1.0439 = $1,624  ← 過大評価
修正後: 1556 SUI × $0.9579 = $1,491  ← aggregator 実績 $1,488 と一致
```

- スワップ量: 分子 / 分母の両方が誤った価格を使うため部分的に相殺 → 損失は軽微 (~$0.03)
- ログ: 全ての USD 表示が ~9% ずれ → 運用判断に支障
- Profitability gate: 比率計算なので相殺 → 判定への影響なし

## 価格変動時の CLMM ポジション挙動

### 取引所価格と pool currentPrice の関係

| 取引所 (Bybit/Binance 等) | CLMM pool `currentPrice` |
|---|---|
| SUI/USDC = 0.89 (1 SUI = $0.89) | SUI per USDC = 1/0.89 ≈ 1.124 |

取引所の SUI/USDC と pool の currentPrice は**逆数の関係**。
SUI 下落時に取引所価格は下がるが、pool の currentPrice（SUI per USDC）は上がる。

### SUI 下落時のポジション変化

```
SUI 下落 ($1.00 → $0.89)
  → 取引所: SUI/USDC 1.00 → 0.89 (下降)
  → pool: currentPrice 1.00 → 1.124 (上昇, tick 上昇)
  → tick がレンジ上端に近づく → ポジションは SUI heavy に変化
  → tick がレンジ上端を超える → ポジションは 100% SUI (coinB) に変換
```

| SUI の方向 | pool tick | ポジション構成 | 意味 |
|---|---|---|---|
| SUI 下落 | tick 上昇 ↑ | USDC → SUI に変換 | SUI が安くなるにつれ LP が SUI を蓄積（SUI heavy） |
| SUI 上昇 | tick 下降 ↓ | SUI → USDC に変換 | SUI が高くなるにつれ LP が USDC を蓄積（USDC heavy） |

> **直感的な説明**: CLMM は受動的マーケットメーカー。SUI 下落時はトレーダーが SUI を売って USDC を取り出すため、プールに SUI が蓄積され LP ポジションは SUI heavy になる。upper tick を超えると 100% SUI となりレンジアウト。

### Impermanent Loss (IL) の実際

CLMM の自動変換は、LP にとって**下落時に SUI を蓄積し、上昇時に利益確定するトレードオフ**:

- **SUI 下落時**: AMM が SUI を蓄積。SUI heavy になることで、回復時の上昇益を取り込む準備をする。一方でさらに下落すると損失が拡大する
- **SUI 上昇時**: AMM が USDC を蓄積（SUI を売り出す）。上昇益の一部を放棄する形になる（アップサイド制限）
- **回復時の IL**: 下落中に蓄積した SUI が「安値で買い、高値で売る」形になるため IL が発生
- **手数料で相殺**: LP 手数料収入 > IL であれば純利益。これが LP 運用の収益条件

### 実例 (2026-02-19 〜 02-23)

```
SUI 価格: $0.93 → $0.89 (-4.3%)
期初ポジション: ~$3,000 (394 USDC + 2,794 SUI)
                         ↓ SUI 下落 → tick 上昇 → SUI heavy 方向へ
                           ※ リバランスによる再開設でポジション構成がリセットされる場合あり
手数料収入: +$108.45 (5日間)

結論: 手数料収入が下落損の約 40% をカバー
```

> **注意**: 複数回のリバランスを跨いだ期間の構成変化は、純粋な CLMM 挙動だけでなく
> リバランス時のスワップも含むため、構成比の変化がシンプルな方向性を示さない場合がある。

## 予防策チェックリスト

1. **実データ検証**: 価格関数の出力を取引所 API (Bybit, Binance 等) の値と照合する
2. **変数名に方向を明記**: `suiPriceUsdc` ではなく `usdcPerSui` / `suiPerUsdc` の「X per Y」形式
3. **ヘルパー関数で用途を分離**: `getCurrentPrice()` (tick 比較用) と `coinBPriceInCoinA()` (USD 換算用)
4. **スワップ結果との突合**: `expected = amount × price` が aggregator 実績と ±1% 以内か確認
5. **新しいプール追加時**: coinA/coinB の順序とデシマルを必ず確認し、価格方向テストを追加する
