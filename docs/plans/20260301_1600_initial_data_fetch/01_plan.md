# BTC/ETH 1年分データ取得計画

## 目的

Binance・Bybit の BTC/USDT および ETH/USDT（先物 linear perpetual）について、
約1年分（2025-03-01 〜 2026-03-01）のマーケットデータを取得・蓄積する。

## 対象データ

### Phase 1: OHLCV（ローソク足）

| # | 取引所 | ペア | 時間足 | 期間 | 推定リクエスト数 |
|---|--------|------|--------|------|----------------|
| 1 | Binance | BTCUSDT | 1h | 1年 | 約9回 |
| 2 | Binance | ETHUSDT | 1h | 1年 | 約9回 |
| 3 | Bybit | BTCUSDT | 1h | 1年 | 約9回 |
| 4 | Bybit | ETHUSDT | 1h | 1年 | 約9回 |

保存先: `data/{exchange}_{symbol}_1h.parquet`

### Phase 2: Funding Rate

| # | 取引所 | ペア | 間隔 | 推定リクエスト数 |
|---|--------|------|------|----------------|
| 5 | Binance | BTCUSDT | 8h | 約2回（limit=1000） |
| 6 | Binance | ETHUSDT | 8h | 約2回 |
| 7 | Bybit | BTCUSDT | 8h | 約6回（limit=200） |
| 8 | Bybit | ETHUSDT | 8h | 約6回 |

保存先: `data/{exchange}_{symbol}_funding_rate.parquet`

### Phase 3: Open Interest

| # | 取引所 | ペア | 間隔 | 推定リクエスト数 |
|---|--------|------|------|----------------|
| 9 | Binance | BTCUSDT | 4h | 制限あり（直近1ヶ月のみ） |
| 10 | Binance | ETHUSDT | 4h | 同上 |
| 11 | Bybit | BTCUSDT | 4h | カーソルページネーション |
| 12 | Bybit | ETHUSDT | 4h | 同上 |

保存先: `data/{exchange}_{symbol}_open_interest.parquet`

注意: Binance OI統計は直近1ヶ月のみ取得可能

## 実装計画

### Issue #1: データ取得基盤スクリプト作成
- `scripts/fetch_ohlcv.py` - ccxtベースのOHLCV取得（ページネーション対応）
- `scripts/fetch_funding_rate.py` - Funding Rate取得
- `scripts/fetch_open_interest.py` - OI取得
- 共通: レートリミット遵守、parquet保存、ログ出力

### Issue #2: BTC/ETH OHLCV取得（Binance + Bybit）
- 4ペア × 1時間足 × 1年分
- テスト: データ件数・日付範囲・欠損値チェック

### Issue #3: BTC/ETH Funding Rate取得（Binance + Bybit）
- 4ペア × 8時間毎 × 1年分
- テスト: Funding Rate値の妥当性チェック

### Issue #4: BTC/ETH Open Interest取得（Binance + Bybit）
- Binance: 直近1ヶ月のみ
- Bybit: カーソルページネーションで1年分
- テスト: OI値の妥当性チェック

## 技術的考慮事項

- ccxtの`enableRateLimit`を有効化し、自動的にレートリミットを遵守
- APIキーは環境変数（`.env`）から読み込み、マーケットデータは認証不要
- 取得データは重複排除してからparquet保存
- タイムスタンプはUTC統一

## ディレクトリ構造（完了後）

```
data/
├── binance_btcusdt_1h.parquet
├── binance_ethusdt_1h.parquet
├── bybit_btcusdt_1h.parquet
├── bybit_ethusdt_1h.parquet
├── binance_btcusdt_funding_rate.parquet
├── binance_ethusdt_funding_rate.parquet
├── bybit_btcusdt_funding_rate.parquet
├── bybit_ethusdt_funding_rate.parquet
├── binance_btcusdt_open_interest.parquet
├── binance_ethusdt_open_interest.parquet
├── bybit_btcusdt_open_interest.parquet
└── bybit_ethusdt_open_interest.parquet
```
