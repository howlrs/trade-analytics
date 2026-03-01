# オンチェーンデータ API 仕様

## サービス比較

| 項目 | CryptoQuant | Glassnode | Dune Analytics | DefiLlama |
|------|------------|-----------|---------------|-----------|
| 認証 | Bearer Token | APIキー | X-DUNE-API-KEY | 不要（無料） |
| 無料プラン | なし | 限定（日次のみ） | あり（制限付き） | 完全無料 |
| 主な強み | 取引所・マイナーフロー | 7,500+指標 | カスタムSQL | DeFi TVL |
| 対応チェーン | BTC, ETH, XRP等 | BTC, ETH, SOL等 | 全主要EVM+非EVM | 200+チェーン |

---

## 1. CryptoQuant API

### 基本情報
| 項目 | 詳細 |
|------|------|
| ベースURL | `https://api.cryptoquant.com/v1/` |
| 認証 | `Authorization: Bearer <token>` |
| 利用資格 | Premium プラン以上 |

### 主要エンドポイント

- **Exchange Flow**: `/v1/btc/exchange-flows/inflow` (outflow, netflow)
- **Miner Flow**: `/v1/btc/miner-flows/`
- **Network Data**: `/v1/btc/network-data/`, `/v1/btc/mempool-stats/`
- **Market Data**: `/v1/btc/market-data/`

パラメータ: `window=hour|day`, `exchange=<取引所名>`, `from`, `to`

```python
import requests

headers = {"Authorization": f"Bearer {API_KEY}"}
params = {"window": "day", "from": "20240101", "to": "20240131"}
resp = requests.get(f"{BASE_URL}/btc/exchange-flows/inflow",
                    headers=headers, params=params)
```

---

## 2. Glassnode API

### 基本情報
| 項目 | 詳細 |
|------|------|
| ベースURL | `https://api.glassnode.com/v1/metrics/` |
| 認証方式1 | クエリパラメータ: `?api_key=YOUR_KEY` |
| 認証方式2 | ヘッダー: `X-Api-Key: YOUR_KEY` |

### 主要メトリクス

| メトリクス | パス |
|-----------|------|
| アクティブアドレス | `/v1/metrics/addresses/active_count` |
| 取引所残高 | `/v1/metrics/distribution/balance_exchanges` |
| 取引所ネットポジション | `/v1/metrics/distribution/exchange_net_position_change` |
| SOPR | `/v1/metrics/indicators/sopr` |
| aSOPR（調整済） | `/v1/metrics/indicators/sopr_adjusted` |
| MVRV比率 | `/v1/metrics/market/mvrv` |
| MVRV Z-Score | `/v1/metrics/market/mvrv_z_score` |
| NVT | `/v1/metrics/indicators/nvt` |
| 実現時価総額 | `/v1/metrics/market/marketcap_realized_usd` |
| 実現価格 | `/v1/metrics/market/price_realized_usd` |

### 共通パラメータ
- `a`: アセットID（`BTC`, `ETH`）
- `s` / `u`: 開始/終了タイムスタンプ（Unix）
- `i`: 粒度（`10m`, `1h`, `24h`, `1w`, `1month`）
- `f`: フォーマット（`json`, `csv`）

### ティア別制限

| プラン | 解像度 | メトリクス | API |
|--------|--------|-----------|-----|
| Standard（無料） | 24hのみ | T1のみ | 不可 |
| Advanced | 最大1h | T1+T2 | 不可 |
| Professional | 最大10m | T1+T2+T3 | アドオンで可 |
| Institutional | カスタム | 全て | 含む |

レートリミット: 600リクエスト/分（標準）

---

## 3. Dune Analytics API

### 基本情報
| 項目 | 詳細 |
|------|------|
| ベースURL | `https://api.dune.com/api/v1/` |
| 認証 | `X-DUNE-API-KEY: <key>` |

### 実行フロー（非同期）

```
1. POST /v1/query/{query_id}/execute   → execution_id取得
2. GET  /v1/execution/{id}/status      → 状態確認
3. GET  /v1/execution/{id}/results     → 結果取得
```

直接SQL実行:
```
POST /v1/sql/execute
Body: {"query_sql": "SELECT ...", "performance": "medium"}
```

### Python SDK
```python
from dune_client import DuneClient

dune = DuneClient(api_key="YOUR_API_KEY")
results = dune.run_sql(query_sql="SELECT * FROM dex.trades LIMIT 10")
print(results.get_rows())
```

### レートリミット

| プラン | Low-limit | High-limit |
|--------|-----------|-----------|
| Free | 15/分 | 40/分 |
| Plus | 70/分 | 200/分 |
| Enterprise | 350+/分 | 1,000+/分 |

---

## 4. DefiLlama API

### 基本情報
| 項目 | 詳細 |
|------|------|
| ベースURL | `https://api.llama.fi` |
| 認証 | 不要（完全無料） |

### 主要エンドポイント

#### TVL・プロトコル
| パス | 説明 |
|------|------|
| `GET /api/protocols` | 全プロトコル一覧+TVL |
| `GET /api/protocol/{protocol}` | プロトコル詳細+TVL履歴 |
| `GET /api/tvl/{protocol}` | TVL数値のみ |

#### チェーン
| パス | 説明 |
|------|------|
| `GET /api/v2/chains` | 全チェーンTVL |
| `GET /api/v2/historicalChainTvl` | 全チェーンTVL推移 |
| `GET /api/v2/historicalChainTvl/{chain}` | 特定チェーンTVL推移 |

#### ステーブルコイン
| パス | 説明 |
|------|------|
| `GET /stablecoins/stablecoins` | 全ステーブルコイン |
| `GET /stablecoins/stablecoincharts/all` | 時価総額推移 |
| `GET /stablecoins/stablecoincharts/{chain}` | チェーン別推移 |

#### DEXボリューム
| パス | 説明 |
|------|------|
| `GET /api/overview/dexs` | DEX全体ボリューム |
| `GET /api/overview/dexs/{chain}` | チェーン別 |
| `GET /api/summary/dexs/{protocol}` | プロトコル別 |

#### 手数料・収益
| パス | 説明 |
|------|------|
| `GET /api/overview/fees` | 全プロトコル手数料 |
| `GET /api/overview/fees/{chain}` | チェーン別 |
| `GET /api/summary/fees/{protocol}` | プロトコル別 |

### 料金

| プラン | 費用 |
|--------|------|
| Open | 無料 |
| Pro | $49/月 |
| API | $300/月（1,000rpm） |
| Enterprise | 要相談 |
