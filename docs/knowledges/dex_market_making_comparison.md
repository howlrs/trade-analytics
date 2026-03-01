# DEX Market Making 適性比較 (2026年3月時点)

## 比較サマリーテーブル

| 項目 | Jupiter Perps | Drift Protocol | GMX v2 | Raydium (Perps) | Raydium (AMM/CLMM) | Orca |
|------|--------------|----------------|--------|-----------------|---------------------|------|
| **チェーン** | Solana | Solana | Arbitrum | Solana | Solana | Solana |
| **モデル** | LP-pool (Oracle) | Hybrid CLOB + AMM | LP-pool (Oracle) | CLOB (Orderly) | AMM / CLMM | CLMM (Whirlpool) |
| **Maker Fee** | N/A (オーダーブック無し) | **-0.0025%** (リベート) | 0.05% | 0.02% (beta時0%) | N/A (LP提供型) | N/A (LP提供型) |
| **Taker Fee** | 0.06% (open/close) | 0.03% (Tier1) | 0.07% | 0.07% | 0.01%~0.25% (pool別) | 0.01%~0.3% (pool別) |
| **BTC/ETH/SOL Perps** | BTC, ETH, SOL | BTC, ETH, SOL + 多数 | BTC, ETH, SOL + 多数 | 110+ pairs | Spot only | Spot only |
| **日次出来高 (推定)** | ~$1-2B | ~$500M-1B | ~$200M | ~$100M (perps) | ~$500M+ (spot) | ~$300M |
| **API/SDK** | REST API (WIP) | TS SDK, Python SDK, Gateway | TS SDK, Python SDK | Orderly API | TS SDK, REST API | TS SDK, REST API |
| **MM適性** | **低** | **高** | **低** | **中** | **中** (LP運用) | **中** (LP運用) |
| **監査** | Offside Labs | Trail of Bits, Neodyme | ABDK, Dedaub, Guardian | OtterSec, HashEx | OtterSec, HashEx | Neodyme, Kudelski |

---

## 1. Jupiter Perps (Solana)

### モデル
- **LP-pool型 (非オーダーブック)**。トレーダーはJLP (Jupiter Liquidity Pool) に対してトレードする。
- オラクル価格 (Pyth, Chainlink) ベースで約定。
- 最大250倍レバレッジ。BTC, ETH, SOL のみ。

### 手数料
- Open/Close: **0.06%** (フラット)
- Borrowing fee: ~0.01%/時間 (利用率に応じて変動)
- **Maker fee/リベートは存在しない** (オーダーブックが無いため)

### MM適性: 低
- オーダーブックが無いため、指値注文によるMM戦略は不可能。
- LP提供 (JLP) は可能だが、プール構成は固定 (SOL/ETH/WBTC/USDC/USDT) で裁量的なMM運用は困難。
- JLP保有者は取引手数料の大部分を受け取る (年利は変動)。

### API/SDK
- [Jupiter Perps API](https://dev.jup.ag/docs/perps) - REST API提供あり (開発中)
- C# SDK (サードパーティ): Solnet.JupiterPerps
- Spot swap用のAPI/SDKは成熟している

### MEV/フロントランニング
- Solana固有のMEV リスクあり (後述の共通セクション参照)
- Jupiter独自のMEV保護: トレードの分割最適化、代替パス探索
- Perpsはオラクル価格ベースのため、AMM型のサンドイッチ攻撃リスクは低い

### 監査
- Offside Labs による監査 (2024年2月レポート公開)

---

## 2. Drift Protocol (Solana) ★ MM最適候補

### モデル
- **Hybrid CLOB + vAMM + JIT (Just-In-Time) Auction**
- オンチェーンオーダーブック (ただし厳密なprice-time priorityではなく、Solanaランタイム最適化)
- Perps + Spot + Borrow/Lend の統合マージン
- 最大100倍レバレッジ

### 手数料 (2025年8月改定)
- **Maker: -0.0025% (リベート)** ← MM戦略に有利
  - DRIFT ステーキングで最大40%追加リベート → **最大 -0.0035%**
- Taker: 0.03% (Tier 1) ~ 0.0275% (Tier 3以上)
  - DRIFTステーキングで最大20%割引
- 30日間出来高ベースのティア制

### MM適性: 高
- **CLOBでの指値注文が可能** + **Makerリベートあり**
- JIT Auction: マーケットオーダーに対してMaker側として約定するメカニズム。専用の流動性提供が可能。
- Market Maker Rewards Program (2025年9月開始): 月200万DRIFTをMM報酬として分配。
- Drift v3 (2025年12月): 10倍高速化、85%のマーケットオーダーが1スロット (400ms) 内に完了。

### 日次出来高
- ピーク時: ~$1B/日 (2025年7月に$1.089B ATH)
- 月間: ~$16B (直近データ)
- BTC-PERP, ETH-PERP, SOL-PERP が主要マーケット

### API/SDK
- **TypeScript SDK**: [@drift-labs/sdk](https://github.com/drift-labs/protocol-v2) - 最も成熟
- **Python SDK**: [DriftPy](https://github.com/drift-labs/driftpy) - TS SDKのミラーだが一部機能不足
- **Self-hosted HTTP Gateway**: [drift-labs/gateway](https://github.com/drift-labs/gateway) - REST API風にDriftを操作
- オーダー発注、ポジション管理、オンチェーンサブスクリプション全対応

### MEV/フロントランニング
- Jito統合によるMEV保護
- JIT Auctionメカニズム自体がフロントランニングを軽減する設計
- Solana共通のwide sandwich riskは残存 (後述)

### 監査
- Trail of Bits (2022年11月-12月)
- Neodyme
- ローンチ以来重大なエクスプロイト無し
- バグバウンティプログラムあり

---

## 3. GMX v2 (Arbitrum)

### モデル
- **LP-pool型 (Oracle + Dynamic Pricing)**
- GM Pools (個別マーケットプール) に対してトレーダーがトレード
- オラクル (Chainlink) ベースの約定
- 最大100倍レバレッジ

### 手数料
- **Maker: 0.05%** ← リベート無し、MM不利
- **Taker: 0.07%**
- Fee Rebate Incentive Program (2025年12月~2026年3月): $600K USDC分のGMXバイバックで手数料リベート

### MM適性: 低
- オーダーブックが無いため、指値によるMM戦略は不可能。
- LP提供 (GM Pool) でパッシブにMM可能だが、裁量性は限定的。
- Maker feeが正の値 (0.05%) であり、高頻度トレードにはコスト高。

### 日次出来高
- ~$200M/日 (2026年3月時点)
- ETH/USD: ~$65M/日, BTC/USD: 主要
- Open Interest: ~$355M

### API/SDK
- **TypeScript SDK**: [@gmx-io/sdk](https://www.npmjs.com/package/@gmx-io/sdk)
- **Python SDK**: [gmx_python_sdk](https://github.com/snipermonke01/gmx_python_sdk) (サードパーティ)
- Bitquery GraphQL API でオンチェーンデータ取得可
- [Integration API](https://github.com/gmx-io/gmx-integration-api)

### MEV/フロントランニング
- Arbitrum (L2) はEthereumメインネットよりMEVリスクが低い
- シーケンサーが単一のためフロントランニングは構造的に限定的
- ただし将来的な分散化シーケンサーでリスク変化の可能性

### 監査
- ABDK, Dedaub, Guardian (計351件の発見、80件がHigh/Critical)
- 10ヶ月の包括的セキュリティレビュー
- 15,000行以上のテストコード
- **最も徹底した監査実績**

---

## 4. Vertex Protocol (Arbitrum) ⚠️ 運用停止

### 現状 (2026年3月)
- **2025年8月14日に全トレーディング停止**
- Ink Foundation (Kraken系L2) に買収・移管
- VRTX トークンはサンセット → INKトークンへの移行 (供給量の1%をVRTX保有者にエアドロップ)
- 9つのEVMチェーンでの展開を全て停止

### 過去の仕様 (参考)
- Hybrid CLOB + AMM
- **Maker fee: 0% (無料)** + ステーキング量に応じた0.15~0.75bpsリベート
- 非常にMM向きだったが、現在は利用不可

### 後継
- Ink L2上で新プロトコルとして再構築予定
- 時期未定。MM候補としては現時点では除外。

---

## 5. Raydium (Solana)

### 5a. Raydium Perps (CLOB - Orderly Network)

#### モデル
- **CLOB (Central Limit Order Book)** - Orderly Networkのインフラ上で稼働
- ガスレス取引、最大100倍レバレッジ
- 110+ trading pairs

#### 手数料
- **Maker: 0.02%** (beta期間中は0%)
- **Taker: 0.07%**
- Orderly Networkが手数料を徴収

#### MM適性: 中
- CLOBのため指値注文可能
- ただしMakerリベートではなく正のfee (0.02%)
- Orderly Networkの共有オーダーブックを使用 → 流動性は他のOrderly統合DEXと共有
- beta終了後のfee確定に注意

#### 日次出来高
- ~$100M/日 (2025年初データ、成長中)

### 5b. Raydium AMM / CLMM (Spot)

#### モデル
- **AMM v4**: 従来型 (0.25% swap fee)
- **CPMM**: 4段階fee tier (0.25%, 1%, 2%, 4%)
- **CLMM**: 8段階fee tier (0.01%~2%)
- OpenBook CLOBとの流動性共有 (フィボナッチ数列ベースで指値注文を配置)

#### 手数料分配
- LP: 84%, RAY buyback: 12%, Treasury: 4%

#### MM適性: 中 (LP運用として)
- CLMM で集中流動性提供 → 効率的なLP戦略が可能
- ただしオーダーブック型のMM (bid/ask spread管理) は直接的には不可
- Impermanent Lossリスクあり

### API/SDK
- [Trade API](https://docs.raydium.io/raydium/traders/trade-api) (swap用)
- TypeScript SDK + gRPC (リアルタイムモニタリング)
- Perps側はOrderly NetworkのAPIを使用

### MEV/フロントランニング
- AMM/CLMMはサンドイッチ攻撃の対象になりやすい
- Perps (Orderly) はオフチェーンマッチングのためMEVリスク低減

### 監査
- OtterSec (2022-2023)
- HashEx (2024年8月: 0件の問題検出)
- Kudelski Security (2021年初期)
- Immunefi バグバウンティ (最大$500K+)
- 2022年11月にハック被害歴あり (修正済み)

---

## 6. Orca (Solana)

### モデル
- **CLMM (Concentrated Liquidity AMM)** - Whirlpool
- 集中流動性で資本効率を最大化
- Adaptive Fees: ボラティリティに応じて手数料が動的に調整 (2025年6月導入)

### 手数料
- Stable Whirlpool: 0.01%
- Standard Whirlpool: 0.2%
- Stable Pool: 0.07%
- Standard Pool: 0.3%
- Adaptive Fee Pools: base fee + ボラティリティプレミアム

### 対応ペア
- SOL/USDC が主要 (~$150M/日)
- BTC/SOL, ETH/SOL 等も存在するが流動性は限定的
- 859 assets, 20,478+ markets

### 日次出来高
- ~$290M/日 (24h volume)
- 月間: ~$6.7B (直近30日)
- SOL/USDCが出来高の約50%を占める

### MM適性: 中 (LP運用として)
- 集中流動性で狭いレンジにLP提供 → 実質的にMM的な動作
- Adaptive Feesにより高ボラ時にLP収益が向上
- ただし直接的なbid/ask管理は不可
- Impermanent Lossリスクあり (集中するほどリスク増)

### API/SDK
- **TypeScript SDK**: [@orca-so/whirlpool-sdk](https://github.com/orca-so/whirlpool-sdk)
- [Whirlpools SDK](https://dev.orca.so/) - swap, LP管理, pool分析
- REST API: プール履歴データ、fee分析エンドポイント
- Python: [orca-python-whirlpool](https://www.johal.in/orca-python-whirlpool-clmm-solana-concentrated-liquidity-2026/) (コミュニティ)

### MEV/フロントランニング
- AMM型はサンドイッチ攻撃の主要ターゲット
- 集中流動性ポジションは攻撃者にとって予測しやすい
- Adaptive FeesがLP側の損失を部分的に緩和

### 監査
- Neodyme (1件のsevere脆弱性を検出、修正済み)
- Kudelski Security
- デュアル監査体制
- Immunefi バグバウンティプログラム
- Anchor framework使用によるアカウント混同脆弱性の防止

---

## Solana共通: MEV/サンドイッチ攻撃リスク

### 現状 (2025-2026)
- 2025年のSolana MEV収益: **$720.1M** (優先手数料を初めて上回る)
- Wide sandwich (複数スロットにまたがる攻撃) が全サンドイッチの**93%**を占有
- 対策により利益性は60-70%低下、ユーザー苦情も60%減少

### 対策手段
- **Jito**: MEV保護バンドルを提供するが、wide sandwichは防げない
- **優先手数料**: 実質的な「保護料」として機能
- AMM型 (Orca, Raydium spot) はサンドイッチ攻撃の主要対象
- CLOB型 (Drift, Raydium Perps) はオフチェーンマッチング要素があり相対的に安全
- Perps (オラクル価格型: Jupiter, GMX) はサンドイッチリスクが構造的に低い

---

## MM戦略別の推奨プロトコル

### 1. オーダーブック型MM (bid/ask spread管理)
| 優先度 | プロトコル | 理由 |
|--------|-----------|------|
| **1st** | **Drift Protocol** | Maker リベート (-0.0025%), CLOB, 充実したSDK, MM報酬プログラム |
| 2nd | Raydium Perps | CLOB (Orderly), 多数のペア。ただしmaker fee正, Orderly依存 |

### 2. LP型MM (集中流動性提供)
| 優先度 | プロトコル | 理由 |
|--------|-----------|------|
| **1st** | **Orca Whirlpool** | 成熟したCLMM, Adaptive Fees, 高出来高, 良好なSDK |
| 2nd | Raydium CLMM | 多様なfee tier, OpenBook連携 |

### 3. パッシブLP
| 優先度 | プロトコル | 理由 |
|--------|-----------|------|
| 1st | Jupiter JLP | BTC/ETH/SOL exposure + 手数料収益, 管理不要 |
| 2nd | GMX GM Pools | 個別マーケット選択可能, Arbitrum (低MEV) |

---

## 結論

**Drift Protocol がMM戦略に最も適している。** 理由:

1. **Makerリベート**: 唯一のリベート提供プロトコル (-0.0025% + staking bonus)
2. **CLOB**: 指値注文による精密なspread管理が可能
3. **SDK充実度**: TypeScript + Python + HTTP Gateway
4. **MM報酬**: 月200万DRIFTの追加インセンティブ
5. **実行速度**: v3で85%のオーダーが400ms以内に完了
6. **監査**: Trail of Bits + Neodyme, エクスプロイト歴なし
7. **出来高**: $500M-1B/日で十分な流動性

次点としてOrcaの集中流動性LP戦略も有望だが、オーダーブック型MMとは性質が異なる。

---

## 参考リンク

- [Jupiter Perps API](https://dev.jup.ag/docs/perps)
- [Jupiter Fee Structure](https://support.jup.ag/hc/en-us/articles/18735045234588-Fees)
- [Drift Trading Fees](https://docs.drift.trade/trading/trading-fees)
- [Drift Maker Rebates](https://docs.drift.trade/market-makers/maker-rebate-fees)
- [Drift SDK](https://docs.drift.trade/sdk-documentation)
- [DriftPy (Python)](https://github.com/drift-labs/driftpy)
- [Drift Gateway](https://github.com/drift-labs/gateway)
- [GMX v2 SDK](https://docs.gmx.io/docs/api/sdk-v2/)
- [GMX Fee Rebate Program](https://gov.gmx.io/t/proposal-for-fees-rebate-incentive-program-december-to-march-2026/4930)
- [GMX Audits (Guardian)](https://guardianaudits.com/casestudies/gmx-case-study)
- [Vertex Shutdown / Ink Migration](https://www.theblock.co/post/361570/vertex-to-sunset-vrtx-token-move-perp-dex-onto-kraken-backed-ink-layer-2)
- [Raydium Perps Fees](https://docs.raydium.io/raydium/traders/raydium-perps/trading-fees)
- [Raydium Security](https://docs.raydium.io/raydium/protocol/security)
- [Orca Whirlpool SDK](https://dev.orca.so/)
- [Orca Fees](https://dev.orca.so/Architecture%20Overview/Whirlpool%20Fees/)
- [Orca Audit (Neodyme)](https://www.neodyme.io/reports/orca.pdf)
- [Solana MEV Report (Helius)](https://www.helius.dev/blog/solana-mev-report)
