# データ取得・加工パターン

## データ保存形式

### parquet

```python
import pandas as pd

# 保存
df.to_parquet('data/btc_ohlcv_1h.parquet', engine='pyarrow')

# 読み込み
df = pd.read_parquet('data/btc_ohlcv_1h.parquet')

# 特定カラムのみ読み込み（メモリ節約）
df = pd.read_parquet('data/btc_ohlcv_1h.parquet', columns=['close', 'volume'])
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
""").fetchdf()

# 複数parquetの結合
df = con.execute("""
    SELECT a.*, b.funding_rate
    FROM read_parquet('data/btc_ohlcv_1h.parquet') a
    JOIN read_parquet('data/btc_funding.parquet') b
    ON a.timestamp = b.timestamp
""").fetchdf()
```

## ccxt によるOHLCV一括取得

```python
import ccxt
import pandas as pd
import time

def fetch_all_ohlcv(symbol, timeframe, since, exchange_id='binance'):
    exchange = getattr(ccxt, exchange_id)()
    all_data = []

    while True:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
        if not ohlcv:
            break
        all_data.extend(ohlcv)
        since = ohlcv[-1][0] + 1  # 次の開始時刻
        time.sleep(exchange.rateLimit / 1000)

    df = pd.DataFrame(all_data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df.set_index('timestamp', inplace=True)
    return df

# 使用例
df = fetch_all_ohlcv('BTC/USDT', '1h', exchange.parse8601('2026-01-01T00:00:00Z'))
df.to_parquet('data/btc_usdt_1h.parquet')
```

## Funding Rate 履歴取得

```python
def fetch_funding_history(symbol, exchange_id='binance'):
    exchange = getattr(ccxt, exchange_id)({'options': {'defaultType': 'future'}})
    history = exchange.fetch_funding_rate_history(
        symbol,
        params={"paginate": True, "paginationCalls": 10}
    )
    df = pd.DataFrame(history)
    df['datetime'] = pd.to_datetime(df['datetime'])
    return df[['datetime', 'symbol', 'fundingRate', 'markPrice']]

df_fr = fetch_funding_history('BTC/USDT:USDT')
df_fr.to_parquet('data/btc_funding_rate.parquet')
```

## DefiLlama データ取得

```python
import requests
import pandas as pd

BASE_URL = "https://api.llama.fi"

# TVL推移
def fetch_protocol_tvl(protocol):
    resp = requests.get(f"{BASE_URL}/api/protocol/{protocol}")
    data = resp.json()
    df = pd.DataFrame(data['tvl'])
    df['date'] = pd.to_datetime(df['date'], unit='s')
    return df

# ステーブルコイン時価総額推移
def fetch_stablecoin_mcap():
    resp = requests.get(f"{BASE_URL}/stablecoins/stablecoincharts/all")
    data = resp.json()
    df = pd.DataFrame(data)
    df['date'] = pd.to_datetime(df['date'], unit='s')
    return df

# DEXボリューム
def fetch_dex_volume():
    resp = requests.get(f"{BASE_URL}/api/overview/dexs")
    return resp.json()
```

## テクニカル指標の計算

### pandas-ta

```python
import pandas_ta as ta

# 一括計算（Strategy）
strategy = ta.Strategy(
    name="basic",
    ta=[
        {"kind": "sma", "length": 20},
        {"kind": "ema", "length": 12},
        {"kind": "ema", "length": 26},
        {"kind": "rsi", "length": 14},
        {"kind": "macd", "fast": 12, "slow": 26, "signal": 9},
        {"kind": "bbands", "length": 20, "std": 2},
        {"kind": "adx", "length": 14},
        {"kind": "obv"},
        {"kind": "vwap"},
    ]
)
df.ta.strategy(strategy)
```

### TA-Lib

```python
import talib

df['sma_20'] = talib.SMA(df['close'], timeperiod=20)
df['rsi_14'] = talib.RSI(df['close'], timeperiod=14)
macd, signal, hist = talib.MACD(df['close'], fastperiod=12, slowperiod=26, signalperiod=9)

# ローソク足パターン検出
df['doji'] = talib.CDLDOJI(df['open'], df['high'], df['low'], df['close'])
df['hammer'] = talib.CDLHAMMER(df['open'], df['high'], df['low'], df['close'])
df['engulfing'] = talib.CDLENGULFING(df['open'], df['high'], df['low'], df['close'])
```

## Jupyter notebook テンプレート

分析ノートブックの典型的な構成:

```
1. セットアップ（import、データ読み込み）
2. データ概要確認（shape, dtypes, describe, 欠損値）
3. 可視化（価格チャート、指標オーバーレイ）
4. 分析（仮説検証、相関分析、条件フィルタリング）
5. シグナル生成（エントリー/エグジット条件）
6. バックテスト（簡易P&L計算）
7. 結論・知見メモ
```
