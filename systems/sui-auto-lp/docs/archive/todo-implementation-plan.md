# リバランスコスト最適化 実装計画 (ToDo)

本ドキュメントは、リバランスコスト分析（2026-02-23）で起票された8件の Issue (#43-#50) に対する実装計画をフェーズ別にまとめたものです。
専門家分析および第三者レビューの合意内容に基づき、コスト削減効果の高いものから優先して進行します。

**背景**: 直近24hでリバランス4回・総コスト $9.95（うち Idle deploy スワップが99.6%）、コスト/収益比 40.2%。P0/P1 適用後の期待改善: コスト $3-5、純利益 +35-50%。

---

## P0: 即時対応（.env 変更 + 小規模コード修正） ✅ 完了

リバランス頻度の抑制と安全機構の改善。設定変更のみで即効果が出る項目。

### [x] Issue #44: REBALANCE_THRESHOLD 引き下げ (15% → 10%)
*   **概要**: 直近24hで threshold トリガーが3回発火（14.3%, 12.9%, 9.2%）。#47 のシミュレーションで3回中3回が見送り可能と判明。現行15%は広すぎる。
*   **専門家分析**: 8% を推奨（3回中2回を抑制）。
*   **第三者レビュー**: 10-12% を推奨。threshold 発火を制限しつつ、range-out 前の予防的リバランス余地を残す。
*   **合意**: **10% で開始**し、1-2週間のデータ蓄積後に再評価。
*   **ToDo**:
    *   [x] `.env` の `REBALANCE_THRESHOLD=0.15` → `0.10` に変更
    *   [ ] デプロイ後1週間のリバランス頻度を監視（目標: 1日1-2回以下）

### [x] Issue #44: maxRebalancesPerDay ソフトリミット化
*   **概要**: 現行は全トリガーに対して暦日3回の上限を一律適用。threshold とrange-out を区別しない。
*   **専門家分析**: ローリング24hウィンドウ化 or 上限2に引き下げ。
*   **第三者レビュー**: **ソフトリミットを強く推奨**。threshold 発火は日3回上限、range-out の緊急リバランスは上限を突破許可。ハードリミットは IL 増大リスクあり。
*   **合意**: **ソフトリミット採用**。`evaluateRebalanceTrigger()` 内で trigger 種別により制限を分岐。
*   **ToDo**:
    *   [x] `src/strategy/trigger.ts` の `dailyRebalanceCount` チェックを修正: `isOutOfRange` の場合は上限チェックをバイパス
    *   [x] テスト追加: ソフトリミット分岐の検証（threshold はブロック、range-out は通過）

### [x] Issue #44: daily count の state.json 永続化
*   **概要**: `dailyRebalanceCount` は in-memory のみ。サービス再起動でカウントがリセットされ、デプロイ直後に過剰リバランスが起きるリスクがある。
*   **ToDo**:
    *   [x] `state.json` に `dailyRebalanceCounts: { [positionId]: { date: string, count: number } }` を追加
    *   [x] `src/strategy/trigger.ts` でカウント読み書きを state 経由に変更（`saveDailyRebalanceCount`, `loadDailyRebalanceCounts`）
    *   [x] `src/scheduler.ts` 起動時に `initDailyRebalanceCounts()` を呼び出し

---

## P1: コスト削減の本丸（コード変更・中規模） ✅ 完了

Idle deploy スワップコストの削減。総コストの99.6%を占めるため、最大の改善効果が見込める。

### [x] Issue #43: maxIdleSwapRatio パラメータ導入 + 部分投入
*   **概要**: `deployIdleFunds()` にスワップ量の制限が一切なく、ポジション規模の40-50%がスワップされるケースがある。コスト $9.91 / $9.95 = 99.6%。
*   **専門家分析**: `maxIdleSwapRatio` 15% + 超過時スワップなし addLiquidity のハイブリッド方式。
*   **第三者レビュー**: 20-30% 上限 + **部分投入**（スワップ可能額だけスワップし残りは次回）。スリッページ影響も考慮。
*   **合意**: **20% を初期値、部分投入方式を採用**。
*   **ToDo**:
    *   [x] `src/types/config.ts` に `maxIdleSwapRatio: z.number().min(0).max(1).default(0.20)` 追加
    *   [x] `src/config/index.ts` に `MAX_IDLE_SWAP_RATIO` 環境変数パース追加
    *   [x] `src/core/rebalance.ts` の `deployIdleFunds()` 内でスワップ比率チェックを実装:
        *   スワップ見込額をソーストークン残高で除してスワップ比率を算出
        *   比率 > `maxIdleSwapRatio` の場合、スワップ額を上限値にキャップ（部分投入）
        *   残余トークンはスワップなしで addLiquidity を試行（SDK が受入可能な分だけ投入）
    *   [x] ログ出力: スワップ比率、キャップ適用有無、実際のスワップ額
    *   [x] テスト追加: ConfigSchema に maxIdleSwapRatio バリデーション

### [x] Issue #45: swapFreeMaxRatioSwap 引き上げ (10% → 20%)
*   **概要**: swapFree リバランスの比率補正スワップ上限が10%で、range-out 時の大きな偏りを補正できない。結果として Idle deploy で大量スワップが発生する。
*   **専門家分析**: 20% に引き上げ。range-out 時のみ50%まで許容する分岐も検討。
*   **第三者レビュー**: **片側余剰の部分ホールド + Mean Reversion 待ち**を提案。偏った残高を無理にスワップせず、価格の自然回復でバランス回復を待つ方がトータルコスト安。
*   **合意**: **部分ホールドをデフォルト + swapFreeMaxRatioSwap 20%**。range-out 時は50%まで緩和。
*   **ToDo**:
    *   [x] `.env` の `SWAP_FREE_MAX_RATIO_SWAP=0.10` → `0.20` に変更
    *   [x] `src/core/rebalance.ts` の swapFree ブロック内で `decision.trigger === 'range-out'` 時に `maxRatio` を `Math.max(config.swapFreeMaxRatioSwap, 0.50)` に引き上げる分岐を追加
    *   [x] テスト更新: range-out 時の緩和動作確認（integration テスト修正済み）

---

## P2: 分析基盤の構築（新規スクリプト） ✅ 完了

継続的な戦略評価とデータ駆動の意思決定を支えるツール群。

### [x] Issue #46: ROI 集計スクリプト作成
*   **概要**: リバランスのコスト効果を事後的に定量評価するツールがない。Profitability gate の精度評価にも必要。
*   **専門家分析**: 全リバランスは採算超え（平均ROI 215%）だが、profitability gate に idle deploy コストが未反映。
*   **第三者レビュー**: **ログ解析スクリプト作成が先決**。不採算パターン特定 → gate 改善のフローを推奨。
*   **ToDo**:
    *   [x] `scripts/analyze-rebalance-roi.ts` を作成
        *   イベントログから rebalance_complete, harvest イベントをパース
        *   各リバランスのエポック（次リバランスまでの期間）ごとに: コスト / レンジ内滞在時間 / 獲得手数料 / ROI を算出
        *   不採算パターン（ROI < 1.0、短時間レンジアウト）を特定
    *   [ ] profitability gate へのフィードバック: gate 計算に idle deploy 推定コストを加算する改善を検討

### [x] Issue #47: Counter-factual 分析ツール作成
*   **概要**: threshold リバランスを見送った場合の機会損失を定量化するツール。
*   **第三者レビュー**: 過去価格データとの突合ツール作成を提案。見送り戦略の費用対効果を証明し、threshold 廃止 or 極端な引き下げの意思決定に活用。
*   **ToDo**:
    *   [x] `scripts/simulate-skip.ts` を作成
        *   各 threshold リバランス直前の旧レンジと実価格推移を突合
        *   見送った場合の OOR 時間と機会損失を算出
        *   「リバランスコスト vs OOR 機会損失」の比較テーブルを出力
        *   verdict: skip-safe / skip-risky / skip-costly の分類

### [x] Issue #48: 曜日×時間帯ヒートマップ作成
*   **概要**: 時間帯別の手数料蓄積パターンを可視化し、Dynamic Harvest Interval の根拠データを蓄積する。
*   **専門家分析**: アジア $1.15/h > 欧州 $0.52/h > 米国 $0.67/h（24hデータ、統計不十分）。
*   **第三者レビュー**: **曜日×時間帯ヒートマップ作成を推奨**。1-2週間のデータ蓄積後に Dynamic Interval（活況1h / 閑散4h）の実装判断。
*   **ToDo**:
    *   [x] `scripts/revenue-heatmap.ts` を作成
        *   イベントログから harvest イベントを時間帯別に集計
        *   曜日（月-日）× 時間帯（2h枠）のASCIIヒートマップを出力
        *   セッション別サマリー（アジア/欧州/米国）、Weekday/Weekend比較
    *   [ ] データ蓄積後に Dynamic Harvest Interval の実装要否を判断

### [x] Issue #50: Cooldown ログ改善 + 役割整理
*   **概要**: Cooldown の発動実績が debug レベルで観測困難。waitAfterRangeoutSec との役割重複あり。
*   **専門家分析**: 実質追加抑制0回。他ガードが先に効いている。
*   **第三者レビュー**: cooldown=連続実行防止、waitAfterRangeout=価格安定待ち に**役割を明確化**。ニアミス事例抽出で延長効果を裏付けるべき。
*   **ToDo**:
    *   [x] Cooldown 開始/満了時のログレベルを `debug` → `info` に変更
    *   [x] ログメッセージに残り時間（`remainingSec`）と `isOutOfRange` 状態を含める
    *   [x] コード内コメントで cooldown と waitAfterRangeoutSec の役割を明記

---

## P3: 検証・フォワードテスト（データ蓄積後に判断）

十分な運用データが揃った段階で実施する検証項目。

### [ ] Issue #49: volTickWidthMin フォワードテスト
*   **概要**: 現行480が純収益ベースでモデル上最適だが、3回分のデータでは統計不十分。オーバーフィット懸念あり。
*   **専門家分析**: 480維持。σティア細分化（σ<20→480, σ20-40→600）を検討。
*   **第三者レビュー**: **フォワードテストが最速**。volTickWidthMin を600に引き上げて数日間稼働し実測比較。バックテスト用クラスタリング分析も並行。
*   **ToDo**:
    *   [ ] P0/P1 のデプロイ後、1週間の安定稼働を確認
    *   [ ] `volTickWidthMin` を 480 → 600 に変更してフォワードテスト開始（数日間）
    *   [ ] テスト前後の純収益（$/day）、リバランス頻度、レンジ内滞在時間を比較
    *   [ ] 結果に応じて 480 維持 or 600 採用を判断

### [ ] Issue #50: 自己修復率の上方/下方集計
*   **概要**: 非対称 cooldown（上方30分/下方60分）の根拠をデータで裏付ける。
*   **第三者レビュー**: `range.out` → `range.in` ログから上方/下方の自己修復率を比較。ニアミス事例（cooldown中に自己修復した事例）を抽出。
*   **ToDo**:
    *   [ ] P2 のログ改善後、2-4週間のデータを蓄積
    *   [ ] 上方/下方別の自己修復率を集計
    *   [ ] ニアミス事例数に応じて cooldown 延長（上方60分/下方120分等）を判断

### [ ] Issue #48: Dynamic Harvest Interval 実装
*   **概要**: 時間帯別にハーベスト間隔を可変化（活況期1h / 閑散期4h）。
*   **第三者レビュー**: `peakHoursInterval: 1h, offPeakInterval: 4h` のconfig化を提案。
*   **補足**: Issue #71 でコンパウンドパスを削除し、全て harvest-only (collectRewarder) に統一済み。
*   **ToDo**:
    *   [ ] P2 のヒートマップで時間帯パターンが確認できた場合に実装
    *   [ ] `src/scheduler.ts` に時間帯別インターバルロジックを追加

---

## 実装順序とデプロイ計画

```
Phase 1 (P0): .env変更 + ソフトリミット + daily count永続化  ✅ 完了
  ↓ デプロイ → 1週間監視
Phase 2 (P1): maxIdleSwapRatio + swapFreeMaxRatioSwap + range-out緩和  ✅ 完了
  ↓ デプロイ → 1週間監視
Phase 3 (P2): 分析スクリプト群 (ROI集計, Counter-factual, ヒートマップ, ログ改善)  ✅ 完了
  ↓ データ蓄積 2-4週間
Phase 4 (P3): フォワードテスト + Dynamic Interval（データ駆動で判断）
```

### 期待効果（P0+P1 適用後）

| 指標 | 現状 (24h) | P0+P1 適用後 (推定) |
|---|---|---|
| リバランス回数 | 4回 | 1-2回 |
| リバランス総コスト | $9.95 | $3-5 |
| コスト/収益比 | 40.2% | 12-20% |
| 純利益 | $14.80 | $20-22 |
| 改善率 | — | **+35-50%** |

---

## 関連ドキュメント

- [リバランスコスト分析 Issue一覧](https://github.com/howlrs/sui-auto-lp/issues?q=is%3Aissue+label%3A): #43-#50
- [運用レビュー手順書](./ops-review.md)
- [戦略パラメータ最適化 (#29)](./issue-29-handoff.md)
- [価格方向ガイド](./price-direction.md)

---

## 過去の完了済み実装（アーカイブ）

<details>
<summary>Phase 1-3 (2026-02-20 完了): Issue #24-#28, #32, #33</summary>

### Issue #24-#28, #32: バグ修正・最適化（全完了）
- **#24**: gasCost マイナス値クランプ処理追加
- **#25**: preswap の currentSqrtPrice 精度修正
- **#26**: alignTickToSpacing を Math.floor に変更
- **#27**: event-log WriteStream のエラーハンドラ追加
- **#28**: getDirectPoolQuote のデシマル値ハードコード除去
- **#32**: ボラティリティエンジンの3段フォールバック実装

### Issue #33: Swap-Free Rebalance（完了）
- リバランス時の0.25%スワップ手数料を回避
- `SWAP_FREE_REBALANCE=true` / `SWAP_FREE_MAX_RATIO_SWAP=0.10`
- GAS_RESERVE 0.5 → 1.0 SUI に増加
- コミット: `39d414a`, `77fb324`, `a21e2f8`
- 2026-02-20 10:47 UTC デプロイ完了

</details>

<details>
<summary>P0-P2 (2026-02-23 完了): Issue #43-#48, #50</summary>

### P0: リバランス頻度最適化
- **#44**: REBALANCE_THRESHOLD 15% → 10%
- **#44**: maxRebalancesPerDay ソフトリミット化（range-out はバイパス）
- **#44**: dailyRebalanceCount の state.json 永続化

### P1: Idle deploy スワップコスト削減
- **#43**: maxIdleSwapRatio パラメータ導入（デフォルト20%、部分投入方式）
- **#45**: swapFreeMaxRatioSwap 10% → 20%、range-out 時は50%まで緩和

### P2: 分析基盤
- **#46**: `scripts/analyze-rebalance-roi.ts` — リバランスROI分析
- **#47**: `scripts/simulate-skip.ts` — Counter-factual シミュレーション
- **#48**: `scripts/revenue-heatmap.ts` — 曜日×時間帯ヒートマップ
- **#50**: Cooldown ログレベルを info に昇格、残り時間表示追加

</details>
