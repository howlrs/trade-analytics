# DEX MM戦場選定: 調査結果 (2026-03-03)

## 調査背景

CEX MM分析の結論:
- MM損益分岐にはゼロ以下のmaker手数料が必要
- CEX VIP5+は実績なしでは到達困難
- DEXのmaker rebateが解決策になり得る

## DEX比較マトリックス

### Perpetual DEX (CLOB型)

| DEX | チェーン | Maker Fee | Taker Fee | 日次出来高 | Latency | API/SDK | 監査 |
|-----|---------|-----------|-----------|-----------|---------|---------|------|
| **Drift** | Solana | **-0.25bp** (rebate) | 3bp | $500M-1B | ~400ms | Python (DriftPy) | Trail of Bits, Neodyme |
| **Hyperliquid** | 独自L1 | 1.5bp (VIP0) → 0bp (VIP4) | 4.5bp→2.4bp | $3-7.5B | ~200ms | Python SDK | **未監査** (リスク) |
| **dYdX v4** | Cosmos | 1bp (T1) → -1.1bp (T7,$200M) | 5bp→2.5bp | $200-800M | ~1-2s | Python (async) | Informal Systems |

### Perpetual DEX (Oracle/LP型) — MM不適

| DEX | モデル | Maker Fee | MM適合性 | 理由 |
|-----|-------|-----------|---------|------|
| Jupiter Perps | Oracle LP | N/A | **低** | CLOBなし、limit order不可 |
| GMX v2 | Oracle LP | 5bp (正) | **低** | CLOBなし、手数料高 |

### Spot DEX (参考)

| DEX | モデル | 備考 |
|-----|-------|------|
| Raydium | CLMM + CLOB | Perps (via Orderly) maker 2bp、rebateなし |
| Orca | CLMM (Whirlpool) | LP型のみ、Adaptive Fees |

## ライブオーダーブック比較 (2026-03-03 スナップショット)

### Quoted Spread

| 通貨 | Binance | Hyperliquid | Drift | 倍率 (Drift/Binance) |
|------|---------|-------------|-------|-------------------|
| SOL | 1.16bp | 0.12bp | **10.2bp** | **8.8x** |
| ETH | 0.05bp | 0.50bp | **15.4bp** | **308x** |
| BTC | 0.01bp | 0.15bp | **10.9bp** | **1090x** |

### Depth (5bp以内)

| 通貨 | Binance (bid) | Hyperliquid (bid) | Drift (bid) |
|------|-------------|-----------------|-----------|
| SOL | 4,179 SOL | 4,041 SOL | **0** (vAMM ~5.8bp外) |
| ETH | 43 ETH | 2,475 ETH | **0** (vAMM ~5.8bp外) |
| BTC | 3.9 BTC | 36 BTC | **0** (vAMM ~5.8bp外) |

### Driftオーダーブックの構造

- **ほぼ100% vAMM流動性** — 実limit order (dlob) は微小 (2-100 SOL)
- vAMMスプレッド: ~10bp (安定、std=0.01bp)
- **5bp以内にlimit orderを置けば、vAMMより先にフィルされる**
- → MM機会: vAMMスプレッドの内側にquoteして taker flow を独占

## MM経済性モデル

### Drift SOL-PERP (最有望)

```
Revenue per round trip:
  Spread capture (5bp half): +10.0bp
  Maker rebate (-0.25bp × 2): +0.5bp
  Total gross: +10.5bp

Costs per round trip:
  Adverse selection: -3 to -8bp (不確実)
  Stale quote risk (400ms latency): variable
  Inventory holding cost: position × vol × time

Net estimate:
  Optimistic: +10.5bp - 3bp = +7.5bp/RT
  Central:    +10.5bp - 6bp = +4.5bp/RT
  Pessimistic: +10.5bp - 10bp = +0.5bp/RT
```

### シミュレーション結果 (CEXデータベースのプロキシ)

| Capture Rate | PnL/day (mean) | Std | Trades/day |
|-------------|----------------|-----|-----------|
| 5% | -32bp | ±464bp | 14 |
| 20% | +24bp | ±181bp | 55 |
| 50% | +102bp | ±245bp | 140 |
| 100% | +222bp | ±103bp | 275 |

→ **高いvariance** — capture rateが収益を決定的に左右する。

## 最終推奨

### Tier 1: 最優先 — **Drift Protocol SOL-PERP**

**理由**:
1. **即時maker rebate** (-0.25bp) — volume要件なし
2. **広いvAMMスプレッド** (10bp) — limit orderの余地が大
3. **低競争** — dlobにほぼ注文なし → first mover advantage
4. **DriftPy SDK** — Python統合可能
5. **監査済み** (Trail of Bits, Neodyme) — カウンターパーティーリスク限定的
6. **JIT Auction** — market orderのmaker fillが可能
7. **MM Rewards Program** — 2M DRIFT/月の追加インセンティブ

**リスク**:
- 逆選択 (CEX-DEX arb): CEX price moveがDrift上のstale quoteを狙う
- Solana障害リスク
- スマートコントラクトリスク (ただし監査済み)
- Capture rate不確実性

**次のステップ**:
1. DriftPy SDKでのpaper trading環境構築
2. WebSocket接続でリアルタイムオーダーブック取得
3. 1-2週間のDrift trade-by-tradeデータ蓄積
4. 逆選択の実測定

### Tier 2: 将来的 — **Hyperliquid**

**理由**:
- 出来高最大 (perp DEX 1位)
- Maker rebate program (-0.1 to -0.3bp, maker volume比率条件)
- Tokyo server → 低latency
- Post-Only注文がキャンセルより優先 (stale quote保護)
- 302+ペア

**課題**:
- VIP0 maker fee = 1.5bp (rebateまで到達困難)
- **未監査** — カウンターパーティーリスク大
- 競争激しい (0.12bp quoted spread)

### Tier 3: 条件付き — **dYdX v4**

**理由**:
- Tier 4 ($25M/30d) で0bp maker fee
- Tier 6-7 で rebate (-0.7 to -1.1bp)
- Cosmos appchain、60+バリデーター

**課題**:
- Latency最長 (~1-2s)
- Volume最小 ($200-800M/day)
- $25M/monthの出来高が最低条件

### 不適: Jupiter Perps, GMX v2, Raydium Perps

- CLOB非対応 (Jupiter, GMX) またはmaker rebateなし (Raydium)

## カウンターパーティーリスク評価

| DEX | チェーン | 監査 | TVL | ブリッジリスク | MEVリスク | 総合リスク |
|-----|---------|------|-----|------------|---------|----------|
| Drift | Solana | ✅ Trail of Bits, Neodyme | 高 | 低 (native SOL/USDC) | 中 (Solana MEV) | **中** |
| Hyperliquid | 独自L1 | ❌ 未監査 | $1.5-2B | 中 (Arbitrumブリッジ) | 低 (構造的) | **高** |
| dYdX v4 | Cosmos | ✅ Informal Systems | $200-400M | 中 (CCTP+IBC) | 中 (block proposer) | **中** |

### リスク緩和策

1. **ポジション上限**: 最大$10K-50K (初期フェーズ)
2. **常時ヘッジ**: Drift MM在庫をBinance/Bybitでデルタヘッジ
3. **回転資金**: 全資産をDEXに置かない (必要分のみデポジット)
4. **自動停止**: 異常スプレッド検出で即座に全注文キャンセル
5. **分散**: Drift単体依存を避け、条件が整えばHyperliquid/dYdXにも展開

## 技術スタック (Drift MM構築)

```
[Data Pipeline]
  Binance WS → CEX price feed (reference price)
  Drift WS → DEX order book, fills, position

[Strategy Engine]
  src/risk.py → rvol_15m, regime detection (既存)
  src/drift_mm.py → spread calculation, inventory management (NEW)

[Execution]
  DriftPy SDK → order placement, cancellation
  Post-Only orders → maker rebate確保

[Hedging]
  ccxt → Binance/Bybit delta hedge (taker)
```

## 参考リンク

- [Drift Protocol Docs](https://docs.drift.trade/)
- [DriftPy SDK](https://github.com/drift-labs/driftpy)
- [Drift DLOB API](https://dlob.drift.trade/)
- [Hyperliquid Docs](https://hyperliquid.gitbook.io/hyperliquid-docs/)
- [Hyperliquid Python SDK](https://github.com/hyperliquid-dex/hyperliquid-python-sdk)
- [dYdX v4 Docs](https://docs.dydx.xyz/)
- [Hyperliquid Fee Schedule](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees)
