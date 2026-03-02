"""Alpha Deep Dive: Event-driven, Mean Reversion, XS Composite.

Run with: python3 analyses/20260303_alpha_deepdive/analysis_alpha_deepdive.py
"""
import polars as pl
import numpy as np
from pathlib import Path
from datetime import datetime
from scipy import stats

DATA_DIR = Path("data")
TRAIN_END = datetime(2025, 9, 1)
TOKENS = ["BTC", "ETH", "SOL", "SUI"]


def load_ohlcv(sym: str) -> pl.DataFrame:
    df = pl.read_parquet(DATA_DIR / f"binance_{sym.lower()}usdt_1h.parquet").sort("timestamp")
    return df.with_columns(
        pl.col("timestamp").dt.replace_time_zone(None).cast(pl.Datetime("us"))
    )


def train_test_split(df: pl.DataFrame) -> tuple:
    train = df.filter(pl.col("timestamp") < pl.lit(TRAIN_END).cast(pl.Datetime("us")))
    test = df.filter(pl.col("timestamp") >= pl.lit(TRAIN_END).cast(pl.Datetime("us")))
    return train, test


# ============================================================
# 1. EVENT-DRIVEN: Large move reversal
# ============================================================
def analyze_event_reversal():
    print("=" * 80)
    print("1. EVENT-DRIVEN ALPHA: Large move reversal")
    print("=" * 80)

    for sym in TOKENS:
        df = load_ohlcv(sym)
        df = df.with_columns(
            (pl.col("close") / pl.col("close").shift(1) - 1).alias("ret_1h"),
        )
        df = df.with_columns([
            pl.col("ret_1h").rolling_std(168).alias("rvol_7d"),
            pl.col("ret_1h").rolling_mean(168).alias("rmean_7d"),
        ])

        for h in [1, 2, 4, 8]:
            df = df.with_columns(
                (pl.col("close").shift(-h) / pl.col("close") - 1).alias(f"fwd_{h}h")
            )

        df = df.drop_nulls("rvol_7d").drop_nulls("fwd_8h")
        df = df.with_columns(
            ((pl.col("ret_1h") - pl.col("rmean_7d")) / pl.col("rvol_7d")).alias("ret_zscore")
        )

        train, test = train_test_split(df)

        for threshold, label in [(2.0, "2sigma"), (3.0, "3sigma")]:
            for pname, pdf in [("Train", train), ("Test", test)]:
                crash = pdf.filter(pl.col("ret_zscore") < -threshold)
                pump = pdf.filter(pl.col("ret_zscore") > threshold)

                if crash.height < 5 or pump.height < 5:
                    continue

                for h in [4]:
                    col = f"fwd_{h}h"
                    c_mean = crash[col].mean() * 10000
                    p_mean = pump[col].mean() * 10000
                    c_t, c_p = stats.ttest_1samp(crash[col].drop_nulls().to_numpy(), 0)
                    p_t, p_p = stats.ttest_1samp(pump[col].drop_nulls().to_numpy(), 0)

                    c_sig = "**" if c_p < 0.05 else "*" if c_p < 0.10 else ""
                    p_sig = "**" if p_p < 0.05 else "*" if p_p < 0.10 else ""
                    print(
                        f"  {sym} {label} {pname}: "
                        f"Crash(N={crash.height}) fwd_4h={c_mean:>6.0f}bp{c_sig}  "
                        f"Pump(N={pump.height}) fwd_4h={p_mean:>6.0f}bp{p_sig}"
                    )


# ============================================================
# 2. INTRADAY MEAN REVERSION
# ============================================================
def analyze_mean_reversion():
    print(f"\n{'=' * 80}")
    print("2. INTRADAY MEAN REVERSION (MA deviation)")
    print("=" * 80)

    for sym in ["ETH", "SOL"]:
        df = load_ohlcv(sym)
        df = df.with_columns(
            (pl.col("close") / pl.col("close").shift(1) - 1).alias("ret_1h"),
        )
        df = df.with_columns([
            pl.col("close").rolling_mean(24).alias("ma24"),
        ])
        df = df.with_columns(
            ((pl.col("close") - pl.col("ma24")) / pl.col("ma24")).alias("dev")
        )
        for h in [1, 4]:
            df = df.with_columns(
                (pl.col("close").shift(-h) / pl.col("close") - 1).alias(f"fwd_{h}h")
            )
        df = df.drop_nulls("dev").drop_nulls("fwd_4h")

        train, test = train_test_split(df)

        for pname, pdf in [("Train", train), ("Test", test)]:
            v = pdf.select(["dev", "fwd_1h", "fwd_4h"]).drop_nulls()
            v = v.with_columns(
                pl.col("dev").rank("ordinal").alias("rank")
            ).with_columns(
                ((pl.col("rank") - 1) * 5 / v.height).cast(pl.Int32).clip(0, 4).alias("q")
            )
            q_means = v.group_by("q").agg([
                pl.col("fwd_1h").mean(),
                pl.col("fwd_4h").mean(),
            ]).sort("q")

            if q_means.height >= 5:
                for h in [1, 4]:
                    col = f"fwd_{h}h"
                    q1 = q_means[col][0] * 10000
                    q5 = q_means[col][4] * 10000
                    print(f"  {sym} {pname} dev->fwd_{h}h: Q1={q1:>5.0f}bp Q5={q5:>5.0f}bp Rev={q1-q5:>5.0f}bp")
        print()


# ============================================================
# 3. XS COMPOSITE WALK-FORWARD
# ============================================================
def analyze_xs_composite():
    print(f"\n{'=' * 80}")
    print("3. XS COMPOSITE (Basis+FR) WALK-FORWARD")
    print("=" * 80)

    panel = {}
    for sym in TOKENS:
        d = load_ohlcv(sym)
        d = d.group_by_dynamic("timestamp", every="1d").agg(
            pl.col("close").last(),
        ).sort("timestamp").with_columns(pl.col("timestamp").dt.date().alias("date"))

        for h in [1, 3]:
            d = d.with_columns(
                (pl.col("close").shift(-h) / pl.col("close") - 1).alias(f"fwd_{h}d")
            )

        # Basis
        b = pl.read_parquet(DATA_DIR / f"binance_{sym.lower()}usdt_basis_1h.parquet").sort("timestamp")
        b = b.with_columns(
            pl.col("timestamp").dt.replace_time_zone(None).cast(pl.Datetime("us")).dt.date().alias("date")
        )
        b_daily = b.group_by("date").agg(pl.col("basis_rate").last().alias("basis_rate"))
        d = d.join(b_daily, on="date", how="left")

        # FR
        f = pl.read_parquet(DATA_DIR / f"bybit_{sym.lower()}usdt_funding_rate.parquet").sort("timestamp")
        f = f.with_columns(
            pl.col("timestamp").dt.replace_time_zone(None).cast(pl.Datetime("us")).dt.date().alias("date")
        )
        f_daily = f.group_by("date").agg(pl.col("funding_rate").last().alias("fr"))
        d = d.join(f_daily, on="date", how="left")

        panel[sym] = d

    # Walk-Forward
    all_dates = sorted(set(panel["BTC"]["date"].to_list()))
    n_dates = len(all_dates)
    wf_results = []
    test_window = 30
    min_train = 60

    i = min_train
    while i + test_window <= n_dates:
        test_dates = set(all_dates[i:i + test_window])

        for dt in sorted(test_dates):
            signals, fwds = {}, {}
            for sym in TOKENS:
                row = panel[sym].filter(pl.col("date") == dt)
                if row.height == 0:
                    continue
                b_val = row["basis_rate"][0]
                f_val = row["fr"][0]
                fwd_val = row["fwd_1d"][0]
                if any(v is None for v in [b_val, f_val, fwd_val]):
                    continue
                if any(np.isnan(v) for v in [b_val, f_val, fwd_val]):
                    continue
                signals[sym] = {"basis": b_val, "fr": f_val}
                fwds[sym] = fwd_val

            if len(signals) < 3:
                continue

            combined = {}
            for sym in signals:
                score = 0
                for feat in ["basis", "fr"]:
                    vals = [signals[s][feat] for s in signals]
                    m, s_d = np.mean(vals), np.std(vals)
                    z = (signals[sym][feat] - m) / s_d if s_d > 0 else 0
                    score += 0.5 * z
                combined[sym] = score

            ranked = sorted(combined.items(), key=lambda x: x[1])
            ls_ret = fwds[ranked[0][0]] - fwds[ranked[-1][0]]

            wf_results.append({"date": dt, "fold": all_dates[i], "ls_return": ls_ret})

        i += test_window

    wf_df = pl.DataFrame(wf_results)
    print(f"\n  Walk-Forward: {wf_df.height} days, {(n_dates - min_train) // test_window} folds")

    folds = wf_df.group_by("fold").agg(
        pl.col("ls_return").mean().alias("mean_ret"),
        pl.col("ls_return").count().alias("n"),
    ).sort("fold")

    for row in folds.iter_rows(named=True):
        ret = row["mean_ret"] * 10000
        print(f"  Fold {str(row['fold'])}: {ret:>8.0f}bp (N={row['n']})")

    arr = wf_df["ls_return"].drop_nulls().to_numpy()
    t, p = stats.ttest_1samp(arr, 0)
    sharpe = (np.mean(arr) / np.std(arr)) * np.sqrt(365) if np.std(arr) > 0 else 0
    win = (arr > 0).mean()
    print(f"\n  Overall: mean={np.mean(arr)*10000:.0f}bp/day, Sharpe={sharpe:.2f}, "
          f"t={t:.2f}, p={p:.4f}, win={win:.0%}")
    print(f"  Net (after 4bp/day cost): {np.mean(arr)*10000-4:.0f}bp/day")


if __name__ == "__main__":
    analyze_event_reversal()
    analyze_mean_reversion()
    analyze_xs_composite()
