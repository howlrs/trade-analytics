# $3,000帯 Cetus CLMM LP運用戦略 — 統合分析レポート

**Date**: 2026-02-19
**Issue**: #29
**Position**: ~$3,000 USDC/SUI on Cetus CLMM (0.25% fee tier, Sui mainnet)

---

## Executive Summary

3つの独立分析（プール構造、リバランスTX、手数料/IL）を統合した結果、**$3,000規模では現在のナローレンジ＋積極リバランス戦略は赤字構造**であることが確認された。

**核心的発見**:
- リバランス1回あたりのコスト: **~$3.76**（99.8%がスワップ手数料）
- 実測フィー収入: **$0.30〜$0.80/日**（理論値の1/100以下）
- 損益分岐リバランス頻度: **月3〜4回が上限**
- 現在のナローレンジ(±3%)では月20〜30回リバランス → **月$50〜$90の赤字**

**結論**: ワイドレンジ(±10〜15%) + 最小リバランス戦略に移行すべき。

---

## 1. プール概況

| Metric | Value |
|---|---|
| Pool ID | `0xb8d7d9e66a60c239e7a60110efcf8de6c705580ed924d0dde141f4a0e2c90105` |
| TVL | ~$5.08M (USDC 1.2M + SUI 4.15M) |
| 24h Volume | $2.8M〜$5.0M |
| Active Positions | 9,959 |
| Fee Rate | 0.25% |
| Tick Spacing | 60 |
| SUI Price | ~$0.934 |
| Reward Emissions | CETUS (~0.427/s) + SUI (~0.485/s) |
| Base Fee APR (全プール) | ~70% |

### Cetus ハック後のリスク
- 2025年5月: $223M exploit、6月再開
- 現在TVLは被害前の~50% (~$120M)
- 補償はCETUSトークンベスティング（2026年6月まで）
- **$3K以上の増資はTVL $200M回復まで非推奨**

---

## 2. 理論値 vs 実測値の乖離（最重要）

pool-analyzer は理論APRを算出したが、**実測値との乖離が100倍以上**ある。

| Range | 理論 Daily Fee | 実測 Daily Fee | 乖離倍率 |
|---|---|---|---|
| ±3% | $194.52 | $0.50〜$1.50 | ~130〜390x |
| ±5% | $117.82 | $0.30〜$0.90 | ~130〜390x |
| ±10% | $60.24 | $0.10〜$0.30 | ~200〜600x |

**乖離の原因**:
1. **競合LP**: 9,959ポジションがアクティブtickで競合。$3Kは全体の0.06%に過ぎない
2. **流動性集中**: 大口LP(whale)がアクティブtick付近に集中し、小口のフィーシェアを圧縮
3. **レンジアウト時間**: 理論値は100%イン・レンジを前提。SUIの日次ボラ5-6%では±3%レンジは1日持たない
4. **初回監査実績**: LP fee $0.072 vs swap cost $0.117 = **ネットマイナス**

> **教訓**: CLMM の理論APR は「そのtickに自分しかいない」前提の上限値。実運用では1/100〜1/1000になりうる。常に実測データで検証すること。

---

## 3. コスト構造分析

### リバランスコスト（1回あたり）

| Component | Cost | 比率 |
|---|---|---|
| Gas (3 TX) | $0.009 | 0.2% |
| Swap Fee (0.25% × ~$1,500) | $3.75 | 99.8% |
| Price Impact | <$0.01 | ~0% |
| **合計** | **~$3.76** | |

### 損益分岐分析

| Fee Rate | Break-Even | Max Rebalances/Month |
|---|---|---|
| $0.30/day (保守的) | **12.5日** | 2.4回 |
| $0.50/day (中央値) | **7.5日** | 4.0回 |
| $0.80/day (楽観的) | **4.7日** | 6.4回 |

### レンジ幅別の月次損益

| Range | Est. Rebalances/Month | Monthly Fee | Monthly Swap Cost | **Net** |
|---|---|---|---|---|
| ±3% | 20-30 | $22.50 | $75-$112 | **-$52 to -$90** |
| ±5% | 8-15 | $15.00 | $30-$56 | **-$15 to -$41** |
| ±8% | 3-6 | $9.00 | $11-$22 | **-$2 to -$13** |
| ±12% | 1-3 | $6.00 | $3.75-$11 | **-$5 to +$2** |
| ±15% | 0.5-2 | $4.50 | $1.88-$7.52 | **-$3 to +$2.62** |

**±12〜15%が唯一の損益分岐ゾーン**。リワード報酬(CETUS+SUI)を加味すれば黒字化の可能性あり。

---

## 4. コンパウンド分析

### 結論: コンパウンド頻度はほぼ無関係

| 頻度 | APY (8% APR時) | 年間差 |
|---|---|---|
| 毎時 | 8.33% | baseline |
| 2時間毎 | 8.33% | <$0.15 |
| 毎日 | 8.32% | <$0.15 |

$3K × 8% APRでは、hourly vs dailyのコンパウンド差は**年間$0.15以下**。
Suiのgas($0.003)が安いのでチェック頻度は現状維持で問題ないが、コンパウンドに最適化の余地はない。

**推奨**: `HARVEST_THRESHOLD_USD` を $3.0 → $0.50 に引き下げ（実害なし、微小な複利効果を取得）

---

## 5. Impermanent Loss (IL) 分析

### 集中流動性のIL増幅

| Range | IL増幅倍率 | 5%価格変動時のIL |
|---|---|---|
| Full Range (v2) | 1x | $1.80 |
| ±3% | ~33x | $60 |
| ±5% | ~20x | $36 |
| ±8% | ~12.5x | $22 |
| ±15% | ~7x | $12.60 |

### IL回収に必要な時間

| Range | IL (5%変動時) | Daily Fee | 回収日数 |
|---|---|---|---|
| ±5% | $36 | $0.50 | **72日** |
| ±8% | $22 | $0.35 | **63日** |
| ±15% | $12.60 | $0.20 | **63日** |

> SUIの日次ボラ5-6%では、ほぼ全てのレンジで「IL回収前に次のレンジアウト」が発生する。IL単体では回収不能で、リワード報酬込みでの黒字化が必須。

---

## 6. プロフェッショナルVault戦略の比較

| Protocol | Chain | 戦略 | リバランス頻度 | 特徴 |
|---|---|---|---|---|
| **Kriya** | Sui/Cetus | 2段階（reset+target range） | 保守: 5%トリガー/±20%レンジ | Sui上の直接競合 |
| **Arrakis** | Ethereum | Monte Carlo週次 | 週1回以下 | 片側偏り時のみリバランス |
| **Charm** | Ethereum | パッシブ（limit orders） | 受動的 | **スワップ手数料を稼ぐ側に回る** |
| **Gamma** | Multi-chain | 動的レンジ | 価格ベース | Position expansion使用 |
| **Kamino** | Solana | 20分チェック | ペア依存 | 安定ペアはほぼリバランスなし |
| **Cetus Vaults** | Sui | ワイドレンジ | 低頻度 | 日次fee harvest |

### 重要な教訓
1. **全てのプロVaultが当プロジェクトより広いレンジを使用**
2. Charm式パッシブリバランス（limit orders）は**コストを収益に転換**できる革命的アプローチ
3. プロVaultは多数の預入者でコストを分散 — ソロLP($3K)にはこの優位性がない
4. Position expansion（close/swap/openではなくadd/remove liquidity）でスワップコスト削減

---

## 7. 推奨パラメータ変更

### 統合推奨値（3分析の中央値・保守寄り）

| Parameter | Current | Recommended | Rationale |
|---|---|---|---|
| `volTickWidthMin` | 240 | **480** | ±2.5% → 月8-15回リバランスを月3-6回に削減 |
| `volTickWidthMax` | 600 | **1200** | ±3.1% → ±6.4%。高ボラ時の過剰リバランス防止 |
| `narrowRangePct` | 0.03 | **0.08** | ±3%は日次ボラ(5-6%)以下。最低±8%必要 |
| `wideRangePct` | 0.08 | **0.15** | ±8%でも月3-6回リバランス。±15%で月0.5-2回 |
| `rebalanceThreshold` | 0.10 | **0.03 or disable** | 10%トリガーは価値破壊。フィー獲得中のリバランスを防止 |
| `maxBreakevenHours` | 12 | **48** | 12hでは$0.05/hrで承認。48hで実質月3-4回に制限 |
| `COOLDOWN_UP_SEC` | 300 | **1800** | 5分→30分。ボラ時の連続リバランス防止 |
| `COOLDOWN_DOWN_SEC` | 600 | **3600** | 10分→60分。20-30%のレンジアウトは自己修復 |
| `HARVEST_THRESHOLD_USD` | 3.0 | **0.50** | $3.0では6-10日待ち。$0.50で~1日毎にコンパウンド |

### 新規パラメータ（追加推奨）

| Parameter | Value | Purpose |
|---|---|---|
| `MAX_REBALANCES_PER_DAY` | 3 | 日次リバランス上限。超過時は待機 |
| `MIN_TIME_IN_RANGE_SEC` | 7200 (2h) | レンジ内2時間未満でのリバランス禁止 |
| `WAIT_AFTER_RANGEOUT_SEC` | 1800 (30min) | レンジアウト後30分待機（自己修復待ち） |

---

## 8. 戦略ロードマップ

### Phase 1: 即時対応（パラメータ調整のみ）
- [ ] レンジ幅パラメータをワイド化 (volTickWidthMin=480, max=1200)
- [ ] profitability gate を強化 (maxBreakevenHours=48)
- [ ] クールダウン延長 (up=1800s, down=3600s)
- [ ] threshold trigger を無効化 or 0.03に縮小
- [ ] harvest threshold 引き下げ ($0.50)
- [ ] 新ガードレール追加 (max rebalances/day, min time in range, wait after rangeout)

### Phase 2: 中期改善（コード変更）
- [ ] リワード報酬(CETUS+SUI)を含めた損益計算の実装
- [ ] 累積P&Lトラッカー（IL実測含む）
- [ ] Compound-only mode: 低ボリューム時はリバランススキップ
- [ ] Position expansion: close/swap/open → add/remove liquidity

### Phase 3: 長期目標（アーキテクチャ変更）
- [ ] Charm式パッシブリバランス: 片側流動性ポジションをlimit orderとして活用
- [ ] 外部価格シグナル統合（CEX価格、ボラティリティ指標）
- [ ] $3K→$10K スケーリング（資本効率が改善する閾値の検証）

---

## 9. スケーリング展望

| Capital | Rebalance Cost | Daily Fee (est.) | Break-Even | Viable Strategy |
|---|---|---|---|---|
| $3K | $3.76 | $0.30-$0.80 | 5-12日 | ワイドレンジ、月2-3回リバランス |
| $10K | $12.50 | $1.00-$2.70 | 5-12日 | 中レンジ、月4-8回可能 |
| $30K | $37.50 | $3.00-$8.00 | 5-12日 | ナローレンジが初めて viable |
| $100K | $125 | $10-$27 | 5-12日 | 積極的リバランスが有効 |

> スワップ手数料は資本に比例するため、**損益分岐日数はスケールしない**。ナローレンジの積極運用が viable になるのは$30K以上。

---

## 10. パッシブリバランス（Charm式）— ゲームチェンジャー

### 核心アイデア
従来: スワップして0.25%を**支払う** → -$3.75/回
Charm式: 片側流動性で0.25%を**稼ぐ** → +$3.75/回
**差額: 1回あたり$7.50**

### Cetus上での実装可能性: **確認済み**
- Cetus は range order（片側流動性）をネイティブサポート
- 既存の `openPosition()` で `tick_lower`/`tick_upper` を現在価格の片側に配置、反対トークンを `'0'` に設定するだけ
- **SDK変更不要**

### 推奨実装: Passive-First, Active-Fallback

```
レンジアウト検出時:
  1. 余剰トークンを特定（例: SUI余剰 → 下方にlimit order配置）
  2. 片側流動性ポジションを現在価格付近に配置
  3. 4〜6時間待機（トレーダーの自然な注文で充填）
  4. 充填完了 → スワップなしで新ポジション開設（コスト: ガスのみ $0.012）
  5. タイムアウト → 従来のアクティブリバランスにフォールバック
```

### 期待される充填率: 60〜80%（USDC/SUI は高ボリュームプール）

| | Active Rebalance | Passive Rebalance |
|---|---|---|
| スワップコスト | -$3.75 | $0 〜 +$3.75 |
| 確実性 | 100%（即座） | 60-80%（数時間） |
| 1回あたりの節約 | — | $3.76〜$7.50 |

---

## 11. スケーリング: $3K → $10K+ の現実

### 重要な発見: 収益はポジションサイズに**比例**しない（閾値なし）

Suiではガスが安い($0.003)ため、Ethereum のような「最低ポジションサイズ」が存在しない。
一方、スワップ手数料(0.25%)は比率ベースなので、**ポジションサイズを増やしても損益分岐日数は変わらない**。

| Range | Rebalances/Mo | Monthly Net Rate | $3Kでの損益 | $10Kでの損益 |
|---|---|---|---|---|
| ±5% | 10 | -0.75% | -$22.50 | -$75.00 |
| ±8% | 4 | -0.20% | -$6.00 | -$20.00 |
| ±12% | 2 | -0.05% | -$1.50 | -$5.00 |
| ±15% | 1 | **+0.025%** | **+$0.75** | **+$2.50** |

**±15%以上が黒字化の唯一のゾーン**（リワード報酬除く）。

### Tick range は immutable（プロトコルレベル）
- 既存ポジションのtick範囲変更は不可能。close/swap/openが唯一の方法
- Position expansion（Pulse V2）= 追加ポジション開設であり、既存の変更ではない
- $3Kでは複数ポジション管理のオーバーヘッドが収益を超えるため非推奨

---

## 12. 詳細分析ドキュメント

- [Pool Position Analysis](./pool-position-analysis.md) — プール構造・理論APR・ポジション分布
- [Rebalance Pattern Analysis](./rebalance-pattern-analysis.md) — リバランス戦略・プロVault比較・コスト分析
- [Fee/Compound/IL Analysis](./fee-compound-il-analysis.md) — 手数料最適化・IL測定・コンパウンド頻度
- [Passive Rebalance Research](./passive-rebalance-research.md) — Charm式パッシブリバランス・Kriya Vault詳細
- [Scaling & Expansion Strategies](./scaling-and-expansion-strategies.md) — スケーリングパス・Position Expansion分析

---

## Sources

- Kriya CLMM Vault Strategy: https://docs.kriya.finance/
- Kamino Ranges & Rebalancing: https://docs.kamino.finance/
- Gauntlet Uniswap ALM Analysis: https://www.gauntlet.xyz/
- Concentrated Liquidity Research (ETH Zurich): https://arxiv.org/pdf/2110.01368
- Backtesting CLMM (Uniswap V3): https://arxiv.org/abs/2410.09983
- Cetus Developer Docs: https://cetus-1.gitbook.io/cetus-developer-docs
- GeckoTerminal / DexPaprika pool analytics

*Analysis date: February 19, 2026*
