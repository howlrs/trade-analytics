# Coinglass API 仕様

## 基本情報

| 項目 | 詳細 |
|------|------|
| REST API | `https://open-api-v4.coinglass.com` |
| WebSocket | `wss://open-ws.coinglass.com/ws-api?cg-api-key={APIキー}` |
| 認証ヘッダー | `CG-API-KEY: <your_api_key>` |
| レスポンス形式 | JSON |

## 主要エンドポイント

### Funding Rate

| エンドポイント | 説明 |
|-------------|------|
| `GET /api/futures/fundingRate/ohlc-history` | FR OHLC履歴 |
| `GET /api/futures/fundingRate/oi-weight-ohlc-history` | OI加重FR OHLC履歴 |
| `GET /api/futures/fundingRate/exchange-list` | 取引所別FR一覧 |

### Open Interest

| エンドポイント | 説明 |
|-------------|------|
| `GET /api/futures/openInterest/ohlc-history` | OI OHLC履歴 |
| `GET /api/futures/openInterest/ohlc-aggregated-history` | 全取引所集計OI |
| `GET /api/futures/openInterest/exchange-list` | 取引所別OI |

### Liquidation

| エンドポイント | 説明 |
|-------------|------|
| `GET /api/futures/liquidation/history` | ペア別清算履歴 |
| `GET /api/futures/liquidation/aggregated-history` | コイン別清算集計 |
| `GET /api/futures/liquidation/map` | 清算マップ |
| `GET /api/futures/liquidation/heatmap/model1-3` | 清算ヒートマップ |

清算マップ必須パラメータ: `exchange`, `symbol`, `range`(`1d`/`7d`/`30d`)

### Long/Short Ratio

| エンドポイント | 説明 |
|-------------|------|
| `GET /api/futures/global-long-short-account-ratio/history` | 全体L/S比率履歴 |
| `GET /api/futures/top-long-short-account-ratio/history` | 上位アカウントベース |
| `GET /api/futures/top-long-short-position-ratio/history` | 上位ポジションベース |

### Exchange データ

| エンドポイント | 説明 |
|-------------|------|
| `GET /api/exchange/assets` | 取引所保有資産 |
| `GET /api/exchange/balance/list` | 取引所残高一覧 |
| `GET /api/exchange/balance/chart` | 残高推移チャート |

### オプション

| エンドポイント | 説明 |
|-------------|------|
| `GET /api/option/info` | 市場概要 |
| `GET /api/option/max-pain` | Max Pain価格 |
| `GET /api/option/exchange-oi-history` | 取引所別オプションOI履歴 |
| `GET /api/option/exchange-vol-history` | 取引所別オプション出来高履歴 |

## 共通パラメータ

- `symbol`: コイン名（例: `BTC`）
- `exchange`: 取引所名（例: `Binance`）
- `interval`: 時間間隔（例: `1d`, `1h`）
- `limit`: 取得件数
- `startTime` / `endTime`: 期間指定

## レートリミット（プラン別）

| プラン | リクエスト/分 | 月額 |
|--------|-------------|------|
| HOBBYIST | 30 | $29 |
| STARTUP | 80 | $79 |
| STANDARD | 300 | $299 |
| PROFESSIONAL | 1,200 | $699 |
| ENTERPRISE | 6,000 | 要問合せ |

超過時: HTTP `429`

レスポンスヘッダーで確認:
- `API-KEY-MAX-LIMIT`: 上限
- `API-KEY-USE-LIMIT`: 現在の使用数

## Python での使い方

```python
import requests

BASE_URL = "https://open-api-v4.coinglass.com"
headers = {
    "CG-API-KEY": "YOUR_API_KEY",
    "Accept": "application/json"
}

# OI取得
params = {"symbol": "BTC", "interval": "1d", "limit": 30}
resp = requests.get(f"{BASE_URL}/api/futures/openInterest/ohlc-history",
                    headers=headers, params=params)
data = resp.json()

# Funding Rate取得
params = {"symbol": "BTC", "interval": "1d", "limit": 30}
resp = requests.get(f"{BASE_URL}/api/futures/fundingRate/ohlc-history",
                    headers=headers, params=params)
```

### 非公式ライブラリ（coinglass-api）

```python
# pip install coinglass-api
from coinglass_api import CoinglassAPI

cg = CoinglassAPI(coinglass_secret="YOUR_API_KEY")
df_funding = cg.funding(ex="Binance", pair="BTC-USDT", interval="h8")
df_oi = cg.open_interest_history(symbol="BTC")
df_liq = cg.liquidation_symbol(symbol="BTC")
```

注意: 非公式ライブラリは旧API(v3以前)ベース。v4の全機能には `requests` 直接呼び出しを推奨。

## レスポンス形式

```json
{
  "code": "0",
  "msg": "success",
  "data": [...]
}
```

エラーコード: `400`パラメータ不正, `401`認証エラー, `429`レート超過, `500`サーバーエラー
