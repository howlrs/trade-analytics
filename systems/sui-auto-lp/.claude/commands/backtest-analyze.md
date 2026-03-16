---
description: Run backtest analysis pipeline (ROI, simulation, heatmap)
allowed-tools: Bash, Read, Grep, Glob
---

リバランス戦略の事後分析パイプラインを実行してください。

$ARGUMENTS に特定の分析を指定できます（例: `roi`, `simulate`, `heatmap`）。
指定がなければ全パイプラインを順番に実行します。

## パイプライン

### Step 1: リバランスROI分析
```bash
npx tsx scripts/analyze-rebalance-roi.ts
```
- 各リバランスの費用対効果を算出
- swap コスト vs 獲得手数料を比較

### Step 2: 反実仮想シミュレーション
```bash
npx tsx scripts/simulate-skip.ts
```
- 「リバランスしなかった場合」の推定損益を算出
- リバランス判断の妥当性を検証

### Step 3: 手数料収益ヒートマップ
```bash
npx tsx scripts/revenue-heatmap.ts
```
- 時間帯別・価格帯別の手数料収益を可視化

### Step 4: 総合レポート作成

上記の分析結果を統合し、以下の形式でレポートを出力:

```
## バックテスト分析レポート

### 1. ROI サマリー
- 平均リバランスROI
- 損益分岐時間の中央値
- 赤字リバランスの割合

### 2. 反実仮想との比較
- リバランスあり vs なし の累積収益差
- 判断精度（正しかったリバランスの割合）

### 3. 収益パターン
- 高収益時間帯
- 最適レンジ幅の示唆

### 4. 改善提案
- パラメータ調整の具体的提案（根拠付き）
```
