# 3年データ深層分析 (2022-11 ~ 2026-03)

## 概要

1年データ (0303分析) で得た知見を3年3ヶ月のデータで再検証し、
市場サイクル（Bear → Recovery → Bull → Consolidation）跨ぎでの普遍性を確認。
加えて、通常の分析では見落とされる非線形動態・クロスアセット構造を発掘。

## 1. Vol構造の普遍性（3年確認済み）

**結論: 全メトリクスがレジーム非依存（CV < 25%）**

| メトリクス | 値 | Bear | Recovery | Bull | Consolidation | 判定 |
|-----------|-----|------|----------|------|---------------|------|
| Vol AC(1h) | 0.984 | 0.976 | 0.985 | 0.987 | 0.989 | 🟢 不変 |
| Vol AC(4h) | 0.925 | 0.892 | 0.920 | 0.935 | 0.945 | 🟢 不変 |
| Vol AC(24h) | 0.41 | 0.30 | 0.38 | 0.45 | 0.53 | 🟡 微変動 |
| P(Low→Low) | 0.966 | 0.959 | 0.964 | 0.968 | 0.971 | 🟢 不変 |
| P(High→High) | 0.966 | 0.959 | 0.964 | 0.968 | 0.971 | 🟢 不変 |
| Extreme Clustering | 3.37x | 2.94 | 3.20 | 3.50 | 4.16 | 🟢 不変 |
| Range予測 r | 0.49 | 0.38 | 0.47 | 0.53 | 0.57 | 🟡 微変動 |
| Vol半減期 | 45h | 28-40h | 35-48h | 42-55h | 45-58h | 🟡 Bear時に速い |

**実用的含意**:
- Vol-basedの全戦略（スプレッドサイジング、レジーム検出、extreme alert）は市場環境問わず機能
- Vol半減期のみAdaptive推定が望ましい（固定パラメータでも致命的ではない）

## 2. クロスアセット情報フロー

### 2.1 Granger因果のレジーム依存性（重要発見）

| レジーム | BTC→ETH | BTC→SOL | ETH→SOL |
|---------|---------|---------|---------|
| 低Vol | F=12.65*** | F=15.05*** | F=8.2** |
| 高Vol | F=1.2 (NS) | F=2.1 (NS) | F=0.8 (NS) |

- **低Vol時のみ**、1-4h遅れの情報伝播が存在（BTC先行）
- **高Vol時は全ペア同時co-move**（因果性消滅）
- **MM戦略への含意**: 低Volレジームではクロスアセットシグナルでヘッジタイミング最適化可能

### 2.2 下方Vol伝染の非対称性

BTC Vol spike後のクロスアセット相関:
- 下落時: +0.066pp at t+0 → +0.043pp at t+6h（6時間持続）
- 上昇時: ベースライン並み

**含意**: 下落Vol shockはMM risk limitを即時発動すべき。上昇Vol shockは猶予あり。

### 2.3 Volume Surprise → Forward Returns

BTC/ETH/SOL Volume Q5 (spike) → Forward 4h Returns:
- BTC: +5.92bp
- ETH: +5.30bp
- SOL: **+10.94bp**

単調的パターン（Q4→Q5で急増）。SOLで最も顕著。
→ 厳密なOOS検証が必要だが、Volume spike後の短期ポジティブドリフトは有望。

## 3. Drift Protocol 構造分析

### 3.1 Oracle-CEX 乖離

| 年 | 平均(bp) | Std(bp) | |乖離|>10bp比率 |
|----|----------|---------|---------------|
| 2023 | +3.2 | 14.5 | 35% |
| 2024 | +2.0 | 9.8 | 24% |
| 2025 | +1.5 | 7.2 | 18% |
| 2026 | +1.0 | 6.4 | 15% |

- **平均回帰、半減期0.66h**
- 極端時（>20bp）に33bp — asymmetric quoting で捕獲可能
- **エッジは年々縮小中** — Driftの流動性改善を反映

### 3.2 vAMM スリッページの本質

- **vol相関**: Spearman r=0.16 (p < 1e-100)
- **volume相関**: r=0.001 (NS)
- **2026年の構造変化**: fill price < oracle price (-5.6bp平均) → maker有利レジーム

**MM戦略への直接的含意**:
- スプレッドはvolに連動させる（volumeではない）
- 高volume時にクオートを引くのは不要

### 3.3 最適MMウィンドウ

| UTC時間帯 | Drift/Binance比率 | 推奨 |
|----------|------------------|------|
| 19:00-22:00 | 1.17-1.31x | 🟢 集中稼働 |
| 12:00-18:00 | 0.95-1.05x | 🟡 通常稼働 |
| 07:00-11:00 | 0.77-0.83x | 🔴 縮小/停止 |

### 3.4 FR相互予測

Drift premium → Bybit FR(t+1): r=0.33
Bybit FR → Drift premium(t+1): r=0.37

→ FR divergenceを使ったポジション調整に利用可能。

## 4. 非線形ボラティリティ動態

### 4.1 GARCH残差α（SOLのみ有意）

- SOL +2σ GARCH surprise → 24h forward: **+63bp (t=3.08)**
- ETH: 有意差なし
- **解釈**: SOLはvol shockに対する市場の織り込みが遅い（低効率）

### 4.2 Vol-of-Vol 2x2マトリクス（新概念）

| | Low VoV | High VoV |
|-|---------|----------|
| **Low Vol** | 平穏（低収益MM） | レジーム遷移の前兆 |
| **High Vol** | **最良MM環境** | 危険（SOL +60bp方向性ドリフト） |

- **High Vol + Low VoV**: 予測可能な広スプレッド → MMの理想条件
- **High Vol + High VoV**: Vol自体が不安定 → リスク制限発動すべき
- **VoVはVol単独よりも優れたリスクフィルター**

### 4.3 ジャンプ検出

| 通貨 | 全期間Jump率 | Bear | Bull | Post-Jump Vol上昇 |
|------|------------|------|------|-----------------|
| SOL | 5.1% | 6.2% | 3.6% | p=0.0004 ✅ |
| ETH | 10.5% | 11.2% | 9.8% | p=0.81 NS |

SOLのjump後はvol上昇が有意 → ヘッジトリガーとして使用可能。

## 5. AS Model 3年バックテスト（決定的結論）

| 通貨 | 2023 Sharpe | 2024 Sharpe | 2025 Sharpe | 2026 Sharpe |
|------|------------|------------|------------|------------|
| SOL (2bp fee) | -2.65 | -1.42 | -0.12 | -1.73 |
| ETH (2bp fee) | -3.01 | -2.58 | -1.05 | -1.44 |

**損益分岐スプレッド**:
| 条件 | SOL | ETH |
|------|-----|-----|
| 2bp maker fee | ~195bp | ~195bp |
| 0bp maker fee | ~85bp | ~130bp |

**CEX MMは3年データで完全に否定された。**
Drift (10bp vAMM spread, -0.25bp rebate) でも85bp breakeven には届かないが、
capture rate次第では損益分岐に近づく可能性がある。

## 6. 戦略推奨の更新

### 即座に実装すべき（3年確認済み）

1. **Vol-of-Vol フィルター**: High Vol + Low VoV 時のみMM稼働
   - ETHではSharpe +0.20改善。SOLではHV-HVV が paradoxically profitable のため要注意
2. **下方Vol contagion 即時リスク制限**: BTC下落vol spike → 全通貨のリスク縮小
3. **Driftクオート**: vol連動スプレッド、19:00-22:00 UTC集中稼働
4. **SOL GARCH surprise後のポジション**: +2σ surprise → 24h方向ポジション (+63bp期待)
5. **Volume Surprise シグナル** (OOS検証済み、Bonferroni通過): 詳細は下記7章

### 追加検証が必要

1. **低Vol時クロスアセットGranger**: 実行可能性（1h遅れは十分か）
2. **Drift capture rate**: ペーパートレードで実測

### 棄却（3年データで再確認）

- CEX MM（全条件マイナス）
- 方向性α全般（1年結論を3年が支持）

## 7. Volume Surprise シグナル（OOS検証済み）

**プロジェクト初の確認済みトレーダブルシグナル。**

### Walk-Forward OOS 結果

| 通貨 | Hold | Q5 Mean (bp) | t-stat | Bonferroni |
|------|------|-------------|--------|------------|
| BTC | 4h | +7.52 | 3.93 | ✅ 通過 |
| BTC | 8h | +14.37 | 5.58 | ✅ 通過 |
| ETH | 8h | +17.19 | 5.26 | ✅ 通過 |
| SOL | 4h | +10.99 | 3.19 | ✅ 通過 |
| SOL | 8h | +24.95 | 5.39 | ✅ 通過 |

### クロスアセット予測（最強シグナル）

BTC vol Q5 → SOL 8h forward: **+31.57bp (t=7.23)**
ETH vol Q5 → SOL 8h forward: **+31.53bp (t=7.53)**

クロスアセットの方がwithin-assetより強い → BTC/ETH volume spike → SOL long が最適戦略。

### 条件

1. **高Vol時のみ有効**: 低Vol時は ~0bp（シグナルなし）
2. **Hold period**: 1hは無効、4h以上が必要
3. **Q5のみ**: 中間quintileは uninformative
4. **2bp taker cost後も profitable**: SOL 8h net +22.95bp

### 推奨実装

```
IF rvol_24h > trailing_90d_median:
  IF btc_volume_zscore > Q5_threshold (or eth_volume_zscore > Q5):
    LONG SOL, hold 4-8h
```

## 8. Drift マイクロ構造分析

### Drift vs Binance 構造比較

| 特性 | Binance | Drift | 含意 |
|------|---------|-------|------|
| Trending割合 | 20% | 39% | Drift は方向性が出やすい |
| Trending持続時間 | 2.8h | 5.1h | Drift のトレンドは長い |
| 効率性比率 (ER) | 1.085 | 0.975 | Drift はジャンプ多い |
| ER>1 (smooth) 比率 | 85.8% | 32.2% | Drift の2/3はgappy |

**含意**: Drift MM は Binance 比で広いベーススプレッドが必要。ER < 1 の環境ではadverse selectionが構造的に高い。

### ER のトレンド（改善中）

| 年 | Drift-Binance ER差 |
|----|-------------------|
| 2023 | -0.19 |
| 2024 | -0.12 |
| 2025 | -0.08 |
| 2026 | -0.05 |

→ Drift のマイクロ構造は年々改善。将来的にはBinance並みのER に収束する可能性。

### 最適リバランス頻度

| ラグ | Vol情報量 (R²) | 推奨 |
|------|--------------|------|
| 1h | 0.97 | 高頻度アップデート |
| 8h | 0.74 | 有意義 |
| 24h | 0.25 | 最低限のアップデート間隔 |
| 48h | 0.12 | 限界以下 |

**推奨: Volパラメータは最低24h毎に更新。理想は1-8h毎。**

### レジーム遷移の予測可能性

**不可能。** 遷移は急激で、事前兆候がない（delta_z が負 = 遷移直前にむしろ安定方向）。
→ レジームの「検出」は即座に可能だが「予測」は不可。反応速度が重要。

## 9. FR-Biased Inventory 戦略

### Drift FR 構造的プレミアム

- Drift 累積8h FR: 平均 +1.32%（Bybitの0.006%と比較して桁違い）
- FR divergence: 平均 +1.31% per 8h = **14.36% APR**
- 高持続性: AC=0.85 (8h), AC=0.51 (1w)
- 極端値からは平均回帰 (P90→次期間 -0.94%, t=-3.65)

### FR-Biased MM Backtest結果

reservation_price に FR bias を追加:
`r = mid - q*gamma*sigma²*tau + alpha * FR_EMA(8h)`

| alpha | Sharpe | Total PnL | MaxDD |
|-------|--------|-----------|-------|
| 0 | 0.00 | 0.00 | — |
| 50 | 0.72 | 108.32 | -58.15 |
| **100** | **0.98** | **182.65** | **-82.22** |
| 200 | 0.90 | 224.32 | -116.82 |
| 500 | 0.65 | 198.71 | -201.45 |

**alpha=100 が最適。PnLの27%がFR収入。**

### Mark-Oracle Premium

- Price方向予測力: R²=0.05% (1h), 0.08% (8h) → **使えない**
- asymmetric quoting への応用: hit rate差 1.4% → **不十分**

## 10. Adverse Selection 分解（Glosten-Milgrom）

### Bid-Ask 情報非対称性

| Spread | Bid PI (4h) | Ask PI (4h) | 差分 | 解釈 |
|--------|-----------|-----------|------|------|
| 5bp | +8bp | -4bp | 12bp | buy-side informed |
| 25bp | +6bp | -6bp | 12bp | 安定 |
| 50bp | +5bp | -7bp | 12bp | 安定 |

**buy-side が構造的に informed**（65%のDrift fillが CEX price 上）。

### 最強AS予測因子: モメンタムコンテキスト

| 条件 | Bid PI shift | Volume regime shift | Vol regime shift |
|------|-------------|-------------------|-----------------|
| 値 | **24.1bp** | 7.0bp | 6.5bp |

- Up-momentum: bid PI = +17.9bp（buy-side 猛毒）
- Down-momentum: bid PI = -6.2bp（sell-side 毒）

**→ 直近4hリターン方向でクオートの非対称性を制御すべき。**

### 時間帯別AS

| UTC | 支配的フロー | 推奨 |
|-----|-------------|------|
| 9-13 | sell-side informed | ask側を広げる |
| 14-18 | バランス（低AS） | **最適クオーティング窓** |
| 19-22 | buy-side informed | bid側を広げる |

### Drift AS のトレンド

| 年 | Fill-CEX Premium (bp) |
|----|---------------------|
| 2023 | +10.6 |
| 2024 | +5.2 |
| 2025 | +1.3 |
| 2026 | **-7.3** |

→ 2026年はfill < CEX（maker有利レジーム）。AS環境は改善中。
