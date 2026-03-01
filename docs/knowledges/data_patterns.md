# データ取得・加工パターン

## データ保存形式

### parquet

```python
import polars as pl

# 保存
df.write_parquet('data/btc_ohlcv_1h.parquet')

# 読み込み
df = pl.read_parquet('data/btc_ohlcv_1h.parquet')

# 特定カラムのみ読み込み（メモリ節約）
df = pl.read_parquet('data/btc_ohlcv_1h.parquet', columns=['close', 'volume'])

# Lazy読み込み（大規模データに最適）
lf = pl.scan_parquet('data/btc_ohlcv_1h.parquet')
result = lf.filter(pl.col('close') > 50000).collect()
```

利点: 型情報保持、圧縮による軽量化、カラムナ形式で高速読み込み

### DuckDB

```python
import duckdb

con = duckdb.connect('data/market.duckdb')

# parquetを直接クエリ
df = con.execute("""
    SELECT * FROM read_parquet('data/btc_ohlcv_1h.parquet')
    WHERE timestamp >= '2026-01-01'
""").pl()  # polars DataFrameとして取得

# 複数parquetの結合
df = con.execute("""
    SELECT a.*, b.funding_rate
    FROM read_parquet('data/btc_ohlcv_1h.parquet') a
    JOIN read_parquet('data/btc_funding.parquet') b
    ON a.timestamp = b.timestamp
""").pl()
```

## ccxt によるOHLCV一括取得

```python
import ccxt
import polars as pl
import time

def fetch_all_ohlcv(symbol, timeframe, since, exchange_id='binance'):
    exchange = getattr(ccxt, exchange_id)({'enableRateLimit': True})
    all_data = []

    while True:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
        if not ohlcv:
            break
        all_data.extend(ohlcv)
        since = ohlcv[-1][0] + 1
        time.sleep(exchange.rateLimit / 1000)

    df = pl.DataFrame(
        all_data,
        schema=["timestamp", "open", "high", "low", "close", "volume"],
        orient="row",
    )
    df = df.with_columns(
        pl.from_epoch(pl.col("timestamp"), time_unit="ms").alias("timestamp")
    )
    return df.unique(subset=["timestamp"]).sort("timestamp")

# 使用例
df = fetch_all_ohlcv('BTC/USDT:USDT', '1h', exchange.parse8601('2026-01-01T00:00:00Z'))
df.write_parquet('data/btc_usdt_1h.parquet')
```

## Bybit REST API 直接取得（推奨）

ccxtのBybit向けページネーションには問題があるため、Bybit v5 REST APIを直接使用する。

```python
import requests
import polars as pl

def fetch_bybit_ohlcv_rest(symbol_raw, interval, start_ms, end_ms):
    pair = symbol_raw.split(":")[0].replace("/", "")
    url = "https://api.bybit.com/v5/market/kline"
    all_data = []
    current_end = end_ms

    while current_end > start_ms:
        params = {
            "category": "linear", "symbol": pair,
            "interval": interval, "end": current_end, "limit": 1000,
        }
        resp = requests.get(url, params=params, timeout=30)
        items = resp.json()["result"]["list"]
        if not items:
            break
        # Bybitは降順で返すため、startTimeでフィルタ
        batch = [r for r in items if int(r[0]) >= start_ms]
        all_data.extend(batch)
        current_end = int(items[-1][0]) - 1

    # DataFrame変換
    records = [{"timestamp": int(r[0]), "open": float(r[1]), "high": float(r[2]),
                "low": float(r[3]), "close": float(r[4]), "volume": float(r[5])}
               for r in all_data]
    df = pl.DataFrame(records)
    df = df.with_columns(
        pl.from_epoch(pl.col("timestamp"), time_unit="ms").alias("timestamp")
    )
    return df.unique(subset=["timestamp"]).sort("timestamp")
```

## Funding Rate 履歴取得

```python
def fetch_funding_history(symbol, exchange_id='binance'):
    exchange = getattr(ccxt, exchange_id)({
        'enableRateLimit': True,
        'options': {'defaultType': 'swap'},
    })
    history = exchange.fetch_funding_rate_history(symbol, limit=1000)
    records = [{"timestamp": r["timestamp"], "funding_rate": r["fundingRate"]}
               for r in history]
    df = pl.DataFrame(records)
    df = df.with_columns(
        pl.from_epoch(pl.col("timestamp"), time_unit="ms").alias("timestamp")
    )
    return df.unique(subset=["timestamp"]).sort("timestamp")
```

## DefiLlama データ取得

```python
import requests
import polars as pl

BASE_URL = "https://api.llama.fi"

# TVL推移
def fetch_protocol_tvl(protocol):
    resp = requests.get(f"{BASE_URL}/api/protocol/{protocol}")
    data = resp.json()
    df = pl.DataFrame(data['tvl'])
    df = df.with_columns(
        pl.from_epoch(pl.col("date"), time_unit="s").alias("date")
    )
    return df

# DEXボリューム
def fetch_dex_volume():
    resp = requests.get(f"{BASE_URL}/api/overview/dexs")
    return resp.json()
```

## テクニカル指標の計算（polars式ベース）

```python
import polars as pl

# SMA
df = df.with_columns(
    pl.col("close").rolling_mean(window_size=20).alias("sma_20")
)

# EMA
df = df.with_columns(
    pl.col("close").ewm_mean(span=12).alias("ema_12"),
    pl.col("close").ewm_mean(span=26).alias("ema_26"),
)

# RSI
delta = pl.col("close").diff()
gain = delta.clip(lower_bound=0).rolling_mean(window_size=14)
loss = (-delta.clip(upper_bound=0)).rolling_mean(window_size=14)
df = df.with_columns(
    (100 - 100 / (1 + gain / loss)).alias("rsi_14")
)

# Bollinger Bands
df = df.with_columns(
    pl.col("close").rolling_mean(window_size=20).alias("bb_mid"),
    (pl.col("close").rolling_mean(window_size=20)
     + 2 * pl.col("close").rolling_std(window_size=20)).alias("bb_upper"),
    (pl.col("close").rolling_mean(window_size=20)
     - 2 * pl.col("close").rolling_std(window_size=20)).alias("bb_lower"),
)
```

### TA-Lib（numpy配列経由）

```python
import talib
import numpy as np

close = df["close"].to_numpy()
df = df.with_columns(
    pl.Series("sma_20", talib.SMA(close, timeperiod=20)),
    pl.Series("rsi_14", talib.RSI(close, timeperiod=14)),
)
```

## marimo notebook テンプレート

分析ノートブックの典型的な構成:

```
1. セットアップ（import、データ読み込み）
2. データ概要確認（shape, dtypes, describe, null_count）
3. 可視化（価格チャート、指標オーバーレイ）
4. 分析（仮説検証、相関分析、条件フィルタリング）
5. シグナル生成（エントリー/エグジット条件）
6. バックテスト（簡易P&L計算）
7. 結論・知見メモ
```

marimo notebookは `.py` ファイルとして保存されるため、gitでの差分管理が容易。
`marimo edit analysis_*.py` で起動する。
