# Bybit API 仕様（v5）

## ベースURL

| 環境 | URL |
|------|-----|
| メインネット | `https://api.bybit.com` |
| メインネット（代替） | `https://api.bytick.com` |
| テストネット | `https://api-testnet.bybit.com` |

## 主要エンドポイント

### OHLCV（ローソク足）: `GET /v5/market/kline`

| パラメータ | 必須 | 説明 |
|-----------|------|------|
| `category` | 任意 | `spot`/`linear`/`inverse`（デフォルト: `linear`） |
| `symbol` | 必須 | 例: `BTCUSDT` |
| `interval` | 必須 | `1,3,5,15,30,60,120,240,360,720,D,W,M` |
| `start` | 任意 | 開始タイムスタンプ（ms） |
| `end` | 任意 | 終了タイムスタンプ（ms） |
| `limit` | 任意 | 1-1000、デフォルト200 |

レスポンス（`result.list`、**降順**）:
```
[startTime(ms), open, high, low, close, volume, turnover]
```

### Funding Rate履歴: `GET /v5/market/funding/history`

| パラメータ | 必須 | 説明 |
|-----------|------|------|
| `category` | 必須 | `linear`/`inverse` |
| `symbol` | 必須 | 例: `BTCUSDT` |
| `startTime` | 任意 | ms |
| `endTime` | 任意 | ms |
| `limit` | 任意 | 1-200、デフォルト200 |

注意: `startTime`のみ → エラー。`endTime`のみ or 両方省略 → 直近200件

レスポンス: `symbol, fundingRate, fundingRateTimestamp`

### Open Interest: `GET /v5/market/open-interest`

| パラメータ | 必須 | 説明 |
|-----------|------|------|
| `category` | 必須 | `linear`/`inverse` |
| `symbol` | 必須 | 例: `BTCUSDT` |
| `intervalTime` | 必須 | `5min,15min,30min,1h,4h,1d` |
| `startTime` | 任意 | ms |
| `endTime` | 任意 | ms |
| `limit` | 任意 | 1-200、デフォルト50 |
| `cursor` | 任意 | ページネーションカーソル |

レスポンス: `openInterest, timestamp` + `nextPageCursor`（カーソルページネーション対応）

### Ticker: `GET /v5/market/tickers`

| パラメータ | 必須 | 説明 |
|-----------|------|------|
| `category` | 必須 | `spot`/`linear`/`inverse`/`option` |
| `symbol` | 任意 | 省略時は全シンボル |

レスポンス: `lastPrice, markPrice, indexPrice, price24hPcnt, volume24h, openInterest, fundingRate, nextFundingTime, bid1Price, ask1Price`

## レートリミット

- **IP制限**: 600リクエスト/5秒/IP
- 超過時: `403`エラー、10分間のウェイト
- ヘッダーで確認: `X-Bapi-Limit`, `X-Bapi-Limit-Status`, `X-Bapi-Limit-Reset-Timestamp`

## 認証

マーケットデータ（`/v5/market/`）は**認証不要**。

プライベートエンドポイントは以下のヘッダーが必要:
```
X-BAPI-API-KEY: <apiKey>
X-BAPI-TIMESTAMP: <ms timestamp>
X-BAPI-SIGN: HMAC-SHA256(timestamp + apiKey + recvWindow + params)
X-BAPI-RECV-WINDOW: 5000
```

## ページネーション（1年分取得）

### Kline
カーソルなし。`end`を後退させてループ。

| 時間足 | 1000件 = 約 | 1年分リクエスト数 |
|--------|-----------|----------------|
| 1m | 16.7時間 | 約525回 |
| 1h | 41.7日 | 約9回 |
| 4h | 166.7日 | 約3回 |
| 1d | 2.7年 | 1回 |

### Funding Rate
カーソルなし。`endTime`後退ループ。200件/回、8h毎で1年≈1095件 → 約6回

### Open Interest
`nextPageCursor`でカーソルページネーション可能

## ccxt での使い方

```python
import ccxt

# Bybit 先物（linear perpetual）
exchange = ccxt.bybit({
    'options': {'defaultType': 'swap'},
    'enableRateLimit': True,
})

# OHLCV
ohlcv = exchange.fetch_ohlcv('BTC/USDT:USDT', '1h', limit=1000)

# Funding Rate
funding = exchange.fetch_funding_rate('BTC/USDT:USDT')
history = exchange.fetch_funding_rate_history('BTC/USDT:USDT')

# Ticker
ticker = exchange.fetch_ticker('BTC/USDT:USDT')
```
