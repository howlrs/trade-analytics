"""MM-Specific Alpha & Microstructure Analysis.

Explores alpha edges relevant to market making: adverse selection,
spread capture, volume→range prediction, timing, inventory dynamics.

Run with: python3 analyses/20260303_mm_alpha/analysis_mm_alpha.py
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
# 1. Cross-Exchange Lead-Lag
# ============================================================
def analyze_lead_lag():
    print("=" * 80)
    print("1. CROSS-EXCHANGE LEAD-LAG (Binance vs Bybit)")
    print("=" * 80)

    for sym in ["BTC", "ETH", "SOL"]:
        try:
            b = load_ohlcv(sym)
            by = pl.read_parquet(DATA_DIR / f"bybit_{sym.lower()}usdt_1h.parquet").sort("timestamp")
            by = by.with_columns(
                pl.col("timestamp").dt.replace_time_zone(None).cast(pl.Datetime("us"))
            )

            merged = b.select(["timestamp", "close"]).rename({"close": "close_bin"}).join(
                by.select(["timestamp", "close"]).rename({"close": "close_byb"}),
                on="timestamp", how="inner",
            )
            merged = merged.with_columns([
                (pl.col("close_bin") / pl.col("close_bin").shift(1) - 1).alias("ret_bin"),
                (pl.col("close_byb") / pl.col("close_byb").shift(1) - 1).alias("ret_byb"),
            ]).drop_nulls()

            ret_b = merged["ret_bin"].to_numpy()
            ret_y = merged["ret_byb"].to_numpy()

            for lag in [1, 2, 4]:
                r1 = np.corrcoef(ret_b[lag:], ret_y[:-lag])[0, 1]
                r2 = np.corrcoef(ret_y[lag:], ret_b[:-lag])[0, 1]
                print(f"  {sym} lag={lag}h: Bin→Byb r={r1:.3f}, Byb→Bin r={r2:.3f}")
        except Exception as e:
            print(f"  {sym}: skipped ({e})")
    print()


# ============================================================
# 2. Adverse Selection Analysis
# ============================================================
def analyze_adverse_selection():
    print("=" * 80)
    print("2. ADVERSE SELECTION (post-fill price movement)")
    print("=" * 80)

    for sym in ["ETH", "SOL"]:
        df = load_ohlcv(sym)
        df = df.with_columns(
            (pl.col("close") / pl.col("close").shift(1) - 1).alias("ret_1h"),
        ).with_columns(
            pl.col("ret_1h").rolling_std(24).alias("rvol_24h"),
        ).drop_nulls("rvol_24h")

        for spread_bps in [25, 50, 100]:
            spread = spread_bps / 10000
            df2 = df.with_columns([
                (pl.col("close") * (1 - spread / 2)).alias("bid"),
                (pl.col("close") * (1 + spread / 2)).alias("ask"),
                pl.col("low").shift(-1).alias("next_low"),
                pl.col("high").shift(-1).alias("next_high"),
                pl.col("close").shift(-1).alias("next_close"),
                pl.col("close").shift(-2).alias("close_2h"),
            ]).drop_nulls("close_2h")

            bid_fills = df2.filter(pl.col("next_low") <= pl.col("bid"))
            ask_fills = df2.filter(pl.col("next_high") >= pl.col("ask"))

            if bid_fills.height > 10:
                adv_1h = ((bid_fills["next_close"] - bid_fills["bid"]) / bid_fills["bid"]).to_numpy()
                adv_2h = ((bid_fills["close_2h"] - bid_fills["bid"]) / bid_fills["bid"]).to_numpy()
                print(f"  {sym} {spread_bps}bp bid: N={bid_fills.height}, "
                      f"adverse_1h={np.mean(adv_1h)*10000:.0f}bp, "
                      f"adverse_2h={np.mean(adv_2h)*10000:.0f}bp")
            if ask_fills.height > 10:
                adv_1h = ((ask_fills["ask"] - ask_fills["next_close"]) / ask_fills["ask"]).to_numpy()
                adv_2h = ((ask_fills["ask"] - ask_fills["close_2h"]) / ask_fills["ask"]).to_numpy()
                print(f"  {sym} {spread_bps}bp ask: N={ask_fills.height}, "
                      f"adverse_1h={np.mean(adv_1h)*10000:.0f}bp, "
                      f"adverse_2h={np.mean(adv_2h)*10000:.0f}bp")

        # Vol regime breakdown
        df3 = df.with_columns(
            pl.col("rvol_24h").rank("ordinal").alias("vol_rank"),
        ).with_columns(
            ((pl.col("vol_rank") - 1) * 3 / pl.col("vol_rank").max()).cast(pl.Int32).clip(0, 2).alias("vol_q"),
        )
        spread = 50 / 10000
        df3 = df3.with_columns([
            (pl.col("close") * (1 - spread / 2)).alias("bid"),
            (pl.col("close") * (1 + spread / 2)).alias("ask"),
            pl.col("low").shift(-1).alias("next_low"),
            pl.col("high").shift(-1).alias("next_high"),
            pl.col("close").shift(-1).alias("next_close"),
        ]).drop_nulls("next_close")

        for vq, label in enumerate(["LowVol", "MidVol", "HighVol"]):
            sub = df3.filter(pl.col("vol_q") == vq)
            bid_f = sub.filter(pl.col("next_low") <= pl.col("bid")).height
            ask_f = sub.filter(pl.col("next_high") >= pl.col("ask")).height
            both = sub.filter(
                (pl.col("next_low") <= pl.col("bid")) & (pl.col("next_high") >= pl.col("ask"))
            ).height
            n = sub.height
            print(f"  {sym} {label}: bid_fill={bid_f/n*100:.0f}%, ask_fill={ask_f/n*100:.0f}%, "
                  f"both={both/n*100:.0f}%")
        print()


# ============================================================
# 3. Spread Capture by Holding Period
# ============================================================
def analyze_spread_capture():
    print("=" * 80)
    print("3. SPREAD CAPTURE RATE BY HOLDING PERIOD")
    print("=" * 80)

    for sym in ["ETH", "SOL"]:
        df = load_ohlcv(sym)
        df = df.with_columns(
            (pl.col("close") / pl.col("close").shift(1) - 1).alias("ret_1h"),
        ).with_columns(
            pl.col("ret_1h").rolling_std(24).alias("rvol_24h"),
        ).drop_nulls("rvol_24h")

        closes = df["close"].to_numpy()
        lows = df["low"].to_numpy()
        highs = df["high"].to_numpy()
        n = len(closes)
        spread = 50 / 10000

        for hold_h in [1, 2, 4, 8]:
            profits = []
            for i in range(n - hold_h):
                bid = closes[i] * (1 - spread / 2)
                ask = closes[i] * (1 + spread / 2)
                bid_filled = any(lows[i + j] <= bid for j in range(1, hold_h + 1))
                ask_filled = any(highs[i + j] >= ask for j in range(1, hold_h + 1))

                maker_fee = 0.0001
                if bid_filled and ask_filled:
                    profits.append(spread - 2 * maker_fee)
                elif bid_filled:
                    pnl = (closes[i + hold_h] - bid) / bid - maker_fee - 0.0004
                    profits.append(pnl)
                elif ask_filled:
                    pnl = (ask - closes[i + hold_h]) / ask - maker_fee - 0.0004
                    profits.append(pnl)

            if profits:
                parr = np.array(profits)
                sh = np.mean(parr) / np.std(parr) * np.sqrt(8760 / hold_h) if np.std(parr) > 0 else 0
                print(f"  {sym} hold={hold_h}h: N={len(profits)}, "
                      f"mean={np.mean(parr)*10000:.1f}bp, "
                      f"win={(parr > 0).mean()*100:.0f}%, sharpe={sh:.2f}")
        print()


# ============================================================
# 4. Volume Burst → Range Prediction
# ============================================================
def analyze_volume_range():
    print("=" * 80)
    print("4. VOLUME BURST → RANGE PREDICTION")
    print("=" * 80)

    for sym in ["ETH", "SOL"]:
        df = load_ohlcv(sym)
        df = df.with_columns([
            ((pl.col("high") - pl.col("low")) / pl.col("close") * 10000).alias("range_bps"),
            (pl.col("volume") / pl.col("volume").rolling_mean(24)).alias("vol_ratio"),
        ])
        df = df.with_columns(
            pl.col("range_bps").shift(-1).alias("next_range"),
        ).drop_nulls("next_range").drop_nulls("vol_ratio")

        train, test = train_test_split(df)

        for pname, pdf in [("Train", train), ("Test", test)]:
            cr = pdf["range_bps"].to_numpy()
            nr = pdf["next_range"].to_numpy()
            vr = pdf["vol_ratio"].to_numpy()

            r_rr = np.corrcoef(cr, nr)[0, 1]
            r_vr = np.corrcoef(vr, nr)[0, 1]

            high_vol = pdf.filter(pl.col("vol_ratio") > 2)
            low_vol = pdf.filter(pl.col("vol_ratio") < 0.5)

            print(f"  {sym} {pname}: range→next r={r_rr:.3f}, vol_ratio→next r={r_vr:.3f}")
            print(f"    HighVol(>2x) next={high_vol['next_range'].mean():.0f}bp (N={high_vol.height}), "
                  f"LowVol(<0.5x) next={low_vol['next_range'].mean():.0f}bp (N={low_vol.height})")
        print()


# ============================================================
# 5. Hour-of-Day & Weekend Patterns
# ============================================================
def analyze_timing():
    print("=" * 80)
    print("5. TIMING PATTERNS (Hour-of-Day, Weekend)")
    print("=" * 80)

    for sym in ["ETH", "SOL"]:
        df = load_ohlcv(sym)
        df = df.with_columns([
            pl.col("timestamp").dt.hour().alias("hour"),
            pl.col("timestamp").dt.weekday().alias("dow"),
            ((pl.col("high") - pl.col("low")) / pl.col("close") * 10000).alias("range_bps"),
        ])

        train, test = train_test_split(df)

        for pname, pdf in [("Train", train), ("Test", test)]:
            hourly = pdf.group_by("hour").agg([
                pl.col("range_bps").mean().alias("mean_range"),
                pl.col("volume").mean().alias("mean_vol"),
            ]).sort("hour")

            best = hourly.sort("mean_range", descending=True).head(3)
            worst = hourly.sort("mean_range").head(3)
            print(f"  {sym} {pname} Best MM hours: "
                  + ", ".join(f"H{r['hour']:02d}({r['mean_range']:.0f}bp)" for r in best.iter_rows(named=True)))
            print(f"  {sym} {pname} Worst MM hours: "
                  + ", ".join(f"H{r['hour']:02d}({r['mean_range']:.0f}bp)" for r in worst.iter_rows(named=True)))

        # Weekend effect
        for pname, pdf in [("Train", train), ("Test", test)]:
            wd = pdf.filter(pl.col("dow") <= 5)
            we = pdf.filter(pl.col("dow") > 5)
            ratio = we["range_bps"].mean() / wd["range_bps"].mean()
            print(f"  {sym} {pname} Weekend/Weekday range ratio: {ratio:.2f}")
        print()


# ============================================================
# 6. Return Autocorrelation & Inventory Risk
# ============================================================
def analyze_inventory_risk():
    print("=" * 80)
    print("6. INVENTORY RISK & AUTOCORRELATION")
    print("=" * 80)

    for sym in ["ETH", "SOL"]:
        df = load_ohlcv(sym)
        df = df.with_columns(
            (pl.col("close") / pl.col("close").shift(1) - 1).alias("ret_1h"),
        ).drop_nulls("ret_1h")

        rets = df["ret_1h"].to_numpy()

        # Autocorrelation
        acs = []
        for lag in [1, 2, 4, 8, 24]:
            ac = np.corrcoef(rets[lag:], rets[:-lag])[0, 1]
            acs.append(f"L{lag}={ac:.3f}")
        print(f"  {sym} Return AC: {', '.join(acs)}")

        # Continuation probability
        signs = np.sign(rets)
        cont = sum(1 for i in range(1, len(signs)) if signs[i] == signs[i - 1] and signs[i] != 0)
        total = sum(1 for i in range(1, len(signs)) if signs[i - 1] != 0)
        print(f"  {sym} P(continuation)={cont/total:.3f}")

        # Inventory simulation
        closes = df["close"].to_numpy()
        lows = load_ohlcv(sym)["low"].to_numpy()[-len(closes):]
        highs = load_ohlcv(sym)["high"].to_numpy()[-len(closes):]

        spread = 50 / 10000
        max_inv = 10
        inventory = 0
        inv_hist = []
        for i in range(len(closes) - 1):
            bid = closes[i] * (1 - spread / 2)
            ask = closes[i] * (1 + spread / 2)
            if lows[i + 1] <= bid and inventory < max_inv:
                inventory += 1
            if highs[i + 1] >= ask and inventory > -max_inv:
                inventory -= 1
            inv_hist.append(abs(inventory))

        inv_arr = np.array(inv_hist)
        print(f"  {sym} Inventory: mean_abs={np.mean(inv_arr):.2f}, "
              f"P(>=5)={(inv_arr >= 5).mean():.1%}, max={np.max(inv_arr)}")
    print()


# ============================================================
# 7. Vol-Adaptive vs Fixed Spread (Train/Test)
# ============================================================
def analyze_vol_adaptive():
    print("=" * 80)
    print("7. VOL-ADAPTIVE vs FIXED SPREAD (Train/Test)")
    print("=" * 80)

    for sym in ["ETH", "SOL"]:
        df = load_ohlcv(sym)
        df = df.with_columns(
            (pl.col("close") / pl.col("close").shift(1) - 1).alias("ret_1h"),
        ).with_columns(
            pl.col("ret_1h").rolling_std(24).alias("rvol_24h"),
        ).drop_nulls("rvol_24h")

        train, test = train_test_split(df)

        for pname, pdf in [("Train", train), ("Test", test)]:
            closes = pdf["close"].to_numpy()
            lows = pdf["low"].to_numpy()
            highs = pdf["high"].to_numpy()
            rvols = pdf["rvol_24h"].to_numpy()
            n = len(closes)
            hold_h = 4

            for strategy in ["fixed_50bp", "vol_adaptive"]:
                profits = []
                for i in range(n - hold_h):
                    if strategy == "fixed_50bp":
                        half_spread = 25 / 10000
                    else:
                        half_spread = max(10 / 10000, 1.5 * rvols[i] * np.sqrt(hold_h))

                    bid = closes[i] * (1 - half_spread)
                    ask = closes[i] * (1 + half_spread)

                    bid_filled = any(lows[i + j] <= bid for j in range(1, hold_h + 1))
                    ask_filled = any(highs[i + j] >= ask for j in range(1, hold_h + 1))

                    maker_fee = 0.0001
                    if bid_filled and ask_filled:
                        profits.append(2 * half_spread - 2 * maker_fee)
                    elif bid_filled:
                        pnl = (closes[i + hold_h] - bid) / bid - maker_fee - 0.0004
                        profits.append(pnl)
                    elif ask_filled:
                        pnl = (ask - closes[i + hold_h]) / ask - maker_fee - 0.0004
                        profits.append(pnl)

                if profits:
                    parr = np.array(profits)
                    sh = np.mean(parr) / np.std(parr) * np.sqrt(8760 / hold_h) if np.std(parr) > 0 else 0
                    print(f"  {sym} {pname} {strategy}: N={len(profits)}, "
                          f"mean={np.mean(parr)*10000:.1f}bp, "
                          f"win={(parr > 0).mean()*100:.0f}%, sharpe={sh:.2f}")
        print()


if __name__ == "__main__":
    analyze_lead_lag()
    analyze_adverse_selection()
    analyze_spread_capture()
    analyze_volume_range()
    analyze_timing()
    analyze_inventory_risk()
    analyze_vol_adaptive()
