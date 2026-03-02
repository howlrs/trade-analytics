---
description: Analyze positions and suggest rebalance actions
allowed-tools: Bash, Read, Grep, Glob
---

Cetus CLMMポジションのリバランス分析を行ってください。

1. 現在のポジション情報を取得（価格範囲、流動性、未回収手数料）
2. 現在の市場価格を取得
3. 以下を分析:
   - 現在価格が範囲内か（in-range / out-of-range）
   - 範囲端からの距離（%）
   - リバランス推奨判定（threshold超過かどうか）
   - 推奨する新しい価格範囲
   - 未回収手数料のコンパウンド推奨
4. 推奨アクションを一覧表示

**注意**: 実際のトランザクション実行前に必ずユーザーの確認を取ること。
ドライランで見積もりを表示し、承認後にのみ実行する。

$ARGUMENTS にプールIDが指定されていればそのプールのみ分析。
