# 戦略推奨: 全調査の統合 (2026-03-03, 1m分析追加)

## 調査サマリー

### 検証規模
- **22手法** の方向性α探索 (OHLCV線形、デリバティブ、ML、オンチェーン、XS、Carry、ペア、カレンダー、FR Linear等)
- **4通貨** × **複数ホライズン** × **Walk-Forward検証**
- **MM戦略**: 戦場選定、Avellaneda-Stoikovモデル、逆選択、スプレッド捕獲、条件付きMM
- **1分足分析**: ボラティリティ構造、MM在庫シミュレーション、逆選択定量、損益分岐手数料、複合シグナル
- **リスク管理**: 動的ポジションサイジング、テール分布分析、極端イベント検出
- **統計的厳密性**: Newey-West HAC補正、重複排除、Bonferroni補正

### 大局的結論
> **1年分のOHLCV (1h) + 3ヶ月の1m足 + デリバティブ + オンチェーンデータでは、
> 方向性αもMM αも統計的に頑健な水準で検出できない。**
> ただし、ボラティリティ予測と構造パターンは堅牢で、リスク管理・運用最適化に活用可能。
>
> **MM損益分岐にはゼロ以下のmaker手数料（VIP5+相当）が必要条件。**

---

## Tier 1: 堅牢な知見 (即時活用可能)

### 1.1 ボラティリティ予測
| 指標 | 1h足 | 1m足 | Train/Test一致 | 用途 |
|------|------|------|--------------|------|
| Vol AC lag=1step | r=0.98 | r=0.90-0.92 | ✅ | 次ステップのボラ≈現在のボラ |
| Vol AC lag=24h | r=0.30-0.49 | — | ✅ | 24h先のボラ推定 |
| Range→Next Range | r=0.44-0.53 | **r=0.64-0.71** | ✅ | スプレッド幅の設定 |
| Vol→Range | r=0.36-0.46 | — | ✅ | rvol_24hからレンジ推定 |
| Volume→Next Range | r=0.28-0.29 | — | ✅ | 出来高急増後のレンジ拡大 |

**1分足のRange予測はr=0.64-0.71で、1h足(r=0.44-0.53)より大幅に改善。**

**活用法**:
```
spread = max(min_spread, α × rvol_15m × √hold_minutes)
position_size = target_risk / (rvol_24h × √(hold_hours/24))
```

### 1.2 時間帯パターン
| 時間帯 (UTC) | Range比 | 出来高比 | MM行動 |
|-------------|---------|---------|--------|
| 13-16 (US Open) | 1.8-2.0x | 高 | スプレッド拡大しつつ高頻度 |
| 04-06 (Asia Night) | 0.6-0.8x | 低 | スプレッド縮小、低頻度 |
| Weekend | 0.63-0.78x | 0.6-0.7x | リスク縮小 |

### 1.3 クロスアセット相関
| ペア | 相関 | 非相関率 | ヘッジ有効性 |
|------|------|---------|------------|
| BTC-ETH | 0.82 | 0.3% | 極めて高い |
| BTC-SOL | 0.79 | 0.0% | 高い |
| BTC-SUI | 0.75 | 0.7% | 高い |

→ ETH/SOL のMM在庫はBTCでデルタヘッジ可能

### 1.4 レジーム持続性
| 粒度 | 低ボラ→低ボラ | 高ボラ→高ボラ |
|------|-------------|-------------|
| 1h足 | 95.6% | 95.8% |
| 1m足 | **98.7-99.1%** | **98.8-99.0%** |

→ 1分足ではレジーム持続性がさらに高い。レジーム判定後、安定的に運用可能。

### 1.5 極端イベント・クラスタリング
| 粒度 | 閾値 | 無条件確率 | 条件付き確率 | 倍率 |
|------|------|-----------|------------|------|
| 1h | |z|>2 | 5.2-5.6% | 15.6-17.2% | **3.1x** |
| 1h | |z|>3 | 1.5-1.9% | 9.4-11.4% | **5.2-6.3x** |
| 1m | |z|>2 | — | — | **2.2-2.6x** |

→ 極端イベント後は次の極端イベント確率が2-6倍。即時スプレッド拡大/ポジション縮小が有効。

### 1.6 Vol変化のミーンリバージョン
- past_vol_chg→future_vol_chg: r=-0.27〜-0.37 (Train/Test一致)
- ボラスパイク後は正常化を見込める
- ボラ圧縮後は「そのまま低ボラ」が最有力 (ブレイクアウトではない)

### 1.7 動的ポジションサイジング効果
| 比較 | MaxDD削減 | Sharpe改善 |
|------|----------|-----------|
| Vol-adjusted vs Equal | 55-68% | +0.5-0.9 |
| Extreme-aware追加 | +29-43% | +0.2-0.3 |
| Full model (vol+time+regime) | BTC: -1.88→-0.61 | ETH: -2.10→-1.19 |

`src/risk.py` として実装済み (18テスト全通過)。

### 1.8 テール分布
- Excess Kurtosis: BTC=11.6, ETH=12.8, SOL=12.6, SUI=17.7 (正規分布=0)
- 1% VaR: BTC -148bp, ETH -222bp, SOL -246bp, SUI -282bp
- Rolling VaR (24h) のbreach率: 1.9-2.2% (目標1%を超過 → 保守的な係数設定が必要)

### 1.9 Roll推定スプレッド (1m足, NEW)
| 通貨 | Binance | Bybit | 中央値レンジ |
|------|---------|-------|------------|
| ETH | 4.3bp | 4.9bp | 11.2bp |
| SOL | 3.0bp | 3.3bp | 13.3bp |
| BTC | 3.1bp | 3.0bp | 8.4bp |

→ Binance全通貨でBybitよりタイト。SOLが最もスプレッドが狭い。

### 1.10 逆選択の定量 (1m足, NEW)
| 通貨 | k=1.0 bid fill後1m | 5m | 15m |
|------|-------------------|-----|------|
| ETH | -1.1bp | -1.1bp | -1.8bp |
| SOL | -1.0bp | -1.3bp | -2.3bp |
| BTC | -1.0bp | -1.0bp | -1.3bp |

→ 全通貨で逆選択確認。fill後15分で1-3bp下落（momentum-like）。

---

## Tier 2: 限定的に有望 (追加検証必要)

### 2.1 XS Composite (Basis+FR) → fwd_1d
- Walk-Forward: Sharpe=1.77, mean=26bp/day
- **p=0.11** (5%水準で非有意)
- 9 fold中6勝 — fold間分散が大
- コスト控除後: 22bp/day

**推奨**: データ蓄積 (あと6ヶ月) で再検証。現時点では実弾投入不可。

### 2.2 MM戦場選定 (1m足分析追加)

**最終結論 (1h + 1m統合)**:

| 順位 | 通貨 | 取引所 | 根拠 |
|------|------|--------|------|
| **1** | **SOL** | **Binance** | Roll最小(3.0bp), フィル率最高(67.6%@10bp), 損益分岐に最も近い(0bp fee) |
| 2 | ETH | Binance | 流動性最大, 出来高安定, Roll=4.3bp |
| 3 | BTC | Binance | スプレッドタイトだがフィル率低(41.2%@10bp) |

**MM損益分岐手数料** (AS inventory model, k=1.5):
| 通貨 | Train (0bp fee) | Test (0bp fee) | Train/Test一致 |
|------|----------------|----------------|--------------|
| SOL | -190bp/day | +10bp/day | ❌ 符号反転 |
| ETH | -222bp/day | -138bp/day | ✅ 両方マイナス |
| BTC | -264bp/day | -87bp/day | ✅ 両方マイナス |

→ **標準手数料(2bp maker)では全通貨マイナス。ゼロ手数料でもSOL以外はマイナス。**
→ **SOLの損益分岐はTest期間の高ボラに依存（Train/Test不一致）。堅牢ではない。**
→ **Tick/LOBデータでの再検証が必須。**

---

## Tier 3: 棄却済み (再検証不要)

| 手法 | 棄却理由 |
|------|---------|
| OHLCV線形α (vol_ratio等) | Train→Test完全崩壊 |
| LightGBM方向予測 | Train 85% → Test 50% |
| FR/OI閾値イベント | 重複排除で全非有意 |
| F&G逆張り | Train/Test符号一致率37% |
| Stablecoin供給 | 全ペア符号不一致 |
| ボラ圧縮→ブレイクアウト | 逆の結果 (圧縮→継続) |
| 取引所間スプレッド裁定 (1h) | 0.19bp (コスト8bpに対して微小) |
| 取引所間スプレッド裁定 (1m) | |diff|>5bp = 0.01-0.43%, コスト後マイナス |
| 月次リバーサル | Train→Test符号反転 |
| FR Settlement Timing | Train/Test符号反転 |
| Cross-Exchange Lead-Lag (1h) | r=0.01-0.03 (無効) |
| Cross-Exchange Lead-Lag (1m) | r=-0.02〜-0.03 (無効) |
| Volume Imbalance→Direction (1h) | r=0.01-0.06 (微弱) |
| Volume Imbalance→Direction (1m) | r=-0.01〜-0.03 (リバーサルだが微弱) |
| FR Carry (Simple) | FR収入(0.5-0.9bp) < コスト(8bp) |
| Basis Carry | Train/Test符号反転 |
| ペアトレード (ETH/BTC等) | コインテグレーション不成立、HL>1000h |
| カレンダー効果 (月末/月初) | 微弱(3-5bp)、Bonferroni後非有意 |
| 条件付きMM (時間帯×Vol, 1h) | 全7パターンでTest全てマイナス |
| 条件付きMM (1m, low-vol/quiet) | 全条件でマイナス (very_low_vol=-74bp/day) |
| Post-Extreme Reversal | Train→Test符号反転 |
| FR Level→7d fwd (Linear) | BTC Train/Test符号反転, WF全負 |
| Cumulative FR (7d sum) | 全通貨p>0.42 |
| Bid-Ask Bounce (1m) | AC=-0.03〜-0.04, gross 0.05-0.15bp/m < 費用 |
| 複合シグナル (1m, reversal+vol+lowvol) | 0.1-0.5bp/5m, コスト(4bp RT)以下 |
| 連続candle reversal (1m) | P(cont)=0.43-0.46, 0.2-0.3bp < コスト |

---

## 推奨アクションプラン

### Phase 1: リスク管理ツール構築 (現データで即可能) ✅完了
1. **動的ポジションサイジングモジュール** → `src/risk.py` 実装済み
   - `rvol_24h` ベースのリスク計算
   - 時間帯・曜日の調整係数
   - レジーム判定 (Low/Mid/High) による倍率

2. **リアルタイムモニタリング**
   - ボラレジーム変化アラート (rvol_24h が 48h MA の 1.5σ超)
   - クロスアセット相関崩壊アラート (7d rolling corr < 0.6)

### Phase 2: Tick/LOBデータ取得 (必須, 次のステップ)
1. **WebSocket BBO + Trade stream**
   - Binance USDT-M Futures: `wss://fstream.binance.com/ws/<symbol>@bookTicker`
   - Bybit: `wss://stream.bybit.com/v5/public/linear`
   - SOL, ETH, BTC の3通貨
   - 最低2週間のTick蓄積

2. **LOBデータで検証すべき項目**
   - 実quoted spread vs Roll推定(3-5bp) の比較
   - Queue position と実フィル確率
   - 逆選択の trade-by-trade 測定
   - BBO imbalance → short-term direction の予測力
   - Trade flow toxicity (VPIN等)

3. **清算データ**
   - Coinglass API (要キー取得)
   - 大口清算イベントの事前検知

### Phase 3: Tick-Level MM再検証 (LOBデータ取得後)
1. **マイクロストラクチャー分析**
   - Bid-Ask Bounce (実BBO)
   - Trade Imbalance (buy/sell volume)
   - LOB圧力指標 (bid/ask depth ratio)
   - 逆選択の正確な測定 (trade sign + price impact)

2. **ASモデル再実装**
   - Tick粒度でのスプレッド・在庫最適化
   - 時間帯・ボラレジーム条件付きパラメータ
   - 実フィル確率に基づくバックテスト

3. **ペーパートレード**
   - 実BBO でのMMシミュレーション
   - フィル確率の正確な推定
   - Binance Testnet での検証

### Phase 4: VIP手数料交渉 (Phase 3と並行)
- MM損益分岐には0bp以下のmaker手数料が必要
- Binance/Bybit の MM プログラム申請
- 必要条件: 月間取引量、稼働率、スプレッド維持

---

## 学びの要約

### 方法論的教訓
1. **シリアル相関の罠**: 重複リターンは N を膨張させ偽陽性を生む。必ず Newey-West HAC または非重複ウィンドウで検定。
2. **Train/Test符号一致の重要性**: p<0.05 でも Train/Test で符号が反転するシグナルは信用できない。
3. **データマイニング・バイアス**: 22手法×複数パラメータ = 数百仮説。Bonferroni補正で p<0.001 が必要。
4. **1h足の限界**: マイクロストラクチャーα は 1h足では検出不可能。
5. **1m足の限界**: OHLCVではqueue position、order flow toxicity、真のBBOが不明。LOBデータが必須。
6. **ボラ予測 ≠ 方向予測**: ボラは予測可能 (r=0.49-0.71) だが方向は不可能。両者は独立。
7. **OHLCV MM シミュレーションの過大評価**: fill = low ≤ bid の仮定は逆選択を過小評価する場合と過大評価する場合がある。実BBOなしでは正確な判定不可。
8. **手数料が支配的**: 仮にα=0のMMでも、手数料が損益を決定する。VIP手数料がMMの最低条件。
