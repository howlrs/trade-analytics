"""Alpha Deep Dive: Event-driven, Mean Reversion, XS Composite."""
import marimo

__generated_with = "0.20.2"
app = marimo.App(width="medium")


@app.cell
def setup():
    from pathlib import Path
    from datetime import datetime

    import marimo as mo
    import numpy as np
    import polars as pl
    from scipy import stats

    DATA_DIR = Path(__file__).parent.parent.parent / "data"
    TRAIN_END = datetime(2025, 9, 1)
    TOKENS = ["BTC", "ETH", "SOL", "SUI"]

    return DATA_DIR, mo, np, pl, stats, TRAIN_END, TOKENS


@app.cell
def helpers(pl, TRAIN_END, DATA_DIR):
    def load_ohlcv(sym: str) -> pl.DataFrame:
        df = pl.read_parquet(DATA_DIR / f"binance_{sym.lower()}usdt_1h.parquet").sort("timestamp")
        return df.with_columns(
            pl.col("timestamp").dt.replace_time_zone(None).cast(pl.Datetime("us"))
        )

    def train_test_split(df: pl.DataFrame) -> tuple:
        train = df.filter(pl.col("timestamp") < pl.lit(TRAIN_END).cast(pl.Datetime("us")))
        test = df.filter(pl.col("timestamp") >= pl.lit(TRAIN_END).cast(pl.Datetime("us")))
        return train, test

    return load_ohlcv, train_test_split


@app.cell
def event_reversal(mo, pl, np, stats, TOKENS, load_ohlcv, train_test_split):
    _lines = ["## 1. EVENT-DRIVEN ALPHA: Large move reversal", ""]
    _lines.append("| Symbol | Threshold | Period | Crash N | Crash fwd_4h (bp) | Sig | Pump N | Pump fwd_4h (bp) | Sig |")
    _lines.append("|--------|-----------|--------|---------|-------------------|-----|--------|------------------|-----|")

    for _sym in TOKENS:
        _df = load_ohlcv(_sym)
        _df = _df.with_columns(
            (pl.col("close") / pl.col("close").shift(1) - 1).alias("ret_1h"),
        )
        _df = _df.with_columns([
            pl.col("ret_1h").rolling_std(168).alias("rvol_7d"),
            pl.col("ret_1h").rolling_mean(168).alias("rmean_7d"),
        ])

        for _h in [1, 2, 4, 8]:
            _df = _df.with_columns(
                (pl.col("close").shift(-_h) / pl.col("close") - 1).alias(f"fwd_{_h}h")
            )

        _df = _df.drop_nulls("rvol_7d").drop_nulls("fwd_8h")
        _df = _df.with_columns(
            ((pl.col("ret_1h") - pl.col("rmean_7d")) / pl.col("rvol_7d")).alias("ret_zscore")
        )

        _train, _test = train_test_split(_df)

        for _threshold, _label in [(2.0, "2sigma"), (3.0, "3sigma")]:
            for _pname, _pdf in [("Train", _train), ("Test", _test)]:
                _crash = _pdf.filter(pl.col("ret_zscore") < -_threshold)
                _pump = _pdf.filter(pl.col("ret_zscore") > _threshold)

                if _crash.height < 5 or _pump.height < 5:
                    continue

                _col = "fwd_4h"
                _c_mean = _crash[_col].mean() * 10000
                _p_mean = _pump[_col].mean() * 10000
                _c_t, _c_p = stats.ttest_1samp(_crash[_col].drop_nulls().to_numpy(), 0)
                _p_t, _p_p = stats.ttest_1samp(_pump[_col].drop_nulls().to_numpy(), 0)

                _c_sig = "\\*\\*" if _c_p < 0.05 else "\\*" if _c_p < 0.10 else ""
                _p_sig = "\\*\\*" if _p_p < 0.05 else "\\*" if _p_p < 0.10 else ""
                _lines.append(
                    f"| {_sym} | {_label} | {_pname} | {_crash.height} | {_c_mean:>6.0f} | {_c_sig} | {_pump.height} | {_p_mean:>6.0f} | {_p_sig} |"
                )

    mo.md("\n".join(_lines))
    return


@app.cell
def mean_reversion(mo, pl, TOKENS, load_ohlcv, train_test_split):
    _lines = ["## 2. INTRADAY MEAN REVERSION (MA deviation)", ""]

    for _sym in ["ETH", "SOL"]:
        _df = load_ohlcv(_sym)
        _df = _df.with_columns(
            (pl.col("close") / pl.col("close").shift(1) - 1).alias("ret_1h"),
        )
        _df = _df.with_columns([
            pl.col("close").rolling_mean(24).alias("ma24"),
        ])
        _df = _df.with_columns(
            ((pl.col("close") - pl.col("ma24")) / pl.col("ma24")).alias("dev")
        )
        for _h in [1, 4]:
            _df = _df.with_columns(
                (pl.col("close").shift(-_h) / pl.col("close") - 1).alias(f"fwd_{_h}h")
            )
        _df = _df.drop_nulls("dev").drop_nulls("fwd_4h")

        _train, _test = train_test_split(_df)

        _lines.append(f"### {_sym}")
        _lines.append("")
        _lines.append("| Period | Horizon | Q1 (bp) | Q5 (bp) | Reversal (bp) |")
        _lines.append("|--------|---------|---------|---------|---------------|")

        for _pname, _pdf in [("Train", _train), ("Test", _test)]:
            _v = _pdf.select(["dev", "fwd_1h", "fwd_4h"]).drop_nulls()
            _v = _v.with_columns(
                pl.col("dev").rank("ordinal").alias("rank")
            ).with_columns(
                ((pl.col("rank") - 1) * 5 / _v.height).cast(pl.Int32).clip(0, 4).alias("q")
            )
            _q_means = _v.group_by("q").agg([
                pl.col("fwd_1h").mean(),
                pl.col("fwd_4h").mean(),
            ]).sort("q")

            if _q_means.height >= 5:
                for _h in [1, 4]:
                    _col = f"fwd_{_h}h"
                    _q1 = _q_means[_col][0] * 10000
                    _q5 = _q_means[_col][4] * 10000
                    _lines.append(
                        f"| {_pname} | fwd\\_{_h}h | {_q1:>5.0f} | {_q5:>5.0f} | {_q1 - _q5:>5.0f} |"
                    )

        _lines.append("")

    mo.md("\n".join(_lines))
    return


@app.cell
def xs_composite(mo, pl, np, stats, TOKENS, load_ohlcv, DATA_DIR):
    _lines = ["## 3. XS COMPOSITE (Basis+FR) WALK-FORWARD", ""]

    _panel = {}
    for _sym in TOKENS:
        _d = load_ohlcv(_sym)
        _d = _d.group_by_dynamic("timestamp", every="1d").agg(
            pl.col("close").last(),
        ).sort("timestamp").with_columns(pl.col("timestamp").dt.date().alias("date"))

        for _h in [1, 3]:
            _d = _d.with_columns(
                (pl.col("close").shift(-_h) / pl.col("close") - 1).alias(f"fwd_{_h}d")
            )

        # Basis
        _b = pl.read_parquet(DATA_DIR / f"binance_{_sym.lower()}usdt_basis_1h.parquet").sort("timestamp")
        _b = _b.with_columns(
            pl.col("timestamp").dt.replace_time_zone(None).cast(pl.Datetime("us")).dt.date().alias("date")
        )
        _b_daily = _b.group_by("date").agg(pl.col("basis_rate").last().alias("basis_rate"))
        _d = _d.join(_b_daily, on="date", how="left")

        # FR
        _f = pl.read_parquet(DATA_DIR / f"bybit_{_sym.lower()}usdt_funding_rate.parquet").sort("timestamp")
        _f = _f.with_columns(
            pl.col("timestamp").dt.replace_time_zone(None).cast(pl.Datetime("us")).dt.date().alias("date")
        )
        _f_daily = _f.group_by("date").agg(pl.col("funding_rate").last().alias("fr"))
        _d = _d.join(_f_daily, on="date", how="left")

        _panel[_sym] = _d

    # Walk-Forward
    _all_dates = sorted(set(_panel["BTC"]["date"].to_list()))
    _n_dates = len(_all_dates)
    _wf_results = []
    _test_window = 30
    _min_train = 60

    _i = _min_train
    while _i + _test_window <= _n_dates:
        _test_dates = set(_all_dates[_i:_i + _test_window])

        for _dt in sorted(_test_dates):
            _signals, _fwds = {}, {}
            for _sym in TOKENS:
                _row = _panel[_sym].filter(pl.col("date") == _dt)
                if _row.height == 0:
                    continue
                _b_val = _row["basis_rate"][0]
                _f_val = _row["fr"][0]
                _fwd_val = _row["fwd_1d"][0]
                if any(_v is None for _v in [_b_val, _f_val, _fwd_val]):
                    continue
                if any(np.isnan(_v) for _v in [_b_val, _f_val, _fwd_val]):
                    continue
                _signals[_sym] = {"basis": _b_val, "fr": _f_val}
                _fwds[_sym] = _fwd_val

            if len(_signals) < 3:
                continue

            _combined = {}
            for _sym in _signals:
                _score = 0
                for _feat in ["basis", "fr"]:
                    _vals = [_signals[_s][_feat] for _s in _signals]
                    _m, _s_d = np.mean(_vals), np.std(_vals)
                    _z = (_signals[_sym][_feat] - _m) / _s_d if _s_d > 0 else 0
                    _score += 0.5 * _z
                _combined[_sym] = _score

            _ranked = sorted(_combined.items(), key=lambda x: x[1])
            _ls_ret = _fwds[_ranked[0][0]] - _fwds[_ranked[-1][0]]

            _wf_results.append({"date": _dt, "fold": _all_dates[_i], "ls_return": _ls_ret})

        _i += _test_window

    _wf_df = pl.DataFrame(_wf_results)
    _lines.append(f"Walk-Forward: **{_wf_df.height} days**, **{(_n_dates - _min_train) // _test_window} folds**")
    _lines.append("")

    _folds = _wf_df.group_by("fold").agg(
        pl.col("ls_return").mean().alias("mean_ret"),
        pl.col("ls_return").count().alias("n"),
    ).sort("fold")

    _lines.append("| Fold | Return (bp) | N |")
    _lines.append("|------|-------------|---|")
    for _row in _folds.iter_rows(named=True):
        _ret = _row["mean_ret"] * 10000
        _lines.append(f"| {str(_row['fold'])} | {_ret:>8.0f} | {_row['n']} |")

    _arr = _wf_df["ls_return"].drop_nulls().to_numpy()
    _t, _p = stats.ttest_1samp(_arr, 0)
    _sharpe = (np.mean(_arr) / np.std(_arr)) * np.sqrt(365) if np.std(_arr) > 0 else 0
    _win = (_arr > 0).mean()
    _lines.append("")
    _lines.append(f"**Overall**: mean={np.mean(_arr)*10000:.0f}bp/day, Sharpe={_sharpe:.2f}, "
                   f"t={_t:.2f}, p={_p:.4f}, win={_win:.0%}")
    _lines.append(f"")
    _lines.append(f"**Net (after 4bp/day cost)**: {np.mean(_arr)*10000-4:.0f}bp/day")

    mo.md("\n".join(_lines))
    return


if __name__ == "__main__":
    app.run()
