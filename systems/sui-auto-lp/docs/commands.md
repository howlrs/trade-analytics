# Sui Auto LP - コマンドリファレンス

## セットアップ

### 依存パッケージのインストール

```bash
npm install
```

### 環境変数の設定

`.env` ファイルをプロジェクトルートに作成する。

```bash
cp .env.example .env
```

| 変数名                     | 必須     | デフォルト   | 説明                                                                     |
| -------------------------- | -------- | ------------ | ------------------------------------------------------------------------ |
| `SUI_NETWORK`              | -        | `testnet`    | `mainnet` または `testnet`                                               |
| `SUI_PRIVATE_KEY`          | **必須** | -            | Sui 秘密鍵（`suiprivkey1...` bech32 形式 または base64）                 |
| `POOL_IDS`                 | **必須** | -            | 監視対象プール ID（カンマ区切りで複数指定可）                            |
| `POSITION_IDS`             | -        | 全ポジション | 管理対象ポジション ID（カンマ区切り）。state.json があればそちらが優先   |
| `REBALANCE_THRESHOLD`      | -        | `0.03`       | リバランス閾値（0〜1）。レンジ端から何%以内で発動するか（推奨: `0.10`）  |
| `CHECK_INTERVAL`           | -        | `30`         | リバランスチェック間隔（秒）                                             |
| `HARVEST_INTERVAL`         | -        | `7200`       | ハーベスト（手数料claim）チェック間隔（秒）                              |
| `SLIPPAGE_TOLERANCE`       | -        | `0.01`       | スリッページ許容値（1% = 0.01）                                          |
| `MIN_GAS_PROFIT_RATIO`     | -        | `2`          | 最低利益/ガス比率                                                        |
| `HARVEST_THRESHOLD_USD`    | -        | `0.50`       | ハーベスト実行の最低手数料額（USD）                                      |
| `LOG_LEVEL`                | -        | `info`       | `debug` / `info` / `warn` / `error`                                      |
| `DRY_RUN`                  | -        | `true`       | `true`: シミュレーションのみ、`false`: 実際にトランザクション実行        |
| `SWAP_FREE_REBALANCE`      | -        | `true`       | `true`: リバランス時のスワップをスキップ（0.25%手数料回避）              |
| `MAX_SWAP_COST_PCT`        | -        | `0.01`       | スワップコスト上限（ポジション価値に対する割合）                         |
| `SWAP_FREE_MAX_RATIO_SWAP` | -        | `0.10`       | swap-free 時の ratio-correction スワップ上限（range-out時は50%に緩和）   |
| `MAX_IDLE_SWAP_RATIO`      | -        | `0.45`       | idle deploy 時のスワップ上限（ソース残高に対する割合）。超過分は部分投入 |
| `PAUSED`                   | -        | `false`      | `true`: リバランス・ハーベストをスキップ（再起動不要）                   |

---

## ボット（自動監視・リバランス）

### 開発モード（ホットリロード付き）

```bash
npm run dev
```

ファイル変更を検知して自動リスタートする。`DRY_RUN=true` のまま使用を推奨。

### 本番起動

```bash
# TypeScript から直接実行
npx tsx src/index.ts

# ビルド後に実行（推奨）
npm run build
npm run start
```

### 永続的なバックグラウンド実行

#### nohup（シンプル）

```bash
nohup npx tsx src/index.ts > bot.log 2>&1 &
echo $!  # PID を表示

# 停止（SIGINT でグレースフル停止）
kill -SIGINT <PID>
```

#### pm2（推奨）

```bash
npm install -g pm2
pm2 start npx --name "sui-auto-lp" -- tsx src/index.ts
pm2 status
pm2 logs sui-auto-lp
pm2 stop sui-auto-lp
pm2 restart sui-auto-lp
pm2 startup && pm2 save  # OS 再起動後も自動起動
```

#### systemd（Linux サービス）

```bash
sudo tee /etc/systemd/system/sui-auto-lp.service << 'EOF'
[Unit]
Description=Sui Auto LP Bot
After=network.target

[Service]
Type=simple
User=o9oem
WorkingDirectory=/home/o9oem/projects/mine/sui-auto-lp
ExecStart=/usr/bin/npx tsx src/index.ts
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable sui-auto-lp
sudo systemctl start sui-auto-lp
```

### DRY_RUN の切り替え

```bash
DRY_RUN=true npx tsx src/index.ts   # シミュレーションモード
DRY_RUN=false npx tsx src/index.ts  # ライブモード
```

---

## 診断・レポート

```bash
npm run health            # ヘルスチェック（詳細は operations.md 参照）
npm run health:verbose    # 詳細出力
npm run health:json       # JSON 出力
npm run report            # 今日の日報
npm run report:all        # 全日付の日報
```

---

## 開発ツール

```bash
npm run type-check        # 型チェック（tsc --noEmit）
npm run build             # ビルド（dist/ に出力）
```

---

## デプロイ

詳細は [deploy.md](deploy.md) を参照。

```bash
bash deploy/setup.sh      # 初回 GCP VM セットアップ
bash deploy/deploy.sh     # コード更新デプロイ
```

---

## Sui CLI（参考）

```bash
sui client balance         # ウォレット残高確認
sui client active-address  # アクティブアドレス確認
sui client objects          # オブジェクト一覧
sui client switch --env mainnet  # ネットワーク切り替え
```

---

## 関連ドキュメント

| ドキュメント | 内容 |
|---|---|
| [deploy.md](deploy.md) | デプロイ運用ガイド（セットアップ、状態パターン、ロールバック） |
| [rebalance.md](rebalance.md) | リバランス挙動・ガードレール・エッジケース |
| [harvest.md](harvest.md) | ハーベスト（Claim）挙動 |
| [operations.md](operations.md) | 日常運用・監視・一時停止・緊急対応 |
| [analysis.md](analysis.md) | 分析スクリプト・DeepBook Margin |
| [ops-review.md](ops-review.md) | 運用レビュー評価テンプレート |
| [flow.md](flow.md) | ロジックフロー（Mermaid 図） |
| [price-direction.md](price-direction.md) | CLMM 価格方向ガイド |

---

## ディレクトリ構成

```
sui-auto-lp/
├── src/
│   ├── index.ts              # エントリポイント
│   ├── scheduler.ts          # 定期実行スケジューラ
│   ├── config/index.ts       # 設定ロード（dotenv + Zod）
│   ├── core/
│   │   ├── pool.ts           # プール情報取得・Cetus SDK 初期化
│   │   ├── position.ts       # ポジション CRUD（open/close/addLiquidity）
│   │   ├── price.ts          # Tick ↔ 価格変換
│   │   ├── rebalance.ts      # リバランス実行（close → swap → open）
│   │   ├── swap.ts           # スワップ計算・実行
│   │   └── compound.ts       # 手数料・リワードのclaim（harvest）
│   ├── strategy/
│   │   ├── range.ts          # 価格範囲戦略（narrow/wide/dynamic）
│   │   ├── trigger.ts        # リバランストリガー判定（クールダウン・収益性ゲート）
│   │   └── volatility.ts     # ボラティリティ計測・動的tick幅決定
│   ├── types/
│   │   ├── index.ts          # PoolInfo, PositionInfo 等の型定義
│   │   └── config.ts         # Zod スキーマ
│   └── utils/
│       ├── logger.ts         # Winston ロガー
│       ├── wallet.ts         # 秘密鍵 → Ed25519Keypair
│       ├── sui.ts            # SuiClient 初期化
│       └── event-log.ts      # イベントログ（JSONL ファイル出力）
├── scripts/                  # 分析・管理スクリプト（analysis.md 参照）
├── logs/                     # イベントログ出力先（自動生成）
├── deploy/
│   ├── setup.sh              # GCP VM 初期セットアップ
│   ├── deploy.sh             # コード更新デプロイ
│   ├── logs.sh               # GCE ログ取得
│   ├── ssh-setup.sh          # SSH 鍵セットアップ
│   └── sui-auto-lp.service   # systemd ユニットファイル
├── docs/                     # ドキュメント（本ファイル含む）
├── test/
│   ├── unit/                 # ユニットテスト
│   └── integration/          # 統合テスト
├── state.json                # ポジション状態永続化（自動生成）
├── .env                      # 環境変数（git 管理外）
├── .env.example              # 環境変数テンプレート
├── package.json
└── tsconfig.json
```
