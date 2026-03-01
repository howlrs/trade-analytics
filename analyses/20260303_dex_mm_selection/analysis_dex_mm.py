"""DEX MM Venue Selection: Drift vs Hyperliquid vs dYdX vs CEX.

Analyses:
1. Fee structure comparison
2. Live order book analysis (quoted spread, depth)
3. CEX vs DEX spread comparison
4. MM economics model on Drift
5. Simulated PnL under different scenarios

Run with: marimo edit analyses/20260303_dex_mm_selection/analysis_dex_mm.py
"""

import marimo

__generated_with = "0.20.2"
app = marimo.App(width="medium")


@app.cell
def setup():
    from pathlib import Path
    from datetime import datetime

    import marimo as mo
    import matplotlib as mpl
    import matplotlib.pyplot as plt
    import matplotlib.font_manager as fm
    import numpy as np
    import polars as pl
    import requests

    fm.fontManager.addfont("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
    mpl.rcParams["font.family"] = "Noto Sans CJK JP"
    mpl.rcParams["axes.unicode_minus"] = False

    DATA_DIR = Path(__file__).parent.parent.parent / "data"
    TRAIN_END = datetime(2026, 2, 1)
    return DATA_DIR, TRAIN_END, datetime, mo, np, pl, plt, requests


@app.cell
def fee_comparison(mo):
    mo.vstack(
        [
            mo.md("## 1. DEX手数料構造比較"),
            mo.md(
                """
| DEX | チェーン | Maker Fee | Taker Fee | 条件 |
|-----|---------|-----------|-----------|------|
| **Drift** | Solana | **-0.25bp** (rebate) | 3bp | 即時、volume要件なし |
| Hyperliquid | 独自L1 | 1.5bp (VIP0) | 4.5bp | VIP4($500M)で0bp |
| dYdX v4 | Cosmos | 1bp (Tier1) | 5bp | Tier4($25M)で0bp, Tier7($200M)で-1.1bp |
| Binance (参考) | — | 2bp (VIP0) | 5bp | VIP5で0bp |

**Drift が唯一、即時で maker rebate を提供。** Volume要件なし。
"""
            ),
        ]
    )
    return


@app.cell
def live_orderbook(mo, np, plt, requests):
    _fig, _axes = plt.subplots(1, 3, figsize=(15, 5))
    _venues = {}

    # Drift
    for _idx, _mkt in enumerate(["SOL-PERP", "ETH-PERP", "BTC-PERP"]):
        _sym = _mkt.split("-")[0]
        try:
            _resp = requests.get(
                f"https://dlob.drift.trade/l2?marketName={_mkt}&depth=20", timeout=10
            )
            _data = _resp.json()
            _bids = _data.get("bids", [])
            _asks = _data.get("asks", [])
            if _bids and _asks:
                _bb = float(_bids[0]["price"]) / 1e6
                _ba = float(_asks[0]["price"]) / 1e6
                _mid = (_bb + _ba) / 2
                _drift_spread = (_ba - _bb) / _mid * 10000
                _venues[(_sym, "Drift")] = _drift_spread
        except Exception:
            pass

    # Hyperliquid
    for _coin in ["SOL", "ETH", "BTC"]:
        try:
            _resp = requests.post(
                "https://api.hyperliquid.xyz/info",
                json={"type": "l2Book", "coin": _coin},
                timeout=10,
            )
            _data = _resp.json()
            _levels = _data.get("levels", [[], []])
            if _levels[0] and _levels[1]:
                _bb = float(_levels[0][0]["px"])
                _ba = float(_levels[1][0]["px"])
                _mid = (_bb + _ba) / 2
                _hl_spread = (_ba - _bb) / _mid * 10000
                _venues[(_coin, "Hyperliquid")] = _hl_spread
        except Exception:
            pass

    # Binance
    for _sym_pair in [("SOL", "SOLUSDT"), ("ETH", "ETHUSDT"), ("BTC", "BTCUSDT")]:
        _sym, _pair = _sym_pair
        try:
            _resp = requests.get(
                f"https://fapi.binance.com/fapi/v1/depth?symbol={_pair}&limit=5",
                timeout=10,
            )
            _data = _resp.json()
            _bids = _data.get("bids", [])
            _asks = _data.get("asks", [])
            if _bids and _asks:
                _bb = float(_bids[0][0])
                _ba = float(_asks[0][0])
                _mid = (_bb + _ba) / 2
                _bn_spread = (_ba - _bb) / _mid * 10000
                _venues[(_sym, "Binance")] = _bn_spread
        except Exception:
            pass

    # Plot
    _tokens = ["SOL", "ETH", "BTC"]
    _venue_names = ["Binance", "Hyperliquid", "Drift"]
    _colors = ["#2196F3", "#FF9800", "#4CAF50"]
    _x = np.arange(len(_tokens))
    _w = 0.25

    for _i, _vname in enumerate(_venue_names):
        _vals = [_venues.get((_t, _vname), 0) for _t in _tokens]
        _axes[0].bar(_x + _i * _w, _vals, _w, label=_vname, color=_colors[_i])

    _axes[0].set_xticks(_x + _w)
    _axes[0].set_xticklabels(_tokens)
    _axes[0].set_ylabel("Quoted Spread (bp)")
    _axes[0].set_title("Quoted Spread比較 (ライブ)")
    _axes[0].legend()
    _axes[0].set_yscale("log")

    # Drift-only detail: bid/ask levels
    try:
        _resp = requests.get(
            "https://dlob.drift.trade/l2?marketName=SOL-PERP&depth=20", timeout=10
        )
        _data = _resp.json()
        _bids = _data.get("bids", [])
        _asks = _data.get("asks", [])
        _bb = float(_bids[0]["price"]) / 1e6
        _ba = float(_asks[0]["price"]) / 1e6
        _mid = (_bb + _ba) / 2

        _bid_prices = [(float(b["price"]) / 1e6 - _mid) / _mid * 10000 for b in _bids]
        _bid_sizes = [float(b["size"]) / 1e9 for b in _bids]
        _ask_prices = [(float(a["price"]) / 1e6 - _mid) / _mid * 10000 for a in _asks]
        _ask_sizes = [float(a["size"]) / 1e9 for a in _asks]

        _axes[1].barh(_bid_prices, _bid_sizes, height=0.3, color="green", alpha=0.7, label="Bid")
        _axes[1].barh(_ask_prices, _ask_sizes, height=0.3, color="red", alpha=0.7, label="Ask")
        _axes[1].axhline(0, color="black", linewidth=0.5)
        _axes[1].set_xlabel("Size (SOL)")
        _axes[1].set_ylabel("Distance from mid (bp)")
        _axes[1].set_title("Drift SOL-PERP: Depth Profile")
        _axes[1].legend()
    except Exception:
        _axes[1].text(0.5, 0.5, "Data unavailable", ha="center", va="center")

    # Fee impact chart
    _half_spreads = [2.5, 5.0, 7.5, 10.0]
    for _vname, _fee in [("Drift", -0.25), ("Hyperliquid", 1.5), ("Binance", 2.0)]:
        _net = [2 * hs - 2 * _fee for hs in _half_spreads]
        _axes[2].plot(_half_spreads, _net, "o-", label=f"{_vname} (fee={_fee}bp)")

    _axes[2].axhline(0, color="black", linewidth=1, linestyle="--")
    _axes[2].set_xlabel("Half Spread (bp)")
    _axes[2].set_ylabel("Net Revenue per RT (bp)")
    _axes[2].set_title("スプレッド収入 - 手数料 (理論値)")
    _axes[2].legend()
    _axes[2].grid(True, alpha=0.3)

    _fig.suptitle("DEX vs CEX: オーダーブック & 手数料比較", fontsize=14)
    _fig.tight_layout()

    mo.vstack(
        [
            mo.md("## 2. ライブオーダーブック分析"),
            mo.md(
                f"""
**Driftのスプレッドは Binance/Hyperliquid の 10-1000倍広い。**

| 通貨 | Binance | Hyperliquid | Drift | 倍率 |
|------|---------|-------------|-------|------|
| SOL | {_venues.get(('SOL','Binance'), 0):.2f}bp | {_venues.get(('SOL','Hyperliquid'), 0):.2f}bp | {_venues.get(('SOL','Drift'), 0):.2f}bp | {_venues.get(('SOL','Drift'), 0) / max(_venues.get(('SOL','Binance'), 1), 0.01):.0f}x |
| ETH | {_venues.get(('ETH','Binance'), 0):.2f}bp | {_venues.get(('ETH','Hyperliquid'), 0):.2f}bp | {_venues.get(('ETH','Drift'), 0):.2f}bp | — |
| BTC | {_venues.get(('BTC','Binance'), 0):.2f}bp | {_venues.get(('BTC','Hyperliquid'), 0):.2f}bp | {_venues.get(('BTC','Drift'), 0):.2f}bp | — |

→ Driftはほぼ全てvAMM流動性。**5bp以内にlimit orderを置けば独占的にフィルされる**。
"""
            ),
            _fig,
        ]
    )
    return


@app.cell
def drift_mm_sim(DATA_DIR, TRAIN_END, mo, np, pl, plt):
    _fig, _axes = plt.subplots(1, 2, figsize=(14, 6))
    _results = []

    for _sym in ["SOL", "ETH", "BTC"]:
        _df = pl.read_parquet(
            DATA_DIR / f"binance_{_sym.lower()}usdt_1m.parquet"
        ).sort("timestamp")
        _df = _df.with_columns(
            pl.col("timestamp").dt.replace_time_zone(None).cast(pl.Datetime("us"))
        )
        _df = _df.with_columns(
            [(pl.col("close") / pl.col("close").shift(1) - 1).alias("ret")]
        ).drop_nulls("ret")
        _test = _df.filter(
            pl.col("timestamp") >= pl.lit(TRAIN_END).cast(pl.Datetime("us"))
        )

        _closes = _test["close"].to_numpy().astype(float)
        _highs = _test["high"].to_numpy().astype(float)
        _lows = _test["low"].to_numpy().astype(float)

        # Drift/CEX volume ratio
        _vol_ratios = {"SOL": 0.2, "ETH": 0.04, "BTC": 0.05}
        _vol_ratio = _vol_ratios[_sym]

        for _capture in [0.05, 0.10, 0.20, 0.50]:
            _fill_prob = _vol_ratio * _capture

            for _half_bp in [3.0, 5.0, 7.5]:
                _half = _half_bp / 10000
                _maker_rebate = 0.00025

                _pnls = []
                for _seed in range(10):
                    _rng = np.random.default_rng(42 + _seed)
                    _cash = 0.0
                    _inv = 0.0
                    _nt = 0

                    for _i in range(1, len(_closes) - 1):
                        _mid = _closes[_i]
                        _bid = _mid * (1 - _half)
                        _ask = _mid * (1 + _half)

                        if (
                            _lows[_i + 1] <= _bid
                            and _rng.random() < _fill_prob
                            and _inv < 5
                        ):
                            _inv += 1
                            _cash -= _bid * (1 - _maker_rebate)
                            _nt += 1
                        if (
                            _highs[_i + 1] >= _ask
                            and _rng.random() < _fill_prob
                            and _inv > -5
                        ):
                            _inv -= 1
                            _cash += _ask * (1 + _maker_rebate)
                            _nt += 1

                    _final = _cash + _inv * _closes[-1]
                    _nd = len(_closes) / 1440
                    _pnls.append(_final / _closes[0] * 10000 / _nd)

                _results.append(
                    {
                        "token": _sym,
                        "capture_%": int(_capture * 100),
                        "half_spread_bp": _half_bp,
                        "pnl_mean": round(np.mean(_pnls), 1),
                        "pnl_std": round(np.std(_pnls), 1),
                        "trades_day": round(_nt / _nd),
                    }
                )

    _res_df = pl.DataFrame(_results)

    # Plot 1: SOL PnL by capture rate
    _sol = _res_df.filter(
        (pl.col("token") == "SOL") & (pl.col("half_spread_bp") == 5.0)
    )
    _captures = _sol["capture_%"].to_numpy()
    _means = _sol["pnl_mean"].to_numpy()
    _stds = _sol["pnl_std"].to_numpy()
    _axes[0].errorbar(_captures, _means, yerr=_stds, fmt="o-", capsize=5)
    _axes[0].axhline(0, color="black", linewidth=1, linestyle="--")
    _axes[0].set_xlabel("Capture Rate (%)")
    _axes[0].set_ylabel("PnL/day (bp)")
    _axes[0].set_title("Drift SOL-PERP: PnL vs Capture Rate (half=5bp)")
    _axes[0].grid(True, alpha=0.3)

    # Plot 2: All tokens at capture=10%
    _sub = _res_df.filter(pl.col("capture_%") == 10)
    _tokens = ["SOL", "ETH", "BTC"]
    _x = np.arange(3)
    _w = 0.25
    for _j, _hs in enumerate([3.0, 5.0, 7.5]):
        _vals = []
        for _t in _tokens:
            _row = _sub.filter(
                (pl.col("token") == _t) & (pl.col("half_spread_bp") == _hs)
            )
            _vals.append(_row["pnl_mean"][0] if _row.height > 0 else 0)
        _axes[1].bar(_x + _j * _w, _vals, _w, label=f"half={_hs}bp")

    _axes[1].axhline(0, color="black", linewidth=1, linestyle="--")
    _axes[1].set_xticks(_x + _w)
    _axes[1].set_xticklabels(_tokens)
    _axes[1].set_ylabel("PnL/day (bp)")
    _axes[1].set_title("Drift MM: Capture=10%, Token比較")
    _axes[1].legend()
    _axes[1].grid(True, alpha=0.3)

    _fig.suptitle("Drift MM シミュレーション (CEXデータベース)", fontsize=14)
    _fig.tight_layout()

    # Show table for key scenarios
    _key = _res_df.filter(
        (pl.col("half_spread_bp") == 5.0) & (pl.col("capture_%").is_in([5, 10, 20]))
    )

    mo.vstack(
        [
            mo.md("## 3. Drift MM シミュレーション"),
            mo.md(
                """
**手法**: CEX 1mデータを使い、Driftの出来高比率でスケーリング。
Drift/CEX出来高比: SOL=20%, ETH=4%, BTC=5%。

**注意**: シミュレーションは高varianceで、実Driftデータなしでは参考値。
"""
            ),
            mo.ui.table(_key.to_pandas()),
            _fig,
        ]
    )
    return


@app.cell
def summary(mo):
    mo.md(
        """
## 4. 最終推奨

### 第1推奨: **Drift Protocol SOL-PERP**

| 項目 | 値 |
|------|---|
| Maker Fee | **-0.25bp (rebate)** — volume要件なし |
| Quoted Spread | ~10bp (vAMM) |
| 競争 | **極めて低い** (dlobにほぼ注文なし) |
| SDK | DriftPy (Python) |
| 監査 | Trail of Bits, Neodyme |
| Latency | ~400ms (Solana slot) |

### なぜDriftか

1. **手数料**: 唯一の即時maker rebate。CEXで必要な VIP5+が不要
2. **広いスプレッド**: vAMMの10bp内側に自由にquote可能
3. **低競争**: 実limit orderがほぼ存在しない → first mover advantage
4. **リスク管理**: `src/risk.py` の動的サイジングがそのまま適用可能
5. **ヘッジ**: Binance/Bybitでの即時デルタヘッジが可能

### 次のステップ

1. **DriftPy統合** — SDK接続、testnet paper trading
2. **リアルタイムデータ蓄積** — Drift WS で trade-by-trade 2週間
3. **逆選択実測** — CEX price vs Drift fill price の比較
4. **最小構成MM** — SOL-PERP のみ、$10K capital、5bp half-spread
5. **段階的拡大** — ETH-PERP、BTC-PERP への展開

### カウンターパーティーリスク

- Solanaチェーンリスク (障害実績あり、改善傾向)
- スマートコントラクトリスク (監査済み、低〜中)
- **緩和策**: 最大デポジット$10-50K、常時CEXヘッジ、異常検出で自動停止
"""
    )
    return


if __name__ == "__main__":
    app.run()
