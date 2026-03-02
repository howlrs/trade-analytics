"""MM-Specific Alpha & Microstructure Analysis.

Explores alpha edges relevant to market making: adverse selection,
spread capture, volume->range prediction, timing, inventory dynamics.
"""
import marimo

__generated_with = "0.20.2"
app = marimo.App(width="medium")


@app.cell
def setup():
    import marimo as mo
    import polars as pl
    import numpy as np
    from pathlib import Path
    from datetime import datetime
    from scipy import stats

    DATA_DIR = Path(__file__).parent.parent.parent / "data"
    TRAIN_END = datetime(2025, 9, 1)
    TOKENS = ["BTC", "ETH", "SOL", "SUI"]

    return DATA_DIR, TRAIN_END, TOKENS, mo, pl, np, stats


@app.cell
def helpers(pl, TRAIN_END, DATA_DIR):
    def load_ohlcv(sym: str) -> "pl.DataFrame":
        df = pl.read_parquet(DATA_DIR / f"binance_{sym.lower()}usdt_1h.parquet").sort("timestamp")
        return df.with_columns(
            pl.col("timestamp").dt.replace_time_zone(None).cast(pl.Datetime("us"))
        )

    def train_test_split(df: "pl.DataFrame") -> tuple:
        train = df.filter(pl.col("timestamp") < pl.lit(TRAIN_END).cast(pl.Datetime("us")))
        test = df.filter(pl.col("timestamp") >= pl.lit(TRAIN_END).cast(pl.Datetime("us")))
        return train, test

    return load_ohlcv, train_test_split


@app.cell
def cross_exchange_lead_lag(mo, pl, np, DATA_DIR, load_ohlcv):
    _lines = ["## 1. Cross-Exchange Lead-Lag (Binance vs Bybit)", "```"]

    for _sym in ["BTC", "ETH", "SOL"]:
        try:
            _b = load_ohlcv(_sym)
            _by = pl.read_parquet(DATA_DIR / f"bybit_{_sym.lower()}usdt_1h.parquet").sort("timestamp")
            _by = _by.with_columns(
                pl.col("timestamp").dt.replace_time_zone(None).cast(pl.Datetime("us"))
            )

            _merged = _b.select(["timestamp", "close"]).rename({"close": "close_bin"}).join(
                _by.select(["timestamp", "close"]).rename({"close": "close_byb"}),
                on="timestamp", how="inner",
            )
            _merged = _merged.with_columns([
                (pl.col("close_bin") / pl.col("close_bin").shift(1) - 1).alias("ret_bin"),
                (pl.col("close_byb") / pl.col("close_byb").shift(1) - 1).alias("ret_byb"),
            ]).drop_nulls()

            _ret_b = _merged["ret_bin"].to_numpy()
            _ret_y = _merged["ret_byb"].to_numpy()

            for _lag in [1, 2, 4]:
                _r1 = np.corrcoef(_ret_b[_lag:], _ret_y[:-_lag])[0, 1]
                _r2 = np.corrcoef(_ret_y[_lag:], _ret_b[:-_lag])[0, 1]
                _lines.append(f"  {_sym} lag={_lag}h: Bin->Byb r={_r1:.3f}, Byb->Bin r={_r2:.3f}")
        except Exception as _e:
            _lines.append(f"  {_sym}: skipped ({_e})")

    _lines.append("```")
    mo.md("\n".join(_lines))
    return


@app.cell
def adverse_selection(mo, pl, np, load_ohlcv):
    _lines = ["## 2. Adverse Selection (post-fill price movement)", "```"]

    for _sym in ["ETH", "SOL"]:
        _df = load_ohlcv(_sym)
        _df = _df.with_columns(
            (pl.col("close") / pl.col("close").shift(1) - 1).alias("ret_1h"),
        ).with_columns(
            pl.col("ret_1h").rolling_std(24).alias("rvol_24h"),
        ).drop_nulls("rvol_24h")

        for _spread_bps in [25, 50, 100]:
            _spread = _spread_bps / 10000
            _df2 = _df.with_columns([
                (pl.col("close") * (1 - _spread / 2)).alias("bid"),
                (pl.col("close") * (1 + _spread / 2)).alias("ask"),
                pl.col("low").shift(-1).alias("next_low"),
                pl.col("high").shift(-1).alias("next_high"),
                pl.col("close").shift(-1).alias("next_close"),
                pl.col("close").shift(-2).alias("close_2h"),
            ]).drop_nulls("close_2h")

            _bid_fills = _df2.filter(pl.col("next_low") <= pl.col("bid"))
            _ask_fills = _df2.filter(pl.col("next_high") >= pl.col("ask"))

            if _bid_fills.height > 10:
                _adv_1h = ((_bid_fills["next_close"] - _bid_fills["bid"]) / _bid_fills["bid"]).to_numpy()
                _adv_2h = ((_bid_fills["close_2h"] - _bid_fills["bid"]) / _bid_fills["bid"]).to_numpy()
                _lines.append(
                    f"  {_sym} {_spread_bps}bp bid: N={_bid_fills.height}, "
                    f"adverse_1h={np.mean(_adv_1h)*10000:.0f}bp, "
                    f"adverse_2h={np.mean(_adv_2h)*10000:.0f}bp"
                )
            if _ask_fills.height > 10:
                _adv_1h = ((_ask_fills["ask"] - _ask_fills["next_close"]) / _ask_fills["ask"]).to_numpy()
                _adv_2h = ((_ask_fills["ask"] - _ask_fills["close_2h"]) / _ask_fills["ask"]).to_numpy()
                _lines.append(
                    f"  {_sym} {_spread_bps}bp ask: N={_ask_fills.height}, "
                    f"adverse_1h={np.mean(_adv_1h)*10000:.0f}bp, "
                    f"adverse_2h={np.mean(_adv_2h)*10000:.0f}bp"
                )

        # Vol regime breakdown
        _df3 = _df.with_columns(
            pl.col("rvol_24h").rank("ordinal").alias("vol_rank"),
        ).with_columns(
            ((pl.col("vol_rank") - 1) * 3 / pl.col("vol_rank").max()).cast(pl.Int32).clip(0, 2).alias("vol_q"),
        )
        _spread_v = 50 / 10000
        _df3 = _df3.with_columns([
            (pl.col("close") * (1 - _spread_v / 2)).alias("bid"),
            (pl.col("close") * (1 + _spread_v / 2)).alias("ask"),
            pl.col("low").shift(-1).alias("next_low"),
            pl.col("high").shift(-1).alias("next_high"),
            pl.col("close").shift(-1).alias("next_close"),
        ]).drop_nulls("next_close")

        for _vq, _label in enumerate(["LowVol", "MidVol", "HighVol"]):
            _sub = _df3.filter(pl.col("vol_q") == _vq)
            _bid_f = _sub.filter(pl.col("next_low") <= pl.col("bid")).height
            _ask_f = _sub.filter(pl.col("next_high") >= pl.col("ask")).height
            _both = _sub.filter(
                (pl.col("next_low") <= pl.col("bid")) & (pl.col("next_high") >= pl.col("ask"))
            ).height
            _n = _sub.height
            _lines.append(
                f"  {_sym} {_label}: bid_fill={_bid_f/_n*100:.0f}%, ask_fill={_ask_f/_n*100:.0f}%, "
                f"both={_both/_n*100:.0f}%"
            )
        _lines.append("")

    _lines.append("```")
    mo.md("\n".join(_lines))
    return


@app.cell
def spread_capture(mo, pl, np, load_ohlcv):
    _lines = ["## 3. Spread Capture Rate by Holding Period", "```"]

    for _sym in ["ETH", "SOL"]:
        _df = load_ohlcv(_sym)
        _df = _df.with_columns(
            (pl.col("close") / pl.col("close").shift(1) - 1).alias("ret_1h"),
        ).with_columns(
            pl.col("ret_1h").rolling_std(24).alias("rvol_24h"),
        ).drop_nulls("rvol_24h")

        _closes = _df["close"].to_numpy()
        _lows = _df["low"].to_numpy()
        _highs = _df["high"].to_numpy()
        _n = len(_closes)
        _spread = 50 / 10000

        for _hold_h in [1, 2, 4, 8]:
            _profits = []
            for _i in range(_n - _hold_h):
                _bid = _closes[_i] * (1 - _spread / 2)
                _ask = _closes[_i] * (1 + _spread / 2)
                _bid_filled = any(_lows[_i + _j] <= _bid for _j in range(1, _hold_h + 1))
                _ask_filled = any(_highs[_i + _j] >= _ask for _j in range(1, _hold_h + 1))

                _maker_fee = 0.0001
                if _bid_filled and _ask_filled:
                    _profits.append(_spread - 2 * _maker_fee)
                elif _bid_filled:
                    _pnl = (_closes[_i + _hold_h] - _bid) / _bid - _maker_fee - 0.0004
                    _profits.append(_pnl)
                elif _ask_filled:
                    _pnl = (_ask - _closes[_i + _hold_h]) / _ask - _maker_fee - 0.0004
                    _profits.append(_pnl)

            if _profits:
                _parr = np.array(_profits)
                _sh = np.mean(_parr) / np.std(_parr) * np.sqrt(8760 / _hold_h) if np.std(_parr) > 0 else 0
                _lines.append(
                    f"  {_sym} hold={_hold_h}h: N={len(_profits)}, "
                    f"mean={np.mean(_parr)*10000:.1f}bp, "
                    f"win={(_parr > 0).mean()*100:.0f}%, sharpe={_sh:.2f}"
                )
        _lines.append("")

    _lines.append("```")
    mo.md("\n".join(_lines))
    return


@app.cell
def volume_range(mo, pl, np, load_ohlcv, train_test_split):
    _lines = ["## 4. Volume Burst -> Range Prediction", "```"]

    for _sym in ["ETH", "SOL"]:
        _df = load_ohlcv(_sym)
        _df = _df.with_columns([
            ((pl.col("high") - pl.col("low")) / pl.col("close") * 10000).alias("range_bps"),
            (pl.col("volume") / pl.col("volume").rolling_mean(24)).alias("vol_ratio"),
        ])
        _df = _df.with_columns(
            pl.col("range_bps").shift(-1).alias("next_range"),
        ).drop_nulls("next_range").drop_nulls("vol_ratio")

        _train, _test = train_test_split(_df)

        for _pname, _pdf in [("Train", _train), ("Test", _test)]:
            _cr = _pdf["range_bps"].to_numpy()
            _nr = _pdf["next_range"].to_numpy()
            _vr = _pdf["vol_ratio"].to_numpy()

            _r_rr = np.corrcoef(_cr, _nr)[0, 1]
            _r_vr = np.corrcoef(_vr, _nr)[0, 1]

            _high_vol = _pdf.filter(pl.col("vol_ratio") > 2)
            _low_vol = _pdf.filter(pl.col("vol_ratio") < 0.5)

            _lines.append(f"  {_sym} {_pname}: range->next r={_r_rr:.3f}, vol_ratio->next r={_r_vr:.3f}")
            _lines.append(
                f"    HighVol(>2x) next={_high_vol['next_range'].mean():.0f}bp (N={_high_vol.height}), "
                f"LowVol(<0.5x) next={_low_vol['next_range'].mean():.0f}bp (N={_low_vol.height})"
            )
        _lines.append("")

    _lines.append("```")
    mo.md("\n".join(_lines))
    return


@app.cell
def timing_patterns(mo, pl, load_ohlcv, train_test_split):
    _lines = ["## 5. Timing Patterns (Hour-of-Day, Weekend)", "```"]

    for _sym in ["ETH", "SOL"]:
        _df = load_ohlcv(_sym)
        _df = _df.with_columns([
            pl.col("timestamp").dt.hour().alias("hour"),
            pl.col("timestamp").dt.weekday().alias("dow"),
            ((pl.col("high") - pl.col("low")) / pl.col("close") * 10000).alias("range_bps"),
        ])

        _train, _test = train_test_split(_df)

        for _pname, _pdf in [("Train", _train), ("Test", _test)]:
            _hourly = _pdf.group_by("hour").agg([
                pl.col("range_bps").mean().alias("mean_range"),
                pl.col("volume").mean().alias("mean_vol"),
            ]).sort("hour")

            _best = _hourly.sort("mean_range", descending=True).head(3)
            _worst = _hourly.sort("mean_range").head(3)
            _lines.append(
                f"  {_sym} {_pname} Best MM hours: "
                + ", ".join(f"H{r['hour']:02d}({r['mean_range']:.0f}bp)" for r in _best.iter_rows(named=True))
            )
            _lines.append(
                f"  {_sym} {_pname} Worst MM hours: "
                + ", ".join(f"H{r['hour']:02d}({r['mean_range']:.0f}bp)" for r in _worst.iter_rows(named=True))
            )

        # Weekend effect
        for _pname, _pdf in [("Train", _train), ("Test", _test)]:
            _wd = _pdf.filter(pl.col("dow") <= 5)
            _we = _pdf.filter(pl.col("dow") > 5)
            _ratio = _we["range_bps"].mean() / _wd["range_bps"].mean()
            _lines.append(f"  {_sym} {_pname} Weekend/Weekday range ratio: {_ratio:.2f}")
        _lines.append("")

    _lines.append("```")
    mo.md("\n".join(_lines))
    return


@app.cell
def inventory_risk(mo, pl, np, load_ohlcv):
    _lines = ["## 6. Inventory Risk & Autocorrelation", "```"]

    for _sym in ["ETH", "SOL"]:
        _df = load_ohlcv(_sym)
        _df = _df.with_columns(
            (pl.col("close") / pl.col("close").shift(1) - 1).alias("ret_1h"),
        ).drop_nulls("ret_1h")

        _rets = _df["ret_1h"].to_numpy()

        # Autocorrelation
        _acs = []
        for _lag in [1, 2, 4, 8, 24]:
            _ac = np.corrcoef(_rets[_lag:], _rets[:-_lag])[0, 1]
            _acs.append(f"L{_lag}={_ac:.3f}")
        _lines.append(f"  {_sym} Return AC: {', '.join(_acs)}")

        # Continuation probability
        _signs = np.sign(_rets)
        _cont = sum(1 for _i in range(1, len(_signs)) if _signs[_i] == _signs[_i - 1] and _signs[_i] != 0)
        _total = sum(1 for _i in range(1, len(_signs)) if _signs[_i - 1] != 0)
        _lines.append(f"  {_sym} P(continuation)={_cont/_total:.3f}")

        # Inventory simulation
        _closes = _df["close"].to_numpy()
        _raw = load_ohlcv(_sym)
        _lows = _raw["low"].to_numpy()[-len(_closes):]
        _highs = _raw["high"].to_numpy()[-len(_closes):]

        _spread = 50 / 10000
        _max_inv = 10
        _inventory = 0
        _inv_hist = []
        for _i in range(len(_closes) - 1):
            _bid = _closes[_i] * (1 - _spread / 2)
            _ask = _closes[_i] * (1 + _spread / 2)
            if _lows[_i + 1] <= _bid and _inventory < _max_inv:
                _inventory += 1
            if _highs[_i + 1] >= _ask and _inventory > -_max_inv:
                _inventory -= 1
            _inv_hist.append(abs(_inventory))

        _inv_arr = np.array(_inv_hist)
        _lines.append(
            f"  {_sym} Inventory: mean_abs={np.mean(_inv_arr):.2f}, "
            f"P(>=5)={(_inv_arr >= 5).mean():.1%}, max={np.max(_inv_arr)}"
        )

    _lines.append("```")
    mo.md("\n".join(_lines))
    return


@app.cell
def vol_adaptive(mo, pl, np, load_ohlcv, train_test_split):
    _lines = ["## 7. Vol-Adaptive vs Fixed Spread (Train/Test)", "```"]

    for _sym in ["ETH", "SOL"]:
        _df = load_ohlcv(_sym)
        _df = _df.with_columns(
            (pl.col("close") / pl.col("close").shift(1) - 1).alias("ret_1h"),
        ).with_columns(
            pl.col("ret_1h").rolling_std(24).alias("rvol_24h"),
        ).drop_nulls("rvol_24h")

        _train, _test = train_test_split(_df)

        for _pname, _pdf in [("Train", _train), ("Test", _test)]:
            _closes = _pdf["close"].to_numpy()
            _lows = _pdf["low"].to_numpy()
            _highs = _pdf["high"].to_numpy()
            _rvols = _pdf["rvol_24h"].to_numpy()
            _n = len(_closes)
            _hold_h = 4

            for _strategy in ["fixed_50bp", "vol_adaptive"]:
                _profits = []
                for _i in range(_n - _hold_h):
                    if _strategy == "fixed_50bp":
                        _half_spread = 25 / 10000
                    else:
                        _half_spread = max(10 / 10000, 1.5 * _rvols[_i] * np.sqrt(_hold_h))

                    _bid = _closes[_i] * (1 - _half_spread)
                    _ask = _closes[_i] * (1 + _half_spread)

                    _bid_filled = any(_lows[_i + _j] <= _bid for _j in range(1, _hold_h + 1))
                    _ask_filled = any(_highs[_i + _j] >= _ask for _j in range(1, _hold_h + 1))

                    _maker_fee = 0.0001
                    if _bid_filled and _ask_filled:
                        _profits.append(2 * _half_spread - 2 * _maker_fee)
                    elif _bid_filled:
                        _pnl = (_closes[_i + _hold_h] - _bid) / _bid - _maker_fee - 0.0004
                        _profits.append(_pnl)
                    elif _ask_filled:
                        _pnl = (_ask - _closes[_i + _hold_h]) / _ask - _maker_fee - 0.0004
                        _profits.append(_pnl)

                if _profits:
                    _parr = np.array(_profits)
                    _sh = np.mean(_parr) / np.std(_parr) * np.sqrt(8760 / _hold_h) if np.std(_parr) > 0 else 0
                    _lines.append(
                        f"  {_sym} {_pname} {_strategy}: N={len(_profits)}, "
                        f"mean={np.mean(_parr)*10000:.1f}bp, "
                        f"win={(_parr > 0).mean()*100:.0f}%, sharpe={_sh:.2f}"
                    )
        _lines.append("")

    _lines.append("```")
    mo.md("\n".join(_lines))
    return


if __name__ == "__main__":
    app.run()
