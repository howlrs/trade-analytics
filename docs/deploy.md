# デプロイ運用ガイド

## 初回セットアップ (`setup.sh`)

GCP VM の作成からサービス起動まで一括実行する。

```bash
# SUI_PRIVATE_KEY を Secret Manager に保存する場合
export SUI_PRIVATE_KEY='your-base64-key'

# .env がプロジェクトルートに存在すること（全設定値が含まれる）
bash deploy/setup.sh
```

**実行内容:**

1. GCP プロジェクト設定・API 有効化
2. Secret Manager に秘密鍵を保存
3. e2-micro VM 作成（Debian 12, 30GB）
4. ファイアウォール設定（SSH のみ）
5. ローカルで `npm run build`
6. tarball（`package.json`, `package-lock.json`, `dist/`, `deploy/`）+ `.env` を SCP 転送
7. VM 上で Node.js 22 インストール → `npm ci --omit=dev` → systemd 登録・起動

---

## コード更新デプロイ

### Step 1: 事前チェック（必須）

`deploy/pre-deploy-check.sh` を実行する。以下を自動で検証・修正する:

- TypeScript 型チェック
- VM サービス状態確認
- `POOL_IDS` のローカル/VM 一致確認
- `POSITION_IDS` を `state.json`（正とする）から自動同期
- `DRY_RUN` 設定確認

```bash
GCE_PASSPHRASE=$(grep GCE_PASSPHRASE .env | cut -d= -f2) bash deploy/pre-deploy-check.sh
```

> **`POSITION_IDS` について**: リバランスのたびに新ポジションが作成され `.env` の値は古くなる。
> `state.json` が常に正とする。`pre-deploy-check.sh` が自動でローカル・VM 双方を同期する。

### Step 2: デプロイ実行

事前チェックが全て `[OK]` になったら実行する。

```bash
GCE_PASSPHRASE=$(grep GCE_PASSPHRASE .env | cut -d= -f2) bash deploy/deploy.sh
```

### Step 3: デプロイ後の確認

```bash
# サービス起動・ポジション ID の確認（起動ログを見る）
GCE_PASSPHRASE=$(grep GCE_PASSPHRASE .env | cut -d= -f2) bash deploy/logs.sh

# 30秒後にリバランス評価が動いているか確認
GCE_PASSPHRASE=$(grep GCE_PASSPHRASE .env | cut -d= -f2) bash deploy/logs.sh --since "1 minute ago"
```

**確認ポイント:**

- [ ] `Restored position ID from state.json` — state.json から正しい ID が復元されている
- [ ] `Starting scheduler` — 管理対象のプール ID・ポジション ID が正しい
- [ ] `Rebalance evaluation` — 30秒間隔でチェックが実行されている
- [ ] エラーログがない

---

## deploy.sh 内部処理の詳細

`deploy.sh` は **停止 → 更新 → 起動** を自動で行う:

1. ローカルで `npm run build`
2. tarball（`dist/`, `package.json`, `package-lock.json`, `deploy/`）を SCP 転送
3. VM 上で展開 → `npm ci --omit=dev` → systemd 再起動（stop → start）

---

## 転送されないファイル

VM の既存値を維持するため、以下は転送しない:

| ファイル | 理由 |
|---|---|
| `.env` | 秘密鍵・ポジション ID を保護 |
| `state.json` | リバランス履歴・現在ポジション ID を保護 |
| `node_modules/` | VM 上で `npm ci` により再構築 |

---

## デプロイ時の状態パターン

### ポジション有り（通常デプロイ）

最も一般的なケース。`state.json` にポジション ID が保存されており、再起動後も継続管理される。

- `state.json` はデプロイで上書きされないため、ポジション ID・リバランス履歴・日次カウントが保護される
- 起動時に `Restored position ID from state.json` ログが出る

### ポジション無し（初回 or 全解除後）

`state.json` にポジション ID がない場合、`discoverPositions` による auto-discovery フローが発動する。

- プール内のウォレット所有ポジションを自動検出
- 検出されたポジション ID を `state.json` に保存
- ポジションが見つからない場合、新規作成フローへ

### 手動ポジション解除後のデプロイ

手動で `close-position.ts` や Cetus UI からポジションを解除した場合:

- ポジションの liquidity が 0 になっている
- ボットは **0-liquidity recovery mode** を検知し、自動復旧を試みる
- recovery mode ではウォレット全残高を使用して新ポジションを開設する

### range-out 中のデプロイ

現在価格がレンジ外の状態でデプロイした場合:

- `waitAfterRangeoutSec`（30分）の待機タイマーがリセットされる
- デプロイ直後はクールダウンが再計測されるため、すぐにはリバランスしない
- 30分後にレンジ外が継続していれば、通常のリバランスフローが発動

### リバランス直後のデプロイ

直前にリバランスが完了した状態でのデプロイ:

- `minTimeInRangeSec`（2時間）ガードが有効
- threshold トリガーは2時間抑制される（range-out は例外で即発動可能）
- `state.json` にリバランス時刻が永続化されているため、再起動後もガードが維持される

### 過剰リバランス中のデプロイ

当日のリバランス回数が多い状態でのデプロイ:

- `dailyRebalanceCounts` が `state.json` に永続化されている
- 再起動後も日次カウントが正確に復元される
- ソフトリミット（3回/日）に達している場合、threshold/time-based はブロックされるが range-out は通過する

---

## ロールバック手順

デプロイ後に問題が発生した場合:

1. **即時停止**: ボットを一時停止する（[operations.md](operations.md) 参照）
2. **前バージョンの再デプロイ**: `git checkout <previous-commit>` → `bash deploy/deploy.sh`
3. **state.json の確認**: VM 上の `state.json` が破損していないか確認

```bash
# VM 上の state.json を確認
gcloud compute ssh sui-auto-lp --zone=us-central1-a \
  --command="cat /opt/sui-auto-lp/state.json"
```

---

## VM 状態確認

```bash
# サービスステータス
GCE_PASSPHRASE=$(grep GCE_PASSPHRASE .env | cut -d= -f2) bash deploy/logs.sh status

# ログをリアルタイム確認
GCE_PASSPHRASE=$(grep GCE_PASSPHRASE .env | cut -d= -f2) bash deploy/logs.sh -f
```
