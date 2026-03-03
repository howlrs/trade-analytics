---
description: Deploy code to GCE VM with safety checks
allowed-tools: Bash, Read, Grep, Glob
---

GCE VM へのデプロイを安全に実行してください。

## 手順

### 1. Pre-deploy チェック
```bash
bash deploy/pre-deploy-check.sh
```
- 型チェック（`npm run type-check`）
- 未コミット変更の確認
- .env 必須項目の確認

### 2. 現在のサービス状態確認
```bash
GCE_PASSPHRASE=$(grep GCE_PASSPHRASE .env | cut -d= -f2) bash deploy/logs.sh status
```
- サービスが active か確認
- 直近のエラーがないか確認

### 3. デプロイ実行（ユーザー承認後）
```bash
GCE_PASSPHRASE=$(grep GCE_PASSPHRASE .env | cut -d= -f2) bash deploy/deploy.sh
```

### 4. デプロイ後検証
```bash
GCE_PASSPHRASE=$(grep GCE_PASSPHRASE .env | cut -d= -f2) bash deploy/logs.sh status
GCE_PASSPHRASE=$(grep GCE_PASSPHRASE .env | cut -d= -f2) bash deploy/logs.sh --since "2 minutes ago"
```
- サービスが正常に再起動したか
- 初回チェックサイクルがエラーなく完了したか

**注意**:
- デプロイ実行前に必ずユーザーの承認を取ること
- エラーがあればロールバック手順（docs/deploy.md）を提示
