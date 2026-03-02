# Issue #29 デプロイ引き継ぎドキュメント

## デプロイ情報

| 項目 | 値 |
|---|---|
| **デプロイ日時 (UTC)** | 2026-02-19T05:44:24Z (code), 2026-02-19T05:51:30Z (env + restart) |
| **デプロイ日時 (JST)** | 2026-02-19 14:44 (code), 14:51 (.env反映) |
| **コミット** | `2aee4f5` (main) |
| **Issue** | #29 LP戦略パラメータ最適化 |
| **デプロイ方法** | `bash deploy/deploy.sh` |

## 変更概要

### パラメータ変更
| パラメータ | 旧値 | 新値 | 影響 |
|---|---|---|---|
| `narrowRangePct` | 0.03 (3%) | 0.08 (8%) | リバランス時の新レンジ幅 |
| `wideRangePct` | 0.08 (8%) | 0.15 (15%) | 同上（wide戦略時） |
| `volTickWidthMin/Max` | 240/600 | 480/1200 | ボラティリティベースのtick幅 |
| `rebalanceThreshold` | 0.10 (10%) | 0.03 (3%) | threshold trigger閾値 |
| `harvestThresholdUsd` | $3.00 | $0.50 | fee harvest最低額 |
| `COOLDOWN_UP_SEC` | 300s (5min) | 1800s (30min) | 上方クールダウン |
| `COOLDOWN_DOWN_SEC` | 600s (10min) | 3600s (60min) | 下方クールダウン |
| `maxBreakevenHours` | 12h | 48h | profitability gate |

### 新ガードレール
| ガードレール | デフォルト | 説明 |
|---|---|---|
| `waitAfterRangeoutSec` | 1800s (30min) | レンジアウト検出後の待機 |
| `maxRebalancesPerDay` | 3 | 1日あたり最大リバランス回数 |
| `minTimeInRangeSec` | 7200s (2h) | 新ポジション開設後の最低レンジ内時間 |

### 再起動安全策
- `positionOpenedAt` が未設定の場合、スケジューラ起動時刻をフォールバックとして使用
- デプロイ直後2時間は threshold trigger が抑制される

## 現在の運用状態

- **ポジション**: 現在のナローレンジ（低ボラティリティ）でそのまま運用
- **リバランス**: レンジ内 → リバランスなし
- **次のアクション**: レンジ外に出た場合、新ガードレールが適用される

### レンジアウト時のフロー
1. レンジアウト検出 → **30分待機**（自己修復を待つ）
2. 30分経過しても戻らない → **profitability gate** (breakeven < 48h?)
3. 通過 → **クールダウンチェック** (up: 30min, down: 60min)
4. 通過 → **日次上限チェック** (3回/日)
5. 全通過 → リバランス実行 → 新ポジションは**ワイドレンジ**で開設

## 検証方法

### 即時確認（デプロイ後）

```bash
# サービス状態
GCE_PASSPHRASE=xxx bash deploy/logs.sh status

# 直近ログ
GCE_PASSPHRASE=xxx bash deploy/logs.sh --since "5 min ago"

# リアルタイム追従
GCE_PASSPHRASE=xxx bash deploy/logs.sh -f
```

### リバランス発生後の検証

検証スクリプトを実行:

```bash
GCE_PASSPHRASE=xxx bash scripts/verify-issue29.sh
```

または手動で以下を確認:

1. **リバランスログの確認**
   ```bash
   GCE_PASSPHRASE=xxx bash deploy/logs.sh --since "2026-02-19 05:44" -g rebalance
   ```

2. **確認ポイント**
   - [ ] `waitAfterRangeoutSec` ログが出ている（30分待機が発動した）
   - [ ] `Profitability gate` ログで `maxBreakevenHours: 48` が表示
   - [ ] `Cooldown` ログで 1800s または 3600s が表示
   - [ ] `Daily rebalance limit` が3を超えていない
   - [ ] 新ポジションの tick 幅が旧ポジションより広い
   - [ ] `Optimal range calculated` ログで volatilityTickWidth が 480以上

3. **日報で損益確認**
   ```bash
   npm run report
   ```

## トラブルシューティング

### リバランスが全く発生しない場合
- profitability gate (48h) でブロックされている可能性
- ログで `breakeven XXXh > 48h limit` を確認
- fee accrual が極端に低い場合は `HARVEST_THRESHOLD_USD` を下げても改善しない（根本はpool volume）

### 過剰リバランスが発生する場合
- `PAUSED=true` で即時停止（再起動不要）
- ログで daily count を確認: `Daily rebalance limit`
- waitAfterRangeoutSec を延長（.env 経由では不可、コード変更必要）

### ロールバック
```bash
git revert 2aee4f5 bc4dda5 13cec78  # 3コミットをrevert
bash deploy/deploy.sh
```
