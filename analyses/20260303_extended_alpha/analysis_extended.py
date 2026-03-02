"""Extended Alpha Exploration: FR Carry, Vol-of-Vol, Calendar, Pairs, Tails.

Covers all remaining alpha hypotheses not tested in previous analyses.
All results documented in docs/knowledges/.
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

    return DATA_DIR, TRAIN_END, TOKENS, mo, pl, np, Path, datetime, stats


@app.cell
def helpers(DATA_DIR, TRAIN_END, pl):
    def load_ohlcv(sym: str) -> pl.DataFrame:
        _df = pl.read_parquet(DATA_DIR / f"binance_{sym.lower()}usdt_1h.parquet").sort("timestamp")
        return _df.with_columns(
            pl.col("timestamp").dt.replace_time_zone(None).cast(pl.Datetime("us"))
        )

    def train_test_split(df: pl.DataFrame) -> tuple:
        _train = df.filter(pl.col("timestamp") < pl.lit(TRAIN_END).cast(pl.Datetime("us")))
        _test = df.filter(pl.col("timestamp") >= pl.lit(TRAIN_END).cast(pl.Datetime("us")))
        return _train, _test

    return load_ohlcv, train_test_split


@app.cell
def fr_carry(TOKENS, DATA_DIR, load_ohlcv, train_test_split, mo, pl, np, stats):
    _lines = ["## 1. FR CARRY (Long when FR>0, Short when FR<0)", "", "```"]

    for _sym in TOKENS:
        _df = load_ohlcv(_sym)
        _fr = pl.read_parquet(DATA_DIR / f"bybit_{_sym.lower()}usdt_funding_rate.parquet").sort("timestamp")
        _fr = _fr.with_columns(pl.col("timestamp").dt.replace_time_zone(None).cast(pl.Datetime("us")))
        _df = _df.join_asof(_fr.select(["timestamp", "funding_rate"]), on="timestamp", strategy="backward")
        _df = _df.drop_nulls("funding_rate")
        _df = _df.with_columns(
            (pl.col("close").shift(-8) / pl.col("close") - 1).alias("fwd_8h"),
        ).drop_nulls("fwd_8h")

        _df_settle = _df.filter(pl.col("timestamp").dt.hour().is_in([0, 8, 16]))
        _train, _test = train_test_split(_df_settle)

        for _pname, _pdf in [("Train", _train), ("Test", _test)]:
            _fr_vals = _pdf["funding_rate"].to_numpy()
            _fwd_vals = _pdf["fwd_8h"].to_numpy()
            _signals = np.where(_fr_vals > 0, 1, -1)
            _net = _signals * _fwd_vals + np.abs(_fr_vals) - 0.0008
            _t, _p = stats.ttest_1samp(_net, 0)
            _lines.append(
                f"  {_sym} {_pname}: net={np.mean(_net)*10000:.1f}bp, "
                f"p={_p:.4f}, N={_pdf.height}"
            )

    _lines.append("```")
    mo.md("\n".join(_lines))


@app.cell
def vol_of_vol(load_ohlcv, train_test_split, mo, pl, np):
    _lines = ["## 2. VOL CHANGE MEAN-REVERSION", "", "```"]

    for _sym in ["BTC", "ETH", "SOL"]:
        _df = load_ohlcv(_sym)
        _df = _df.with_columns(
            (pl.col("close") / pl.col("close").shift(1) - 1).alias("ret_1h"),
        ).with_columns(
            pl.col("ret_1h").rolling_std(24).alias("rvol_24h"),
        ).with_columns([
            (pl.col("rvol_24h").shift(-24) / pl.col("rvol_24h") - 1).alias("vol_chg_fwd"),
            (pl.col("rvol_24h") / pl.col("rvol_24h").shift(24) - 1).alias("vol_chg_past"),
        ]).drop_nulls("vol_chg_fwd")

        _train, _test = train_test_split(_df)

        for _pname, _pdf in [("Train", _train), ("Test", _test)]:
            _vc = _pdf["vol_chg_fwd"].to_numpy()
            _pvc = _pdf["vol_chg_past"].to_numpy()
            _valid = ~(np.isnan(_pvc) | np.isnan(_vc) | np.isinf(_pvc) | np.isinf(_vc))
            _r = np.corrcoef(_pvc[_valid], _vc[_valid])[0, 1]
            _lines.append(f"  {_sym} {_pname}: past_vol_chg->future_vol_chg r={_r:.3f}")

    _lines.append("```")
    mo.md("\n".join(_lines))


@app.cell
def extreme_clustering(load_ohlcv, mo, pl, np):
    _lines = ["## 3. EXTREME EVENT CLUSTERING", "", "```"]

    for _sym in ["BTC", "ETH", "SOL"]:
        _df = load_ohlcv(_sym)
        _df = _df.with_columns(
            (pl.col("close") / pl.col("close").shift(1) - 1).alias("ret_1h"),
        ).with_columns(
            pl.col("ret_1h").rolling_std(168).alias("rvol_7d"),
        ).drop_nulls("rvol_7d")

        _zscore = _df["ret_1h"].to_numpy() / _df["rvol_7d"].to_numpy()
        _abs_z = np.abs(_zscore)

        for _threshold in [2, 3]:
            _extreme = _abs_z > _threshold
            _p_uncond = _extreme.mean()
            _cond_count = sum(1 for _i in range(len(_extreme) - 1) if _extreme[_i] and _extreme[_i + 1])
            _cond_total = sum(1 for _i in range(len(_extreme) - 1) if _extreme[_i])
            _p_cond = _cond_count / _cond_total if _cond_total > 0 else 0
            _ratio = _p_cond / _p_uncond if _p_uncond > 0 else 0
            _lines.append(
                f"  {_sym} |z|>{_threshold}: P(uncond)={_p_uncond:.4f}, "
                f"P(cond|prev)={_p_cond:.3f}, ratio={_ratio:.1f}x"
            )

    _lines.append("```")
    mo.md("\n".join(_lines))


@app.cell
def pairs(load_ohlcv, mo, np):
    _lines = ["## 4. PAIR TRADING (Cointegration)", "", "```"]

    try:
        from statsmodels.tsa.stattools import coint

        _pairs = [("ETH", "BTC"), ("SOL", "BTC"), ("SOL", "ETH")]
        for _sym1, _sym2 in _pairs:
            _df1 = load_ohlcv(_sym1).select(["timestamp", "close"]).rename({"close": f"close_{_sym1}"})
            _df2 = load_ohlcv(_sym2).select(["timestamp", "close"]).rename({"close": f"close_{_sym2}"})
            _merged = _df1.join(_df2, on="timestamp", how="inner").drop_nulls()

            _p1 = np.log(_merged[f"close_{_sym1}"].to_numpy())
            _p2 = np.log(_merged[f"close_{_sym2}"].to_numpy())

            _t_stat, _p_val, _ = coint(_p1, _p2)
            _lines.append(f"  {_sym1}/{_sym2}: coint p={_p_val:.4f}")
    except ImportError:
        _lines.append("  statsmodels not available, skipping")

    _lines.append("```")
    mo.md("\n".join(_lines))


@app.cell
def position_sizing(load_ohlcv, train_test_split, TRAIN_END, mo, np, Path):
    import sys
    _project_root = str(Path(__file__).parent.parent.parent)
    if _project_root not in sys.path:
        sys.path.insert(0, _project_root)
    from src.risk import compute_rvol, detect_regime, compute_position_size

    _lines = ["## 5. POSITION SIZING BACKTEST", "", "```"]

    for _sym in ["BTC", "ETH", "SOL"]:
        _df = load_ohlcv(_sym)
        _closes = _df["close"].to_numpy()
        _rets = np.diff(_closes) / _closes[:-1]
        _hours = _df["timestamp"].dt.hour().to_numpy()[1:]
        _dows = _df["timestamp"].dt.weekday().to_numpy()[1:]
        _ts = _df["timestamp"].to_list()
        _split_idx = next(_i for _i in range(len(_ts)) if _ts[_i] >= TRAIN_END) - 1

        _rvol = compute_rvol(_rets, 24)
        _regime = detect_regime(_rvol)

        for _sname, _use_model in [("equal_weight", False), ("full_model", True)]:
            for _period, (_lo, _hi) in [("Train", (168, _split_idx)), ("Test", (_split_idx, len(_rets)))]:
                _pnls = []
                for _i in range(_lo, _hi):
                    if np.isnan(_rvol[_i]) or np.isnan(_regime[_i]):
                        continue
                    if _use_model:
                        _size = compute_position_size(_rvol[_i], int(_hours[_i]), bool(_dows[_i] > 5), int(_regime[_i]))
                    else:
                        _size = 1.0
                    _pnls.append(_rets[_i] * _size)

                if _pnls:
                    _arr = np.array(_pnls)
                    _sh = np.mean(_arr) / np.std(_arr) * np.sqrt(8760) if np.std(_arr) > 0 else 0
                    _cum = np.cumsum(_arr)
                    _dd = np.max(np.maximum.accumulate(_cum) - _cum)
                    _lines.append(f"  {_sym} {_sname:15s} {_period}: Sharpe={_sh:>6.2f}, MaxDD={_dd*10000:.0f}bp")

    _lines.append("```")
    mo.md("\n".join(_lines))


if __name__ == "__main__":
    app.run()
