---
description: Collect fees and rewards (harvest) from existing positions
allowed-tools: Bash, Read, Grep, Glob
---

Cetus CLMMポジションの手数料・リワード回収（harvest）を分析してください。

1. 全ポジションの未回収手数料・リワードを取得
2. 各ポジションについて:
   - 未回収手数料額（トークンA, トークンB）
   - 推定USD換算額
   - ハーベスト時のガス代見積もり
   - 手数料 > 閾値 であればハーベスト推奨
3. ハーベスト推奨ポジションの一覧を表示
4. ユーザー承認後、手数料・リワードを collectRewarder で回収

**注意**: 必ずdry-runで見積もりを表示し、ユーザー確認後にのみ実行する。
