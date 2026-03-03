---
description: Operational review using docs/ops-review.md template
allowed-tools: Bash, Read, Grep, Glob
---

運用レビューを `docs/ops-review.md` のテンプレートに沿って実行してください。

$ARGUMENTS に期間を指定できます（例: `12h`, `24h`, `7d`）。デフォルトは12時間。

## 手順

### 1. テンプレート読み込み
```
docs/ops-review.md を読み込み、評価テンプレートと評価の観点を確認
```

### 2. サービス状態確認
```bash
GCE_PASSPHRASE=$(grep GCE_PASSPHRASE .env | cut -d= -f2) bash deploy/logs.sh status
```

### 3. 重要イベント抽出（各キーワードを個別に grep）

以下のキーワードを **個別に** grep すること（パイプ結合は失敗しやすい）:

- `Rebalance completed`
- `Cooldown`
- `Harvest`
- `compound`
- `error`
- `warn`
- `idle`
- `open`
- `Volatility engine`

```bash
GCE_PASSPHRASE=$(grep GCE_PASSPHRASE .env | cut -d= -f2) bash deploy/logs.sh --since "12 hours ago" -g 'キーワード'
```

### 4. テンプレートに沿ってレポート作成

`docs/ops-review.md` の「評価テンプレート」セクションに完全に準拠した形式で出力。

### 5. チェックリスト確認

「評価の観点」セクションのチェックリストで正常性・収益性・リスクを確認。

**重要**: ログ grep は1キーワードずつ、またはシングルクォートの OR パターンで。
