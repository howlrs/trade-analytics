# DeepBook Margin Short ヘッジ設計

**Status**: Draft (未実装)
**Date**: 2026-03-03
**Author**: Claude Code

## 概要

Cetus CLMM LP の SUI 下落リスクを部分的にヘッジするため、DeepBook v3 Margin で SUI ショートポジションを常時保持する。利益部分 (~$150 USDC) を担保に低レバレッジショートを建て、LP の Impermanent Loss を軽減する。

## 背景・動機

- 現行 LP は SUI/USDC プールに ~$3,170 を集中投入
- SUI 下落時、LP は SUI heavy になり IL が拡大する（例: 2/26-3/3 で SUI -10%、IL影響 ≈ -$377）
- LP 手数料収入 ($105/2.6日) では下落局面の IL を吸収しきれない
- 利益の一部 (~$150) を活用し、ショートヘッジで IL を部分的に相殺したい

## 要件

- **ヘッジ方向**: SUI 下落方向のみ（常時ショート）
- **ヘッジ手段**: DeepBook v3 Margin Short
- **ヘッジ予算**: ~$150 USDC（LP利益部分、env設定可能）
- **自動化**: 既存スケジューラに統合。起動時にヘッジ open、リバランス後にサイズ調整
- **安全性**: 危険な売り指値は使わない。清算リスクを低く保つ（低レバレッジ）

## アーキテクチャ

### 新規ファイル

```
src/
├── core/
│   └── hedge.ts          # ヘッジポジション管理 (open/close/adjust/monitor)
└── strategy/
    └── hedge-sizing.ts   # LP構成比からヘッジサイズ計算
```

### 既存ファイル修正

- `src/scheduler.ts` — ヘッジチェックをリバランスサイクル・Harvestサイクルに統合
- `src/config/` — ヘッジパラメータ追加
- `src/types.ts` — ヘッジ関連型定義追加

### 統合フロー

```
scheduler loop (30s)
  ├── rebalance check (既存)
  │   └── post-rebalance → hedgeAdjust()  # LP構成比変化に追従
  ├── harvest cycle (2h毎, 既存)
  │   └── post-harvest → hedgeHealthCheck()  # riskRatio監視
  └── hedge check (新規, harvest cycleに便乗)
      ├── hedgeMonitor()  # PnL, borrowing rate, riskRatio
      └── hedgeAdjust()   # サイズ調整 (閾値超過時のみ)
```

## ヘッジサイズ計算

### LP構成比連動ロジック

```
suiRatio = LP内SUI価値 / LP総価値  (0.0 〜 1.0)
hedgeCoverage = lerp(0.05, 0.10, (suiRatio - 0.5) / 0.3)
  → SUI 50% (balanced): 5%
  → SUI 65%:            7.5%
  → SUI 80%+:           10% (cap)

targetShortUsd = lpValueUsd × suiRatio × hedgeCoverage
leverage = lerp(1.2, 2.0, (suiRatio - 0.5) / 0.3)
actualShortUsd = min(hedgeBudget × leverage, targetShortUsd)
```

### 具体例

| LP状態 | SUI比率 | ヘッジカバー | レバレッジ | ショートサイズ |
|--------|---------|-------------|-----------|--------------|
| Balanced | 50% | 5% | 1.2x | $180 (= $150 × 1.2) |
| SUI heavy | 65% | 7.5% | 1.6x | $240 (= $150 × 1.6) |
| Very SUI heavy | 80% | 10% | 2.0x | $300 (= $150 × 2.0) |

$150担保でLP $3,170のSUI 50% = $1,585のうち、$180〜$300をカバー = **約11〜19%ヘッジ**。

### 調整条件

- 現在サイズと目標サイズの差が **20%以上** の場合のみTXを発行
- リバランス直後は必ず再計算（LP構成比が大きく変わるため）
- 最小調整間隔: 30分（無意味な頻繁調整を防止）

## ポジション管理

### ライフサイクル

```
1. サービス起動
   → 既存MarginManager検索 (check-deepbook.tsのdiscover logic)
   → あれば riskRatio/PnL 確認して継続
   → なければ new MarginManager → depositQuote → borrowBase → placeMarketOrder(sell)

2. 通常運用
   → harvest cycle (2h) で health check
   → リバランス後にサイズ調整
   → riskRatio低下時に自動縮小

3. サービス停止
   → ヘッジポジションは維持 (次回起動時に再接続)
   → 手動クローズ用スクリプト提供 (scripts/close-hedge.ts)
```

### DeepBook Margin Short フロー

```
Open:
  depositQuote(usdc)     # USDC担保入金
  borrowBase(sui)        # SUI借入
  placeMarketOrder(sell) # 借りたSUIを市場で売却

Close:
  placeMarketOrder(buy)  # SUIを買い戻し
  repayBase(sui)         # SUI返済
  withdrawQuote(usdc)    # USDC + PnL 引き出し

Adjust (increase):
  borrowBase(additionalSui)
  placeMarketOrder(sell)

Adjust (decrease):
  placeMarketOrder(buy, partialAmount)
  repayBase(partialSui)
```

## リスク管理

### ガードレール

| パラメータ | 値 | 説明 |
|---|---|---|
| `HEDGE_MAX_LEVERAGE` | 2.0 | 最大レバレッジ上限 |
| `HEDGE_MIN_LEVERAGE` | 1.2 | 最小レバレッジ |
| `HEDGE_RISK_RATIO_WARN` | 1.5 | 警告閾値 → レバ自動縮小 |
| `HEDGE_RISK_RATIO_EMERGENCY` | 1.3 | 緊急閾値 → ポジション50%縮小 |
| `HEDGE_BUDGET_USDC` | 150 | 最大担保投入額 |
| `HEDGE_ADJUST_THRESHOLD` | 0.20 | サイズ差20%で調整発動 |
| `HEDGE_BORROW_RATE_WARN` | 50 | 年率50%超で警告ログ |

### 清算防止

- DeepBook の清算ライン: riskRatio ≈ 1.125
- 通常運用帯: riskRatio 1.8〜3.0 (低レバ)
- 1.5以下で自動縮小開始 → 1.3以下で50%強制縮小
- SUI が 2x になっても riskRatio > 1.3 を維持する設計

### LP本体への影響分離

- ヘッジ処理は全て try-catch で囲み、失敗しても LP 本体に影響しない
- ヘッジ TX 失敗時: LP と同じ circuit breaker (5回連続失敗で停止)
- `HEDGE_ENABLED=false` で即座にヘッジ機能を無効化可能 (既存ポジションは維持)
- `DRY_RUN` / `PAUSED` は LP と連動

### 損失シナリオ

| シナリオ | LP影響 | ヘッジ影響 | 合計 |
|---------|--------|-----------|------|
| SUI -10% | IL ≈ -$200 | 利益 +$20〜30 | -$170〜180 (10-15%軽減) |
| SUI -20% | IL ≈ -$500 | 利益 +$45〜60 | -$440〜455 (9-12%軽減) |
| SUI +10% | IL ≈ -$50 | 損失 -$20〜30 | -$70〜80 (悪化) |
| SUI +20% | IL ≈ -$100 | 損失 -$45〜60 | -$145〜160 (悪化) |
| SUI横ばい | 手数料 +$40/day | borrowing rate -$0.1〜1/day | ほぼ変わらず |

**注意**: SUI上昇時はヘッジが損失を出す。常時ショートのトレードオフ。ただし LP の SUI 上昇時 IL は比較的小さい (USDC heavy = 安定側) ため、ネット影響は限定的。

## 環境変数

```bash
# DeepBook Hedge (全てオプション、デフォルト値あり)
HEDGE_ENABLED=true                    # ヘッジ機能ON/OFF
HEDGE_BUDGET_USDC=150                 # 担保投入額 (USDC)
HEDGE_MAX_LEVERAGE=2.0                # 最大レバレッジ
HEDGE_MIN_LEVERAGE=1.2                # 最小レバレッジ
HEDGE_ADJUST_THRESHOLD=0.20           # サイズ調整閾値 (20%)
HEDGE_RISK_RATIO_WARN=1.5             # riskRatio警告
HEDGE_RISK_RATIO_EMERGENCY=1.3        # riskRatio緊急
HEDGE_BORROW_RATE_WARN=50             # borrowing rate警告 (年率%)
HEDGE_MARGIN_MANAGER_ID=              # 既存MarginManager ID (空なら自動検索)
```

## State 永続化

`state.json` に追加:

```json
{
  "hedge": {
    "marginManagerId": "0x...",
    "entryPrice": 0.8978,
    "shortSizeUsd": 225,
    "leverage": 1.5,
    "lastAdjustedAt": "2026-03-03T12:00:00Z",
    "depositedUsdc": 150,
    "totalPnlUsd": 0,
    "cumulativeBorrowCostUsd": 0
  }
}
```

## ログ出力

```json
// ヘッジ評価 (harvest cycle)
{"level":"info","msg":"Hedge evaluation","action":"adjust","currentSize":200,"targetSize":240,"suiRatio":0.62,"leverage":1.6,"riskRatio":2.1}

// ヘッジ実行
{"level":"info","msg":"Hedge executed","type":"increase","delta":40,"newSize":240,"digest":"...","gas":0.003}

// ヘルスチェック
{"level":"info","msg":"Hedge health","riskRatio":2.1,"borrowRate":"12.3%","pnlUsd":-3.50,"status":"healthy"}

// 緊急縮小
{"level":"warn","msg":"Hedge emergency reduce","riskRatio":1.28,"action":"reduce_50pct","oldSize":300,"newSize":150}
```

## DeepBook SDK 注意事項

- SDK v0.28.3 の `mainnetPackageIds.MARGIN_PACKAGE_ID` は古い → 起動時にパッチ必須
- `@mysten/deepbook-v3` は `@mysten/sui` v2.x を要求するが、Cetus SDK は v1.x → 共存問題
  - 現行: v0.28.3 を使い `@mysten/sui` v1.x で動作 (一部型キャストで回避)
- MarginManager は shared object → `getOwnedObjects` で見つからない → TX履歴からdiscover
- `clientOrderId` は `u64` → `Date.now().toString()` を使用

## 手動操作スクリプト

実装時に以下のスクリプトも提供:

```bash
# ヘッジ状態確認
npx tsx scripts/check-hedge.ts

# ヘッジ手動クローズ
npx tsx scripts/close-hedge.ts

# ヘッジ手動オープン (DRY_RUN対応)
npx tsx scripts/open-hedge.ts --usdc 150 --leverage 1.5 --dry-run
```

## 実装優先度

1. `core/hedge.ts` — open/close/adjust/monitor の基本操作
2. `strategy/hedge-sizing.ts` — LP連動サイズ計算
3. `scheduler.ts` 統合 — リバランス後・harvest cycle での呼び出し
4. 手動スクリプト群 — check/open/close
5. state.json 永続化 — クラッシュリカバリ

## 未決事項

- [ ] DeepBook SUI/USDC プールの実際の流動性・スプレッド調査
- [ ] Borrowing rate の過去データ調査 (コスト見積もり精度向上)
- [ ] テストネットでの E2E テスト
- [ ] LP rebalance と hedge adjust の atomicity (同一TX or 順次?)
