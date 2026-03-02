# Sui Auto LP - Cetus CLMM Liquidity Optimization

## Project Overview

Sui チェーン上の Cetus Protocol CLMM プールに対して、流動性提供（LP）を自動最適化するシステム。
価格範囲の自動調整、手数料のコンパウンド、ポジションのリバランスを自律的に実行する。

## Tech Stack

- **Runtime**: Node.js (v24+) / TypeScript
- **Blockchain**: Sui (CLI v1.65.2+ via suiup)
- **DEX SDK**: `@cetusprotocol/cetus-sui-clmm-sdk` (CLMM concentrated liquidity)
- **Sui SDK**: `@mysten/sui` (Sui TypeScript SDK)
- **Scheduler**: node-cron or custom interval loop

## Architecture

```
src/
├── config/          # 設定ファイル（プール設定、戦略パラメータ）
├── core/
│   ├── pool.ts      # プール情報取得・監視
│   ├── position.ts  # ポジション管理（open/close/adjust）
│   ├── rebalance.ts # リバランスロジック
│   ├── compound.ts  # 手数料・リワードのclaim（harvest）
│   └── price.ts     # 価格フィード・tick計算
├── strategy/
│   ├── range.ts     # 価格範囲戦略（narrow/wide/dynamic）
│   ├── trigger.ts   # リバランストリガー条件
│   └── volatility.ts # ボラティリティ計測・動的tick幅決定
├── utils/
│   ├── sui.ts       # Sui クライアント・署名ヘルパー
│   ├── wallet.ts    # ウォレット管理
│   └── logger.ts    # ログユーティリティ
├── scheduler.ts     # 定期実行スケジューラ
└── index.ts         # エントリポイント
```

## Key Concepts

### Cetus CLMM SDK Usage

```typescript
import { initCetusSDK } from '@cetusprotocol/cetus-sui-clmm-sdk'
const sdk = initCetusSDK({ network: 'mainnet' })
```

主要操作:
- `sdk.Pool.getPool(poolId)` - プール情報取得
- `sdk.Position.getPositionList(owner)` - ポジション一覧
- `sdk.Position.createAddLiquidityTransactionPayload()` - 流動性追加
- `sdk.Position.createRemoveLiquidityTransactionPayload()` - 流動性削除
- `sdk.Rewarder.collectRewarderTransactionPayload()` - リワード回収

### リバランス戦略

1. **Range Out Detection**: 現在価格がLP範囲外に出たら検知
2. **Threshold Rebalance**: 範囲端からX%以内に近づいたらリバランス
3. **Time-based**: 定期的に最適範囲を再計算
4. **Harvest**: 手数料・リワードをウォレットにclaim

## Sui CLI

- インストール: `suiup` 経由（`suiup install sui`）
- クライアント設定: `sui client` でネットワーク・ウォレット管理
- アクティブアドレス確認: `sui client active-address`
- 残高確認: `sui client balance`

## Development Guidelines

- 秘密鍵・mnemonicは `.env` に格納し、絶対にコミットしない
- すべてのトランザクションはドライラン（simulation）を先に実行
- mainnet操作前にtestnetで検証する
- リバランス時のスリッページ上限を設定で管理
- ログは構造化JSONで出力し、全トランザクションを記録
- ガス代を含めた収益計算を行い、赤字リバランスを回避

## Environment Variables

```
SUI_NETWORK=mainnet|testnet
SUI_PRIVATE_KEY=<bech32 or base64 encoded private key>
POOL_IDS=<comma separated pool IDs to manage>
POSITION_IDS=<comma separated position IDs (optional, defaults to all)>
REBALANCE_THRESHOLD=0.03  # 推奨: 0.10
CHECK_INTERVAL=30
HARVEST_INTERVAL=7200
SLIPPAGE_TOLERANCE=0.01
MIN_GAS_PROFIT_RATIO=2
HARVEST_THRESHOLD_USD=0.50
LOG_LEVEL=info
DRY_RUN=true
PAUSED=false
MAX_SWAP_COST_PCT=0.01
SWAP_FREE_REBALANCE=true
SWAP_FREE_MAX_RATIO_SWAP=0.20
MAX_IDLE_SWAP_RATIO=0.45

# DeepBook Margin (optional, for scripts/check-deepbook.ts & open-deepbook-long.ts)
# DEEPBOOK_MARGIN_MANAGER_IDS=0x...  # comma separated, auto-discovered if omitted
```

### ボラティリティ戦略パラメータ（プール個別設定）

プール設定の `strategy: 'dynamic'` 使用時に、ボラティリティに基づく動的レンジ幅を計算する。

| パラメータ | デフォルト | 説明 |
|---|---|---|
| `volLookbackHours` | `2` | ボラティリティ計算のルックバック時間 |
| `volTickWidthMin` | `480` | 最小 tick 幅 |
| `volTickWidthMax` | `1200` | 最大 tick 幅 |

σ（1時間あたりのtick変動標準偏差）に応じて自動的にレンジ幅を決定:
- σ < 40 → 480 ticks, σ 40-80 → 720 ticks, σ 80-120 → 960 ticks, σ ≥ 120 → 1200 ticks

### リバランスガードレール

過剰リバランス防止のための安全機構:

| パラメータ | デフォルト | 説明 |
|---|---|---|
| `waitAfterRangeoutSec` | `1800` (30分) | レンジアウト検出後の待機時間。20-30%は自己修復する |
| `maxRebalancesPerDay` | `3` | 1日あたりの最大リバランス回数（ソフトリミット: range-outは例外で通過。カウントは state.json に永続化） |
| `minTimeInRangeSec` | `7200` (2時間) | 新ポジション開設後の最低レンジ内時間（threshold triggerのみ） |
| Profitability gate | `48h` | breakeven が48時間を超えるリバランスをブロック |
| Cooldown (SUI下落) | `3600s` (60分) | SUI下落時のクールダウン（SUI heavy → 回復待ち） |
| Cooldown (SUI上昇) | `1800s` (30分) | SUI上昇時のクールダウン（USDC heavy → 早期再参入） |

### 緊急停止・一時停止

`PAUSED=true` を `.env` に設定すると、リバランス・コンパウンドをスキップする。
サービス再起動不要（次のチェックサイクル、最大30秒以内に反映）。
詳細は `docs/operations.md` の「一時停止・再開」セクション参照。

## Important: CLMM 価格方向

価格関数 `sqrtPriceX64ToPrice` は **coinB per coinA** を返す（USDC per SUI ではない）。
USD 換算時は `coinBPriceInCoinA()` (= 1/getCurrentPrice) を使うこと。
詳細・経緯・チェックリストは **[docs/price-direction.md](docs/price-direction.md)** を必ず参照。

**取引所との対応**: 取引所の SUI/USDC (例: 0.89) と pool の currentPrice (例: 1.124) は逆数の関係。
SUI 下落時は pool tick が上昇し、ポジションは SUI heavy になる（LP が SUI を蓄積）。
SUI 上昇時は pool tick が下降し、ポジションは USDC heavy になる（LP が USDC を蓄積）。

## 運用レビュー

定期的な運用状況の確認・評価手順は **[docs/ops-review.md](docs/ops-review.md)** を参照。
ログ取得コマンド、評価テンプレート、チェックリストをまとめている。

### ログ評価の手順

ユーザーから「ログ評価」「運用レビュー」「ログから評価」等を依頼されたら、
**必ず `docs/ops-review.md` のテンプレートに沿って出力する**こと。

1. `deploy/logs.sh status` でサービス状態確認
2. `deploy/logs.sh --since "12 hours ago" -g <keyword>` で重要イベント抽出
   - 主要キーワード: `Rebalance completed`, `Cooldown`, `Harvest`, `error`, `warn`, `idle`, `open`, `Volatility engine`
3. `docs/ops-review.md` の「評価テンプレート」セクションに沿ってレポート作成
4. 「評価の観点」セクションのチェックリストで正常性・収益性・リスクを確認

### ログ grep の注意点

`deploy/logs.sh -g` は内部で `grep -E` を使用する。
- 単一キーワード: `-g "Harvest"` — OK
- OR条件（パイプ区切り）: `-g 'Harvest|error|warn'` — **シングルクォートで囲む**
- 複雑すぎるパターンは失敗しやすい。キーワードごとに分割して個別実行を推奨

## Documentation

```
docs/
├── commands.md          # コマンド早見表（環境変数・起動方法）
├── deploy.md            # デプロイ運用ガイド（セットアップ・状態パターン・ロールバック）
├── rebalance.md         # リバランス挙動・ガードレール・エッジケース
├── harvest.md           # ハーベスト（Claim）挙動
├── operations.md        # 日常運用・監視・一時停止・緊急対応
├── analysis.md          # 分析スクリプト・DeepBook Margin
├── ops-review.md        # 運用レビュー評価テンプレート
├── flow.md              # ロジックフロー（Mermaid 図）
├── price-direction.md   # CLMM 価格方向ガイド
├── archive/             # 完了済み・一時的ドキュメント
└── research/            # 調査・分析ドキュメント
```

## Commands

```bash
# 開発
npm run dev          # 開発モード起動
npm run build        # ビルド
npm run start        # 本番起動
npm run type-check   # 型チェック

# デプロイ（詳細は docs/deploy.md 参照）
bash deploy/setup.sh   # GCP VM 初期セットアップ
bash deploy/deploy.sh  # コード更新デプロイ

# ログ取得（詳細は docs/operations.md 参照）
GCE_PASSPHRASE=xxx bash deploy/logs.sh                          # 直近50行
GCE_PASSPHRASE=xxx bash deploy/logs.sh --since "1 hour ago"     # 時間指定
GCE_PASSPHRASE=xxx bash deploy/logs.sh -f                       # リアルタイム追従
GCE_PASSPHRASE=xxx bash deploy/logs.sh status                   # サービス状態

# 一時停止・再開（詳細は docs/operations.md 参照）

# 診断
npm run health                        # ヘルスチェック
npm run report                        # 日報

# 分析（詳細は docs/analysis.md 参照）
npx tsx scripts/analyze-rebalance-roi.ts   # リバランスROI分析
npx tsx scripts/simulate-skip.ts           # 反実仮想シミュレーション
npx tsx scripts/revenue-heatmap.ts         # 手数料収益ヒートマップ

# DeepBook Margin（詳細は docs/analysis.md 参照）
npm run deepbook                      # マージンポジション確認
npm run deepbook:long -- --usdc 10 --leverage 2 --dry-run   # ロング建て

# Sui CLI
sui client balance   # 残高確認
sui client objects    # オブジェクト一覧
```
