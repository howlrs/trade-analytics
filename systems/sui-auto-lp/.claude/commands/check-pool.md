---
description: Check Cetus pool status and current positions
allowed-tools: Bash, Read, Grep, Glob
---

指定されたCetus CLMMプールの現在の状態を確認してください。

1. `sui client active-address` でアクティブウォレットを確認
2. `sui client balance` で残高を確認
3. TypeScript SDKを使って以下を取得:
   - プールの現在価格
   - プールのTVL・流動性
   - 自分のポジション一覧（tick範囲、流動性量、未回収手数料）
4. 各ポジションについて、現在価格が範囲内かどうかを判定
5. 結果をテーブル形式で表示

$ARGUMENTS にプールIDが指定されていればそのプールのみ確認。
指定がなければ .env の POOL_IDS に設定された全プールを確認。
