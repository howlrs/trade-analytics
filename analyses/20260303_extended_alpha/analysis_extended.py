"""Extended Alpha Exploration: FR Carry, Vol-of-Vol, Calendar, Pairs, Tails.

Covers all remaining alpha hypotheses not tested in previous analyses.
All results documented in docs/knowledges/.

Run with: python3 analyses/20260303_extended_alpha/analysis_extended.py
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
# 1. FR CARRY
# ============================================================
def analyze_fr_carry():
    print("=" * 80)
    print("1. FR CARRY (Long when FR>0, Short when FR<0)")
    print("=" * 80)

    for sym in TOKENS:
        df = load_ohlcv(sym)
        fr = pl.read_parquet(DATA_DIR / f"bybit_{sym.lower()}usdt_funding_rate.parquet").sort("timestamp")
        fr = fr.with_columns(pl.col("timestamp").dt.replace_time_zone(None).cast(pl.Datetime("us")))
        df = df.join_asof(fr.select(["timestamp", "funding_rate"]), on="timestamp", strategy="backward")
        df = df.drop_nulls("funding_rate")
        df = df.with_columns(
            (pl.col("close").shift(-8) / pl.col("close") - 1).alias("fwd_8h"),
        ).drop_nulls("fwd_8h")

        df_settle = df.filter(pl.col("timestamp").dt.hour().is_in([0, 8, 16]))
        train, test = train_test_split(df_settle)

        for pname, pdf in [("Train", train), ("Test", test)]:
            fr_vals = pdf["funding_rate"].to_numpy()
            fwd_vals = pdf["fwd_8h"].to_numpy()
            signals = np.where(fr_vals > 0, 1, -1)
            net = signals * fwd_vals + np.abs(fr_vals) - 0.0008
            t, p = stats.ttest_1samp(net, 0)
            print(f"  {sym} {pname}: net={np.mean(net)*10000:.1f}bp, "
                  f"p={p:.4f}, N={pdf.height}")


# ============================================================
# 2. VOL-OF-VOL
# ============================================================
def analyze_vol_of_vol():
    print(f"\n{'=' * 80}")
    print("2. VOL CHANGE MEAN-REVERSION")
    print("=" * 80)

    for sym in ["BTC", "ETH", "SOL"]:
        df = load_ohlcv(sym)
        df = df.with_columns(
            (pl.col("close") / pl.col("close").shift(1) - 1).alias("ret_1h"),
        ).with_columns(
            pl.col("ret_1h").rolling_std(24).alias("rvol_24h"),
        ).with_columns([
            (pl.col("rvol_24h").shift(-24) / pl.col("rvol_24h") - 1).alias("vol_chg_fwd"),
            (pl.col("rvol_24h") / pl.col("rvol_24h").shift(24) - 1).alias("vol_chg_past"),
        ]).drop_nulls("vol_chg_fwd")

        train, test = train_test_split(df)

        for pname, pdf in [("Train", train), ("Test", test)]:
            vc = pdf["vol_chg_fwd"].to_numpy()
            pvc = pdf["vol_chg_past"].to_numpy()
            valid = ~(np.isnan(pvc) | np.isnan(vc) | np.isinf(pvc) | np.isinf(vc))
            r = np.corrcoef(pvc[valid], vc[valid])[0, 1]
            print(f"  {sym} {pname}: past_vol_chg→future_vol_chg r={r:.3f}")


# ============================================================
# 3. EXTREME EVENT CLUSTERING
# ============================================================
def analyze_extreme_clustering():
    print(f"\n{'=' * 80}")
    print("3. EXTREME EVENT CLUSTERING")
    print("=" * 80)

    for sym in ["BTC", "ETH", "SOL"]:
        df = load_ohlcv(sym)
        df = df.with_columns(
            (pl.col("close") / pl.col("close").shift(1) - 1).alias("ret_1h"),
        ).with_columns(
            pl.col("ret_1h").rolling_std(168).alias("rvol_7d"),
        ).drop_nulls("rvol_7d")

        zscore = (df["ret_1h"].to_numpy() / df["rvol_7d"].to_numpy())
        abs_z = np.abs(zscore)

        for threshold in [2, 3]:
            extreme = abs_z > threshold
            p_uncond = extreme.mean()
            cond_count = sum(1 for i in range(len(extreme) - 1) if extreme[i] and extreme[i + 1])
            cond_total = sum(1 for i in range(len(extreme) - 1) if extreme[i])
            p_cond = cond_count / cond_total if cond_total > 0 else 0
            ratio = p_cond / p_uncond if p_uncond > 0 else 0
            print(f"  {sym} |z|>{threshold}: P(uncond)={p_uncond:.4f}, "
                  f"P(cond|prev)={p_cond:.3f}, ratio={ratio:.1f}x")


# ============================================================
# 4. PAIR TRADING
# ============================================================
def analyze_pairs():
    print(f"\n{'=' * 80}")
    print("4. PAIR TRADING (Cointegration)")
    print("=" * 80)

    try:
        from statsmodels.tsa.stattools import coint

        pairs = [("ETH", "BTC"), ("SOL", "BTC"), ("SOL", "ETH")]
        for sym1, sym2 in pairs:
            df1 = load_ohlcv(sym1).select(["timestamp", "close"]).rename({"close": f"close_{sym1}"})
            df2 = load_ohlcv(sym2).select(["timestamp", "close"]).rename({"close": f"close_{sym2}"})
            merged = df1.join(df2, on="timestamp", how="inner").drop_nulls()

            p1 = np.log(merged[f"close_{sym1}"].to_numpy())
            p2 = np.log(merged[f"close_{sym2}"].to_numpy())

            t_stat, p_val, _ = coint(p1, p2)
            print(f"  {sym1}/{sym2}: coint p={p_val:.4f}")
    except ImportError:
        print("  statsmodels not available, skipping")


# ============================================================
# 5. POSITION SIZING BACKTEST
# ============================================================
def analyze_position_sizing():
    print(f"\n{'=' * 80}")
    print("5. POSITION SIZING BACKTEST")
    print("=" * 80)

    from src.risk import compute_rvol, detect_regime, compute_position_size

    for sym in ["BTC", "ETH", "SOL"]:
        df = load_ohlcv(sym)
        closes = df["close"].to_numpy()
        rets = np.diff(closes) / closes[:-1]
        hours = df["timestamp"].dt.hour().to_numpy()[1:]
        dows = df["timestamp"].dt.weekday().to_numpy()[1:]
        ts = df["timestamp"].to_list()
        split_idx = next(i for i in range(len(ts)) if ts[i] >= TRAIN_END) - 1

        rvol = compute_rvol(rets, 24)
        regime = detect_regime(rvol)

        for sname, use_model in [("equal_weight", False), ("full_model", True)]:
            for period, (lo, hi) in [("Train", (168, split_idx)), ("Test", (split_idx, len(rets)))]:
                pnls = []
                for i in range(lo, hi):
                    if np.isnan(rvol[i]) or np.isnan(regime[i]):
                        continue
                    if use_model:
                        size = compute_position_size(rvol[i], int(hours[i]), bool(dows[i] > 5), int(regime[i]))
                    else:
                        size = 1.0
                    pnls.append(rets[i] * size)

                if pnls:
                    arr = np.array(pnls)
                    sh = np.mean(arr) / np.std(arr) * np.sqrt(8760) if np.std(arr) > 0 else 0
                    cum = np.cumsum(arr)
                    dd = np.max(np.maximum.accumulate(cum) - cum)
                    print(f"  {sym} {sname:15s} {period}: Sharpe={sh:>6.2f}, MaxDD={dd*10000:.0f}bp")


if __name__ == "__main__":
    analyze_fr_carry()
    analyze_vol_of_vol()
    analyze_extreme_clustering()
    analyze_pairs()
    analyze_position_sizing()
