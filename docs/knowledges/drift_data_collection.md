# Drift Protocol データ収集ガイド

## 概要

Drift Protocol DEX MM 戦略構築に必要なデータを継続的に蓄積するためのガイド。
最低 **2週間分** のデータが分析開始の前提条件（`strategic_recommendations.md` Phase 2）。

## 収集スクリプト

### L2 Snapshot (`scripts/collect_drift_data.py`)

板情報を定期ポーリングし、parquet に蓄積。

```bash
# 推奨設定: 5秒間隔ポーリング、1時間ごとにflush
python scripts/collect_drift_data.py --markets SOL --interval 5 --flush-interval 3600
```

**出力**: `data/drift_solusdc_l2_snapshots.parquet`
**スキーマ**: timestamp, market, drift_bid/ask 1-5 (price/size/source), drift_mid, drift_spread_bp, binance_bid/ask/mid, cex_dex_divergence_bp

> **注意**: GCE 版は Binance BBO の代わりに oracle_price/oracle_twap/oracle_div_bp を使用（上記「GCE デプロイ」参照）

### Trade History (`scripts/fetch_drift_trades.py`)

Drift Data API (`data.api.drift.trade`) から約定履歴を取得。

```bash
# 直近1000件取得（ページネーション自動）
python scripts/fetch_drift_trades.py --symbol SOL --limit 1000
```

**出力**: `data/drift_solusdc_trades.parquet`
**スキーマ**: timestamp, price, size, quote_filled, taker_fee, maker_rebate, side, market, action_explanation, tx_sig
**API制限**: 直近31日分、1リクエスト最大50件（自動ページネーション対応）

## L2 Snapshot 長期収集（GCE デプロイ — 推奨）

### 構成

L2 snapshot は過去データ取得不可のため、24/7 稼働の GCE VM でリアルタイム収集する。

- **VM**: `sui-auto-lp` (e2-micro, us-central1-a, project: crypto-bitflyer-418902)
- **実装**: Go + Docker (`systems/drift-data-collector/`)
- **出力**: JSONL (1時間ごとにファイルローテーション) → `/opt/drift-data/`
- **同期**: 毎日 06:00 UTC に GCS (`gs://crypto-bitflyer-drift-data/`) へ rsync
- **リソース**: Docker image 13.7MB, メモリ ~8MB, CPU ~0%

### CEX BBO の代替: Oracle Price

GCE (US リージョン) からは Binance/Bybit API が地理制限 (HTTP 451/403) でブロックされる。
代わりに Drift DLOB API の `includeOracle=true` で取得できる **Pyth oracle price** を使用。
Pyth oracle = CEX 加重価格なので、divergence 計算に十分。

| フィールド | 説明 |
|-----------|------|
| `oracle_price` | Pyth oracle 価格 (≈ CEX mid) |
| `oracle_twap` | Oracle TWAP |
| `oracle_div_bp` | `(drift_mid - oracle_price) / oracle_price * 10000` |

### JSONL スキーマ

```
timestamp, market,
drift_bid1-5_price/size/source, drift_ask1-5_price/size/source,
drift_mid, drift_spread_bp,
oracle_price, oracle_twap, oracle_div_bp
```

### 運用コマンド

```bash
cd systems/drift-data-collector

# 初回セットアップ
bash deploy/setup.sh

# コード更新・再デプロイ
bash deploy/deploy.sh

# ログ確認
bash deploy/logs.sh 20

# GCS → ローカル取得
gsutil -m rsync -r gs://crypto-bitflyer-drift-data/ data/drift-gce/

# JSONL → parquet 変換
python systems/drift-data-collector/convert_jsonl_to_parquet.py \
  --input data/drift-gce/ --output data/drift_solusdc_l2_snapshots.parquet --append
```

### リモート直接確認

```bash
VM="sui-auto-lp"
ZONE="us-central1-a"
PROJECT="crypto-bitflyer-418902"

# コンテナ状態
gcloud compute ssh $VM --zone=$ZONE --project=$PROJECT --command "sudo docker ps"

# メモリ使用量
gcloud compute ssh $VM --zone=$ZONE --project=$PROJECT --command "sudo docker stats drift-collector --no-stream"

# データファイル確認
gcloud compute ssh $VM --zone=$ZONE --project=$PROJECT --command "ls -lh /opt/drift-data/"
```

### コスト

| リソース | 月額 |
|---------|------|
| e2-micro VM | $0（既存、追加コストなし） |
| Docker メモリ | ~8MB（969MB 中） |
| GCS (~1GB/月) | ~$0.02 |

## L2 Snapshot ローカル収集（旧方式）

WSL tmux で実行。Windows 再起動で途切れるため、GCE 方式を推奨。

```bash
# tmux で起動
tmux new-session -d -s drift-l2 \
  'cd ~/projects/mine/trade-analytics && python scripts/collect_drift_data.py --markets SOL --interval 5 --flush-interval 3600'
```

### Trade History の定期取得（cron）

L2 snapshot は連続収集だが、Trade history は定期バッチで十分。

```bash
# crontab -e で追加（毎時0分に1000件取得）
0 * * * * cd ~/projects/mine/trade-analytics && python scripts/fetch_drift_trades.py --symbol SOL --limit 1000 >> logs/drift_trades.log 2>&1
```

## 推奨収集設定

| パラメータ | L2 Snapshot (GCE) | L2 Snapshot (ローカル) | Trade History |
|-----------|-------------------|----------------------|---------------|
| 頻度 | 5秒間隔（常時） | 5秒間隔（常時） | 毎時1000件 |
| flush/rotation | 1時間ごと (JSONL) | 1時間ごと (parquet) | 即時 |
| 目標期間 | 無期限 | 2週間以上 | 2週間以上 |
| 推定サイズ | ~150MB/週 (JSONL) | ~200MB/週 (parquet) | ~10MB/週 |

## 分析に必要なデータ量の目安

- **スプレッド分布分析**: 3日分以上（時間帯別パターン把握）
- **逆選択の実測**: 1週間以上（CEX-DEX divergence と約定の相関）
- **MM戦略パラメータ設計**: 2週間以上（レジーム変動を含む十分なサンプル）

## ヒストリカルデータ取得 (`scripts/fetch_drift_historical.py`)

Data API の日別エンドポイントで過去データをバルク取得。

```bash
# 全種類一括取得
python scripts/fetch_drift_historical.py --all --start 2023-01-01

# 個別取得
python scripts/fetch_drift_historical.py --type candles --start 2022-11-15
python scripts/fetch_drift_historical.py --type funding --start 2023-01-01
python scripts/fetch_drift_historical.py --type trades --start 2023-01-01
```

### 取得済みデータ（2026-03-02時点）

| データ | ファイル | 行数 | サイズ | 期間 |
|--------|---------|------|--------|------|
| Candles 1h | `drift_sol_perp_candles_1h.parquet` | 28,841 | 1.5 MB | 2022-11-15 ~ |
| Funding Rates | `drift_sol_perp_funding_rates.parquet` | 27,700 | 1.1 MB | 2023-01-01 ~ |
| Trades | `drift_sol_perp_trades_historical.parquet` | 6,220,037 | 540 MB | 2023-01-01 ~ |

### 注意事項
- Trades の日別CSVは1日最大10,000件（5000件/page × 2 pages）。出来高の大きい日は全件取得不可
- Candles API の `startTs` はカーソルとして動作（その時点**以前**のデータを返す）
- スクリプトはレジューム対応（既存ファイルに含まれる日付はスキップ）
- Funding Rates の日別は `format=json`（CSV未対応の日あり）

## API 仕様メモ

- **DLOB L2 API**: `https://dlob.drift.trade/l2` — リアルタイム板情報（REST）。`includeOracle=true` で oracle price/twap/slot も返却
- **Data API trades**: `https://data.api.drift.trade/market/{symbol}/trades` — 約定履歴（直近31日、50件/page）
- **Data API trades (日別)**: `https://data.api.drift.trade/market/{symbol}/trades/{year}/{month}/{day}` — 過去データ（format=csv推奨、5000件/page × 2 pages）
- **Data API candles**: `https://data.api.drift.trade/market/{symbol}/candles/{resolution}` — OHLCV（1/5/15/60/240/D/W/M分足）
- **Data API fundingRates (日別)**: `https://data.api.drift.trade/market/{symbol}/fundingRates/{year}/{month}/{day}`
- **旧 DLOB /trades エンドポイント**: 廃止済み（404）→ Data API に移行
