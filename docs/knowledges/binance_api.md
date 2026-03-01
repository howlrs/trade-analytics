# Binance API 仕様

## ベースURL

### 現物（Spot）
| URL | 説明 |
|-----|------|
| `https://api.binance.com` | メイン（推奨） |
| `https://data-api.binance.vision` | 公開マーケットデータ専用（認証不要） |

### 先物（USDⓈ-M Futures）
| URL | 説明 |
|-----|------|
| `https://fapi.binance.com` | 本番環境 |

### WebSocket
| URL | 説明 |
|-----|------|
| `wss://stream.binance.com:9443` | 現物メイン |
| `wss://fstream.binance.com` | 先物メイン |

ストリーム接続: `/ws/<streamName>` or `/stream?streams=<s1>/<s2>`

## 主要エンドポイント

### 現物

| 用途 | エンドポイント | 重み |
|------|-------------|------|
| OHLCV | `GET /api/v3/klines` | 1-10 |
| オーダーブック | `GET /api/v3/depth` | 5-250 |
| 24h Ticker | `GET /api/v3/ticker/24hr` | 1-40 |
| 最新価格 | `GET /api/v3/ticker/price` | 1-4 |
| 直近Trades | `GET /api/v3/trades` | 25 |

### 先物（USDⓈ-M）

| 用途 | エンドポイント | 重み |
|------|-------------|------|
| OHLCV | `GET /fapi/v1/klines` | 1-10 |
| 連続ローソク足 | `GET /fapi/v1/continuousKlines` | - |
| オーダーブック | `GET /fapi/v1/depth` | 2-20 |
| 24h Ticker | `GET /fapi/v1/ticker/24hr` | 1-40 |
| 最新価格 | `GET /fapi/v2/ticker/price` | 1 |
| 直近Trades | `GET /fapi/v1/trades` | 5 |
| Funding Rate履歴 | `GET /fapi/v1/fundingRate` | - |
| Funding Rate情報 | `GET /fapi/v1/fundingInfo` | - |
| Mark Price | `GET /fapi/v1/premiumIndex` | - |
| Open Interest | `GET /fapi/v1/openInterest` | 1 |
| OI統計履歴 | `GET /futures/data/openInterestHist` | - |

### OHLCV パラメータ

```
symbol    (STRING, 必須) 例: BTCUSDT
interval  (ENUM,   必須) 1s,1m,3m,5m,15m,30m,1h,2h,4h,6h,8h,12h,1d,3d,1w,1M
startTime (LONG,   任意) ミリ秒タイムスタンプ
endTime   (LONG,   任意) ミリ秒タイムスタンプ
limit     (INT,    任意) デフォルト500, 最大1000(現物)/1500(先物)
```

レスポンス（配列の配列）:
```
[Open time, Open, High, Low, Close, Volume, Close time,
 Quote asset volume, Trades count,
 Taker buy base volume, Taker buy quote volume, Unused]
```

### Funding Rate パラメータ

```
symbol    (STRING, 任意)
startTime (LONG,   任意)
endTime   (LONG,   任意)
limit     (INT,    任意) デフォルト100, 最大1000
```

レスポンス:
```json
{"symbol": "BTCUSDT", "fundingRate": "0.00010000", "fundingTime": 1577534400000, "markPrice": "94000.50"}
```

### OI統計履歴パラメータ

```
symbol (STRING, 必須)
period (ENUM,   必須) "5m","15m","30m","1h","2h","4h","6h","12h","1d"
limit  (LONG,   任意) デフォルト30, 最大500
```

制限: 最新1ヶ月のデータのみ

## レートリミット

### 現物
| 種別 | 制限 |
|------|------|
| REQUEST_WEIGHT | 6,000/分/IP |
| RAW_REQUESTS | 61,000/5分/IP |

### 先物
| 種別 | 制限 |
|------|------|
| リクエスト重み | 2,400/分/IP |
| 注文レート | 300/10秒 |

超過時: `429` → `Retry-After`ヘッダー確認、繰り返し違反 → `418` IPバン

## 認証

### ヘッダー
```
X-MBX-APIKEY: <your_api_key>
```

### HMAC-SHA256 署名
```python
import hmac, hashlib, time
from urllib.parse import urlencode

params['timestamp'] = int(time.time() * 1000)
query = urlencode(params)
signature = hmac.new(SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
url = f"{BASE_URL}{path}?{query}&signature={signature}"
```

## WebSocketストリーム名

| データ | 現物 | 先物 |
|--------|------|------|
| ローソク足 | `<symbol>@kline_<interval>` | 同左 |
| 板(差分) | `<symbol>@depth@100ms` | 同左 |
| 板(Top N) | `<symbol>@depth5/10/20` | 同左 |
| 取引 | `<symbol>@trade` | `<symbol>@aggTrade` |
| Mark Price | - | `<symbol>@markPrice` |
| 清算注文 | - | `<symbol>@forceOrder` |

接続制限: 最大1024ストリーム/接続、24時間有効

## ccxt での使い方

```python
import ccxt

# 現物
exchange = ccxt.binance()
ohlcv = exchange.fetch_ohlcv('BTC/USDT', '1h', limit=500)

# 先物
exchange = ccxt.binance({'options': {'defaultType': 'future'}})
funding = exchange.fetch_funding_rate('BTC/USDT:USDT')
history = exchange.fetch_funding_rate_history('BTC/USDT:USDT')

# オーダーブック
orderbook = exchange.fetch_order_book('BTC/USDT', limit=100)

# Ticker
ticker = exchange.fetch_ticker('BTC/USDT')
```
