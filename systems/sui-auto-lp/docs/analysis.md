# 分析スクリプト・ユーティリティ

## リバランス ROI 分析

各リバランスの費用対効果を事後検証する。

```bash
npx tsx scripts/analyze-rebalance-roi.ts           # 全期間
npx tsx scripts/analyze-rebalance-roi.ts --last 7   # 直近7日間
npx tsx scripts/analyze-rebalance-roi.ts 2026-02-20 # 特定日
```

**出力内容:**
- リバランスごとのコスト・収益・ROI・レンジ内滞在率
- 赤字パターンの特定（ROI < 1.0）
- 全体サマリー（平均ROI、最悪/最良ケース）

---

## 反実仮想シミュレーション（skip 分析）

threshold リバランスを仮にスキップしていたらどうなったかを分析する。

```bash
npx tsx scripts/simulate-skip.ts           # 全期間
npx tsx scripts/simulate-skip.ts --last 14  # 直近14日間
```

**出力内容:**
- 各 threshold リバランスの分類: skip-safe / skip-risky / skip-costly
- スキップしていた場合の節約額推定
- リバランス削減の推奨

---

## 手数料収益ヒートマップ

曜日×時間帯（2h バケット）の手数料収益を可視化する。

```bash
npx tsx scripts/revenue-heatmap.ts           # 全期間
npx tsx scripts/revenue-heatmap.ts --last 14  # 直近14日間
```

**出力内容:**
- ASCII ヒートマップ（7×12 グリッド、UTC 基準）
- セッション別サマリー（Asia / Europe / Americas）
- 平日 vs 週末の比較
- Dynamic Harvest Interval の推奨

---

## ボラティリティ診断

現在のボラティリティとレンジ幅推奨を表示する。

```bash
npx tsx scripts/check-volatility.ts
```

---

## ポジション管理スクリプト

### ポジションのリサイズ（目標 USD 額に調整）

```bash
npx tsx scripts/resize-position.ts      # $20 に調整（デフォルト）
npx tsx scripts/resize-position.ts 50   # $50 に調整
```

対象: `.env` の `POSITION_IDS` の最初のポジション。超過分のリクイディティを除去し、資金をウォレットに返却する。

### デバッグポジションのクリーンアップ

```bash
npx tsx scripts/cleanup-positions.ts
```

テスト中に作成された余分なポジションからリクイディティを除去する。

### ポジションのクローズ

```bash
npx tsx scripts/close-position.ts [positionId]
```

### 遊休資金の追加投入

```bash
DRY_RUN=true npx tsx scripts/add-funds.ts    # ドライラン
DRY_RUN=false npx tsx scripts/add-funds.ts   # 本番実行
```

- Aggregator vs Direct Pool を自動比較し、安い方でスワップ
- ガスリザーブ 1.0 SUI を自動確保
- スワップ後に addLiquidity を実行

---

## テストスクリプト

### SUI → USDC スワップ + LP ポジション開設

```bash
npx tsx scripts/test-swap-and-open.ts --dry-run  # ドライラン
npx tsx scripts/test-swap-and-open.ts             # 本番実行
```

SUI を USDC にスワップし、USDC/SUI プールに $10+$10 のテストポジションを開設する。

### スワップコスト比較

```bash
npx tsx scripts/compare-swap-cost.ts
```

Aggregator と Direct Pool のスワップコストを比較する。

---

## DeepBook Margin（レバレッジトレード）

Cetus CLMM のレンジ運用と併用して、DeepBook Margin でレバレッジドロングを建てるスクリプト群。

### マージンポジション確認

```bash
npm run deepbook
```

表示内容:
- ポジション状態（資産、負債、レバレッジ、リスク比率、方向）
- オープン注文・条件付き注文（TP/SL）
- プール情報（mid price, オーダーブック深度, maker/taker fee）
- マージンプール金利・供給/借入残高
- 直近トレード（indexer API）

### ロングポジション建て

```bash
npm run deepbook:long -- --usdc 10 --leverage 2 --dry-run   # dry-run
npm run deepbook:long -- --usdc 10 --leverage 2              # 成行注文
npm run deepbook:long -- --usdc 10 --leverage 2 --price 0.85 # 指値（POST_ONLY）
```

| 引数 | 必須 | 説明 |
|---|---|---|
| `--usdc <amount>` | Yes | デポジットする USDC 額 |
| `--leverage <x>` | Yes | レバレッジ倍率（1以上） |
| `--price <price>` | No | 指値価格（省略=成行） |
| `--dry-run` | No | シミュレーションのみ |

**実行フロー**（1 atomic TX）:

1. `depositQuote` — USDC をマージン口座にデポジット
2. `borrowQuote` — レバレッジ分の USDC を借入（leverage > 1x の場合）
3. `placeMarketOrder` or `placeLimitOrder` — SUI を購入

**指値注文の動作:**
- `POST_ONLY` モード — 板に resting order として載る場合のみ発注
- 即時約定する価格の場合、注文はキャンセルされる

**環境変数:**

| 変数名 | 必須 | 説明 |
|---|---|---|
| `SUI_PRIVATE_KEY` | **必須** | ウォレット秘密鍵 |
| `SUI_NETWORK` | - | `mainnet` or `testnet`（default: mainnet） |
| `DEEPBOOK_MARGIN_MANAGER_IDS` | - | MarginManager ID（カンマ区切り、省略時は自動検出） |

**注意: SDK パッチ**

`@mysten/deepbook-v3` v0.28.3 の mainnet `MARGIN_PACKAGE_ID` が古いため、スクリプト内でランタイムパッチを適用している。SDK アップグレード時にパッチが不要になる可能性あり。
