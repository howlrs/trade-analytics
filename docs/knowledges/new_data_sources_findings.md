# 新規データソース探索の知見

作成日: 2026-03-02
分析ノートブック: `analyses/20260302_0400_new_features_exploration/analysis_new_features.py`

## 背景

OHLCV線形アルファ・デリバティブ主導シグナル（FR/OI）ともにウォークフォワードで崩壊。
方針転換として「新しい情報源」を3つ投入し探索的分析を実施。

## 検証データソース

| データ | API | 頻度 | 認証 |
|--------|-----|------|------|
| Fear & Greed Index | alternative.me | 日次 | 不要 |
| Stablecoin MCap (Total/USDT/USDC) | DefiLlama stablecoins | 日次 | 不要 |
| 先物ベーシス (Mark - Index) | Binance fapi | 1h | 不要 |

## 結果サマリー

### 1. Fear & Greed Index — 棄却

- F&G < 20 (Extreme Fear) → Long の仮説を検証
- Test期間で全トークン **マイナスリターン**（BTC -63bps, SUI -118bps / 24h, p<0.001）
- 方向が仮説と逆: Fear時の購入は損失を拡大した
- 五分位の単調性: Train/Test 符号一致率 37%（8ペア中3）
- **解釈**: 2025後半〜2026の分析期間では「低F&G = 本格的な下落トレンド」であり逆張りが機能しなかった

### 2. ステーブルコイン供給 — 完全棄却

- SC MCap 7日変化率: **全8ペアでTrain/Test符号不一致**
- USDT 7日変化率: ETHのみ微弱な正の一致、他は全てNG
- 日次データの解像度不足 + 供給変化→価格への因果経路が弱い

### 3. 先物ベーシス — 部分的に有望

**安定したパターン**:
- BTC basis_rate → 1h: 負の単調性 Train=-0.6, Test=-0.6（高ベーシス → 短期リターン低下）
- SUI basis_rate → 24h: Train=-0.8, Test=-0.8
- BTC basis_zscore → 1h: Test=-0.8

**ただし**:
- Z-score ±2σ イベント分析ではほぼ全て非有意（p>0.1）
- 中期（8h, 24h）では符号不安定化
- 効果量が小さく、取引コスト考慮で収益化困難

### 4. クロスアセット・複合効果

- BTC basis → ALT リターン: 先行指標仮説は不成立（符号一致ほぼ全滅）
- Greed + High Basis: BTC/SOL/SUIで有意なマイナス（p<0.05）だが N=10〜13 の極小サンプル

## ウォークフォワード全体

- 符号一致率: **25%**（84ペア中21）— ランダム期待値50%を大幅に下回る
- 有望候補（符号一致 & |単調性|>0.3）: 11件中、実用候補は BTC basis 1h のみ

## Binance API メモ

- `markPriceKlines`: `symbol` パラメータ (例: `BTCUSDT`)
- `indexPriceKlines`: `pair` パラメータ (例: `BTCUSDT`) — **symbol ではなく pair**
- 両方とも認証不要、limit=1500

## DefiLlama Stablecoin API メモ

- `stablecoincharts/all`: 総MCap。`totalCirculatingUSD.peggedUSD` がUSD建て総額
- `stablecoin/{id}`: 個別コイン。`chainBalances.{chain}.tokens[].circulating.peggedUSD` を全チェーン合算
- USDT=id:1, USDC=id:2

## 教訓

1. **日次データの限界**: F&G/Stablecoin は日次 → 1h OHLCVと結合すると同一値が24h続くため、独立した情報が少ない
2. **マクロ指標は短期トレードに不向き**: F&G/Stablecoin供給はレジーム判定には使えても、8h/24h の方向性シグナルとしては弱すぎる
3. **Basis は FR の高解像度版として有用**: ただし単独シグナルではなくフィルタ条件として使うのが現実的
4. **逆張りの罠**: 「極端値 = 反転」は直感的だが、トレンド相場では極端値がさらに極端になる
