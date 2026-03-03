---
description: External AI code review using Gemini CLI
allowed-tools: Bash, Read, Grep, Glob
---

Gemini CLI を用いた外部AIコードレビューを実行してください。

$ARGUMENTS にレビュー対象を指定できます:
- ファイルパス（例: `src/core/rebalance.ts`）
- `diff` — 直近の変更差分をレビュー
- `diff:base..HEAD` — 指定範囲の差分をレビュー
- 未指定の場合は、直近コミットの差分をレビュー

## レビュー手順

### 1. 対象の特定

差分ベースの場合:
```bash
git diff HEAD~1 --stat
```

### 2. Gemini CLI でレビュー実行

**必ず `-m gemini-3-flash-preview` 以上のモデルを指定すること。**

#### 差分レビュー（推奨）
```bash
git diff HEAD~1 -- <file> | gemini -m gemini-3-flash-preview -p "Review this diff for a DeFi liquidity management system (Cetus CLMM on Sui). Check: 1) Fund safety - no unintended fund loss 2) Slippage protection 3) Price direction correctness (sqrtPriceX64ToPrice returns coinB/coinA) 4) Edge cases with NaN/Infinity 5) Gas reserve handling 6) Error recovery safety. Report Critical/Warning/Info." 2>/dev/null
```

#### ファイル全体レビュー
```bash
cat <file> | gemini -m gemini-3-flash-preview -p "Security and quality review for DeFi LP management code on Sui/Cetus CLMM. Focus on: fund isolation, transaction safety, price calculation correctness, error handling." 2>/dev/null
```

#### 複数ファイル横断レビュー
```bash
cat src/core/rebalance.ts src/core/position.ts | gemini -m gemini-3-flash-preview -p "Cross-review these two modules for logical consistency, data flow integrity, and DeFi safety." 2>/dev/null
```

### 3. Claude 側での所見統合

Gemini の指摘を受けて:
1. Critical 指摘があれば該当コードを確認し、妥当性を検証
2. Warning 指摘は改善提案としてまとめる
3. 既知の仕様（価格方向等）による誤検知はフィルタ

### 4. レポート出力

```
## 外部AIレビューレポート

### 対象
- ファイル/差分範囲

### Gemini 所見
> (Gemini の出力を引用)

### Claude 検証結果
- Critical: 検証済み/誤検知の判定
- Warning: 対応推奨/既知仕様の判定

### 推奨アクション
- 対応が必要な項目リスト
```

**注意**:
- `.env` や秘密鍵を Gemini に送信しない
- stderr は `2>/dev/null` で抑制
