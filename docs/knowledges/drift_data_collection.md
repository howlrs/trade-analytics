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

### Trade History (`scripts/fetch_drift_trades.py`)

Drift Data API (`data.api.drift.trade`) から約定履歴を取得。

```bash
# 直近1000件取得（ページネーション自動）
python scripts/fetch_drift_trades.py --symbol SOL --limit 1000
```

**出力**: `data/drift_solusdc_trades.parquet`
**スキーマ**: timestamp, price, size, quote_filled, taker_fee, maker_rebate, side, market, action_explanation, tx_sig
**API制限**: 直近31日分、1リクエスト最大50件（自動ページネーション対応）

## 長期収集の実行方法

### tmux を使用（推奨）

```bash
# L2 snapshot 連続収集
tmux new-session -d -s drift-l2 \
  'cd ~/projects/mine/trade-analytics && python scripts/collect_drift_data.py --markets SOL --interval 5 --flush-interval 3600'

# 確認
tmux ls
tmux attach -t drift-l2  # Ctrl+B D でデタッチ
```

### nohup を使用

```bash
nohup python scripts/collect_drift_data.py --markets SOL --interval 5 --flush-interval 3600 \
  > logs/drift_l2.log 2>&1 &
echo $! > logs/drift_l2.pid
```

### Trade History の定期取得（cron）

L2 snapshot は連続収集だが、Trade history は定期バッチで十分。

```bash
# crontab -e で追加（毎時0分に1000件取得）
0 * * * * cd ~/projects/mine/trade-analytics && python scripts/fetch_drift_trades.py --symbol SOL --limit 1000 >> logs/drift_trades.log 2>&1
```

## 推奨収集設定

| パラメータ | L2 Snapshot | Trade History |
|-----------|-------------|---------------|
| 頻度 | 5秒間隔（常時） | 毎時1000件 |
| flush | 1時間ごと | 即時（スクリプト完了時） |
| 目標期間 | 2週間以上 | 2週間以上 |
| 推定サイズ | ~200MB/週 | ~10MB/週 |

## 事前準備

```bash
mkdir -p logs
```

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

- **DLOB L2 API**: `https://dlob.drift.trade/l2` — リアルタイム板情報（REST）
- **Data API trades**: `https://data.api.drift.trade/market/{symbol}/trades` — 約定履歴（直近31日、50件/page）
- **Data API trades (日別)**: `https://data.api.drift.trade/market/{symbol}/trades/{year}/{month}/{day}` — 過去データ（format=csv推奨、5000件/page × 2 pages）
- **Data API candles**: `https://data.api.drift.trade/market/{symbol}/candles/{resolution}` — OHLCV（1/5/15/60/240/D/W/M分足）
- **Data API fundingRates (日別)**: `https://data.api.drift.trade/market/{symbol}/fundingRates/{year}/{month}/{day}`
- **旧 DLOB /trades エンドポイント**: 廃止済み（404）→ Data API に移行
