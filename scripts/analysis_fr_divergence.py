"""
Drift-CEX Funding Rate Divergence Arbitrage Analysis
=====================================================
Explores the STRUCTURE of FR divergence between Drift (hourly) and Bybit (8h),
and whether it creates actionable signals for MM inventory bias and directional trades.

Data:
- Drift SOL-PERP hourly funding rates + mark/oracle TWAPs
- Bybit SOLUSDT 8h funding rates
- Binance SOLUSDT 1h OHLCV (price reference)
- Drift SOL-PERP 1h candles (fill prices + volume)
"""

import polars as pl
import numpy as np
from scipy import stats

# ─────────────────────────────────────────────────────────────
# 0. Load & Prepare Data
# ─────────────────────────────────────────────────────────────
print("=" * 80)
print("DRIFT-CEX FUNDING RATE DIVERGENCE ARBITRAGE ANALYSIS")
print("=" * 80)

drift_fr = (
    pl.read_parquet("data/drift_sol_perp_funding_rates.parquet")
    .sort("timestamp")
    .with_columns(pl.col("timestamp").dt.truncate("1h").alias("ts_hour"))
)
bybit_fr = (
    pl.read_parquet("data/bybit_solusdt_funding_rate_full.parquet")
    .sort("timestamp")
    .with_columns(pl.col("timestamp").cast(pl.Datetime("ns", "UTC")).alias("timestamp"))
)
binance = (
    pl.read_parquet("data/binance_solusdt_1h_full.parquet")
    .sort("timestamp")
    .with_columns(pl.col("timestamp").cast(pl.Datetime("ns", "UTC")).alias("timestamp"))
)
drift_candles = (
    pl.read_parquet("data/drift_sol_perp_candles_1h.parquet")
    .sort("timestamp")
)

# Common date range
min_ts = max(
    drift_fr["timestamp"].min(),
    bybit_fr["timestamp"].min(),
    binance["timestamp"].min(),
    drift_candles["timestamp"].min(),
)
max_ts = min(
    drift_fr["timestamp"].max(),
    bybit_fr["timestamp"].max(),
    binance["timestamp"].max(),
    drift_candles["timestamp"].max(),
)
print(f"\nOverlapping period: {min_ts} → {max_ts}")
print(f"  Drift FR rows: {drift_fr.height}, Bybit FR rows: {bybit_fr.height}")
print(f"  Binance 1h rows: {binance.height}, Drift candles: {drift_candles.height}")

# Filter to overlap
drift_fr = drift_fr.filter(pl.col("timestamp").is_between(min_ts, max_ts))
bybit_fr = bybit_fr.filter(pl.col("timestamp").is_between(min_ts, max_ts))
binance = binance.filter(pl.col("timestamp").is_between(min_ts, max_ts))
drift_candles = drift_candles.filter(pl.col("timestamp").is_between(min_ts, max_ts))

print(f"\nAfter filtering to overlap:")
print(f"  Drift FR: {drift_fr.height}, Bybit FR: {bybit_fr.height}")
print(f"  Binance: {binance.height}, Drift candles: {drift_candles.height}")

# ─────────────────────────────────────────────────────────────
# PART 1: FR Divergence Dynamics
# ─────────────────────────────────────────────────────────────
print("\n" + "=" * 80)
print("PART 1: FUNDING RATE DIVERGENCE DYNAMICS")
print("=" * 80)

# Aggregate Drift hourly FR to 8h cumulative rate to match Bybit
# Bybit settles at 00:00, 08:00, 16:00 UTC
# Drift hourly FR is per-hour, so we sum 8 hours to get comparable magnitude
drift_8h = (
    drift_fr
    .with_columns(
        pl.col("timestamp").dt.truncate("8h").alias("ts_8h"),
    )
    .group_by("ts_8h")
    .agg(
        pl.col("funding_rate").sum().alias("drift_fr_8h_cum"),
        pl.col("funding_rate").mean().alias("drift_fr_8h_mean"),
        pl.col("funding_rate").count().alias("n_hours"),
        pl.col("oracle_price_twap").last().alias("oracle_last"),
        pl.col("mark_price_twap").last().alias("mark_last"),
    )
    .sort("ts_8h")
    .filter(pl.col("n_hours") >= 6)  # require at least 6 of 8 hours
)

# Join Drift 8h agg with Bybit FR
bybit_8h = bybit_fr.with_columns(
    pl.col("timestamp").dt.truncate("8h").alias("ts_8h")
).select("ts_8h", pl.col("funding_rate").alias("bybit_fr"))

merged = drift_8h.join(bybit_8h, on="ts_8h", how="inner").sort("ts_8h")
merged = merged.with_columns(
    (pl.col("drift_fr_8h_cum") - pl.col("bybit_fr")).alias("divergence"),
    (pl.col("drift_fr_8h_cum") / pl.col("bybit_fr")).alias("ratio"),
)

print(f"\nMerged 8h windows: {merged.height}")

div = merged["divergence"].to_numpy()
div_clean = div[np.isfinite(div)]

print(f"\nDivergence (Drift_cum_8h - Bybit) statistics:")
print(f"  Mean:   {np.mean(div_clean):.6f}")
print(f"  Median: {np.median(div_clean):.6f}")
print(f"  Std:    {np.std(div_clean):.6f}")
print(f"  Skew:   {stats.skew(div_clean):.4f}")
print(f"  Kurt:   {stats.kurtosis(div_clean):.4f}")
print(f"  Min:    {np.min(div_clean):.6f}")
print(f"  Max:    {np.max(div_clean):.6f}")
pcts = np.percentile(div_clean, [1, 5, 10, 25, 50, 75, 90, 95, 99])
print(f"  Percentiles [1,5,10,25,50,75,90,95,99]:")
for p, v in zip([1, 5, 10, 25, 50, 75, 90, 95, 99], pcts):
    print(f"    P{p:2d}: {v:+.6f}")

# Autocorrelation of divergence
print(f"\nDivergence autocorrelation:")
for lag_periods, label in [(1, "8h"), (3, "24h"), (21, "1w")]:
    if len(div_clean) > lag_periods:
        ac = np.corrcoef(div_clean[lag_periods:], div_clean[:-lag_periods])[0, 1]
        print(f"  Lag {label} ({lag_periods} periods): {ac:.4f}")

# Mean-reversion test: does extreme divergence revert?
p90 = np.percentile(div_clean, 90)
p10 = np.percentile(div_clean, 10)

merged_np = merged.with_columns(
    pl.col("divergence").shift(-1).alias("div_next"),
    pl.col("divergence").shift(-3).alias("div_next_3"),
).drop_nulls()

extreme_high = merged_np.filter(pl.col("divergence") > p90)
extreme_low = merged_np.filter(pl.col("divergence") < p10)
middle = merged_np.filter(
    (pl.col("divergence") >= p10) & (pl.col("divergence") <= p90)
)

print(f"\nMean-reversion of divergence:")
print(f"  When divergence > P90 ({p90:.6f}):")
print(f"    N={extreme_high.height}")
chg = extreme_high["div_next"].to_numpy() - extreme_high["divergence"].to_numpy()
print(f"    Next-period change: {np.mean(chg):.6f} (t={stats.ttest_1samp(chg, 0).statistic:.2f})")
chg3 = extreme_high["div_next_3"].to_numpy() - extreme_high["divergence"].to_numpy()
print(f"    3-period change:    {np.mean(chg3):.6f} (t={stats.ttest_1samp(chg3, 0).statistic:.2f})")

print(f"  When divergence < P10 ({p10:.6f}):")
print(f"    N={extreme_low.height}")
chg = extreme_low["div_next"].to_numpy() - extreme_low["divergence"].to_numpy()
print(f"    Next-period change: {np.mean(chg):.6f} (t={stats.ttest_1samp(chg, 0).statistic:.2f})")
chg3 = extreme_low["div_next_3"].to_numpy() - extreme_low["divergence"].to_numpy()
print(f"    3-period change:    {np.mean(chg3):.6f} (t={stats.ttest_1samp(chg3, 0).statistic:.2f})")

# Is the divergence tradeable? Estimate PnL
# Long Drift + Short Bybit when divergence < P10 (expect reversion → Drift FR rises)
# This is FR arb: if Drift FR < Bybit FR, you're paying less (or earning more) on Drift side
print(f"\n--- FR Arb Feasibility ---")
drift_mean = merged["drift_fr_8h_cum"].mean()
bybit_mean = merged["bybit_fr"].mean()
print(f"  Mean Drift 8h cum FR: {drift_mean:.6f} ({drift_mean*3*365:.2f}% APR)")
print(f"  Mean Bybit 8h FR:     {bybit_mean:.6f} ({bybit_mean*3*365:.2f}% APR)")
print(f"  Mean divergence:      {drift_mean - bybit_mean:.6f} ({(drift_mean - bybit_mean)*3*365:.2f}% APR)")

# Conditional arb: only open when divergence is extreme
# When Drift >> Bybit: short Drift, long Bybit (earn Drift FR, pay Bybit FR)
high_div = merged.filter(pl.col("divergence") > p90)
low_div = merged.filter(pl.col("divergence") < p10)
print(f"\n  Conditional (divergence > P90, short Drift / long Bybit):")
arb_pnl_high = high_div["drift_fr_8h_cum"].to_numpy() - high_div["bybit_fr"].to_numpy()
print(f"    Mean 8h PnL per $1: {np.mean(arb_pnl_high):.6f} ({np.mean(arb_pnl_high)*3*365:.2f}% APR)")
print(f"    Sharpe (ann.):      {np.mean(arb_pnl_high)/np.std(arb_pnl_high)*np.sqrt(3*365):.2f}")

print(f"  Conditional (divergence < P10, long Drift / short Bybit):")
arb_pnl_low = low_div["bybit_fr"].to_numpy() - low_div["drift_fr_8h_cum"].to_numpy()
print(f"    Mean 8h PnL per $1: {np.mean(arb_pnl_low):.6f} ({np.mean(arb_pnl_low)*3*365:.2f}% APR)")
print(f"    Sharpe (ann.):      {np.mean(arb_pnl_low)/np.std(arb_pnl_low)*np.sqrt(3*365):.2f}")


# ─────────────────────────────────────────────────────────────
# PART 2: FR-Informed MM Position Bias
# ─────────────────────────────────────────────────────────────
print("\n" + "=" * 80)
print("PART 2: FR-INFORMED MM INVENTORY BIAS")
print("=" * 80)

# Use hourly data for this backtest
# Strategy: MM on Drift. Use Drift hourly FR as signal for inventory bias.
# Reservation price: r = mid - q*gamma*sigma^2*T + alpha*FR_signal
# We model: at each hour, MM earns spread (from Drift fill data)
# plus/minus inventory * price change + inventory * FR_earned

# Merge Drift FR with Drift candles hourly
drift_hourly = (
    drift_fr
    .select("ts_hour", "funding_rate", "oracle_price_twap", "mark_price_twap")
    .join(
        drift_candles.with_columns(
            pl.col("timestamp").dt.truncate("1h").alias("ts_hour")
        ).select("ts_hour", "fill_close", "volume_quote"),
        on="ts_hour",
        how="inner",
    )
    .sort("ts_hour")
    .with_columns(
        pl.col("fill_close").pct_change().alias("ret_1h"),
        # Rolling FR: exponential moving average as signal
        pl.col("funding_rate").ewm_mean(span=8).alias("fr_ema8"),
        pl.col("funding_rate").ewm_mean(span=24).alias("fr_ema24"),
        # Realized vol (rolling 24h)
        pl.col("fill_close").pct_change().rolling_std(24).alias("rvol_24h"),
    )
    .drop_nulls(subset=["ret_1h", "fr_ema8", "rvol_24h"])
)

print(f"\nHourly merged dataset: {drift_hourly.height} rows")
print(f"Period: {drift_hourly['ts_hour'].min()} → {drift_hourly['ts_hour'].max()}")

# Backtest parameters
SPREAD_HALF_BPS = 5.0  # half-spread captured in bps (conservative for Drift ~10bp spread)
GAMMA = 0.01  # risk aversion
MAX_Q = 1.0   # max inventory in notional units
FR_ALPHA_VALUES = [0, 50, 100, 200, 500]  # FR bias multiplier

results = {}
for alpha in FR_ALPHA_VALUES:
    fr_signal = drift_hourly["fr_ema8"].to_numpy()
    rvol = drift_hourly["rvol_24h"].to_numpy()
    ret = drift_hourly["ret_1h"].to_numpy()
    fr_raw = drift_hourly["funding_rate"].to_numpy()
    price = drift_hourly["fill_close"].to_numpy()
    vol_q = drift_hourly["volume_quote"].to_numpy()

    n = len(ret)
    pnl = np.zeros(n)
    inventory = np.zeros(n)
    cum_pnl = np.zeros(n)

    q = 0.0  # current inventory
    for i in range(1, n):
        # Target inventory based on FR signal: positive FR → short bias
        q_target = np.clip(-alpha * fr_signal[i - 1], -MAX_Q, MAX_Q)

        # Adjust inventory towards target (partial fill model)
        q_delta = np.clip(q_target - q, -0.5, 0.5)  # max 0.5 per hour

        # Spread PnL (earn half-spread on the trade)
        spread_pnl = abs(q_delta) * SPREAD_HALF_BPS * 1e-4 * price[i]

        # Inventory PnL: existing position * return
        inv_pnl = q * ret[i] * price[i - 1]

        # FR PnL: inventory earns/pays funding
        # Positive FR + short position → earn; Positive FR + long position → pay
        fr_pnl = -q * fr_raw[i]  # short earns when FR > 0

        pnl[i] = spread_pnl + inv_pnl + fr_pnl
        q += q_delta
        inventory[i] = q
        cum_pnl[i] = cum_pnl[i - 1] + pnl[i]

    total = cum_pnl[-1]
    n_full_days = (n // 24) * 24
    daily_pnl = pnl[:n_full_days].reshape(-1, 24).sum(axis=1) if n >= 24 else pnl
    sharpe = np.mean(daily_pnl) / np.std(daily_pnl) * np.sqrt(365) if np.std(daily_pnl) > 0 else 0
    max_dd = np.min(cum_pnl - np.maximum.accumulate(cum_pnl))

    results[alpha] = {
        "total_pnl": total,
        "sharpe": sharpe,
        "max_dd": max_dd,
        "mean_inv": np.mean(np.abs(inventory)),
        "fr_pnl_frac": np.sum(-inventory[1:] * fr_raw[1:]) / total if total != 0 else 0,
    }

print(f"\nMM Backtest Results (spread={SPREAD_HALF_BPS}bp half-spread):")
print(f"{'Alpha':>8} {'Total PnL':>12} {'Sharpe':>8} {'Max DD':>10} {'|Inv| avg':>10} {'FR% of PnL':>12}")
print("-" * 65)
for alpha, r in results.items():
    label = "neutral" if alpha == 0 else str(alpha)
    print(
        f"{label:>8} {r['total_pnl']:>12.2f} {r['sharpe']:>8.2f} {r['max_dd']:>10.2f}"
        f" {r['mean_inv']:>10.4f} {r['fr_pnl_frac']:>11.1%}"
    )

# Statistical test: is alpha=100 better than alpha=0?
best_alpha = max(results, key=lambda k: results[k]["sharpe"])
print(f"\n  Best alpha by Sharpe: {best_alpha}")
print(f"  Improvement vs neutral: Sharpe {results[best_alpha]['sharpe']:.2f} vs {results[0]['sharpe']:.2f}")


# ─────────────────────────────────────────────────────────────
# PART 3: FR Momentum / Mean-Reversion
# ─────────────────────────────────────────────────────────────
print("\n" + "=" * 80)
print("PART 3: DRIFT FR AUTOCORRELATION & EXTREME FR → PRICE")
print("=" * 80)

fr_series = drift_fr.sort("timestamp")["funding_rate"].to_numpy()
fr_clean = fr_series[np.isfinite(fr_series)]

print(f"\nDrift hourly FR statistics:")
print(f"  N:      {len(fr_clean)}")
print(f"  Mean:   {np.mean(fr_clean):.7f}")
print(f"  Std:    {np.std(fr_clean):.7f}")
print(f"  Median: {np.median(fr_clean):.7f}")

print(f"\nAutocorrelation of Drift hourly FR:")
for lag, label in [(1, "1h"), (8, "8h"), (24, "24h"), (168, "1w")]:
    if len(fr_clean) > lag:
        ac = np.corrcoef(fr_clean[lag:], fr_clean[:-lag])[0, 1]
        print(f"  Lag {label:>4} ({lag:>3} hours): {ac:.4f}")

# Extreme FR → future price movement
# Join FR with Binance price (more liquid reference) at hourly level
fr_price = (
    drift_fr
    .select("ts_hour", "funding_rate")
    .join(
        binance.with_columns(
            pl.col("timestamp").dt.truncate("1h").alias("ts_hour")
        ).select("ts_hour", "close"),
        on="ts_hour",
        how="inner",
    )
    .sort("ts_hour")
    .with_columns(
        # Forward returns at various horizons
        (pl.col("close").shift(-8) / pl.col("close") - 1).alias("fwd_ret_8h"),
        (pl.col("close").shift(-24) / pl.col("close") - 1).alias("fwd_ret_24h"),
        (pl.col("close").shift(-72) / pl.col("close") - 1).alias("fwd_ret_72h"),
        # Rolling z-score of FR
        (
            (pl.col("funding_rate") - pl.col("funding_rate").rolling_mean(168))
            / pl.col("funding_rate").rolling_std(168)
        ).alias("fr_zscore"),
    )
    .drop_nulls()
)

print(f"\nFR-Price dataset: {fr_price.height} rows")

fr_z = fr_price["fr_zscore"].to_numpy()
for horizon, col in [("8h", "fwd_ret_8h"), ("24h", "fwd_ret_24h"), ("72h", "fwd_ret_72h")]:
    fwd = fr_price[col].to_numpy()
    # Full sample correlation
    r, p = stats.pearsonr(fr_z, fwd)
    print(f"\n  FR z-score → {horizon} forward return:")
    print(f"    Pearson r={r:.4f}, p={p:.4f}")

    # Extreme quintiles
    q_high = np.percentile(fr_z, 90)
    q_low = np.percentile(fr_z, 10)
    high_ret = fwd[fr_z > q_high]
    low_ret = fwd[fr_z < q_low]
    mid_ret = fwd[(fr_z >= q_low) & (fr_z <= q_high)]

    print(f"    FR > P90 (N={len(high_ret)}): mean ret = {np.mean(high_ret)*100:.3f}%")
    print(f"    FR < P10 (N={len(low_ret)}):  mean ret = {np.mean(low_ret)*100:.3f}%")
    print(f"    Middle   (N={len(mid_ret)}):   mean ret = {np.mean(mid_ret)*100:.3f}%")

    # Is high-FR a bearish signal? (contrarian)
    t_stat, p_val = stats.ttest_ind(high_ret, low_ret)
    print(f"    High vs Low t-test: t={t_stat:.2f}, p={p_val:.4f}")


# ─────────────────────────────────────────────────────────────
# PART 4: Mark-Oracle Premium as Leading Indicator
# ─────────────────────────────────────────────────────────────
print("\n" + "=" * 80)
print("PART 4: MARK-ORACLE PREMIUM AS LEADING INDICATOR")
print("=" * 80)

premium = (
    drift_fr
    .select("ts_hour", "funding_rate", "oracle_price_twap", "mark_price_twap")
    .with_columns(
        ((pl.col("mark_price_twap") - pl.col("oracle_price_twap"))
         / pl.col("oracle_price_twap") * 10000).alias("premium_bps"),
    )
    .join(
        binance.with_columns(
            pl.col("timestamp").dt.truncate("1h").alias("ts_hour")
        ).select("ts_hour", "close"),
        on="ts_hour",
        how="inner",
    )
    .sort("ts_hour")
    .with_columns(
        pl.col("funding_rate").shift(-1).alias("next_fr"),
        (pl.col("close").shift(-1) / pl.col("close") - 1).alias("fwd_ret_1h"),
        (pl.col("close").shift(-8) / pl.col("close") - 1).alias("fwd_ret_8h"),
        pl.col("premium_bps").rolling_mean(8).alias("premium_ema8"),
        (
            (pl.col("premium_bps") - pl.col("premium_bps").rolling_mean(168))
            / pl.col("premium_bps").rolling_std(168)
        ).alias("premium_zscore"),
    )
    .drop_nulls()
)

print(f"\nPremium dataset: {premium.height} rows")

prem = premium["premium_bps"].to_numpy()
print(f"\nMark-Oracle Premium (bps) statistics:")
print(f"  Mean:   {np.mean(prem):.2f} bps")
print(f"  Std:    {np.std(prem):.2f} bps")
print(f"  Median: {np.median(prem):.2f} bps")
prem_pcts = np.percentile(prem, [1, 5, 25, 50, 75, 95, 99])
for p, v in zip([1, 5, 25, 50, 75, 95, 99], prem_pcts):
    print(f"  P{p:2d}: {v:+.2f} bps")

# 4a: Premium → next FR
next_fr = premium["next_fr"].to_numpy()
r, p = stats.pearsonr(prem, next_fr)
print(f"\nPremium → next-hour FR:")
print(f"  Pearson r={r:.4f}, p={p:.2e}")
print(f"  (Expected: high correlation since FR = f(mark - oracle))")

# 4b: Premium → next-hour price return
fwd_1h = premium["fwd_ret_1h"].to_numpy()
r, p = stats.pearsonr(prem, fwd_1h)
print(f"\nPremium → 1h forward return:")
print(f"  Pearson r={r:.4f}, p={p:.4f}")

# Premium z-score as signal
prem_z = premium["premium_zscore"].to_numpy()
fwd_8h = premium["fwd_ret_8h"].to_numpy()
r8, p8 = stats.pearsonr(prem_z, fwd_8h)
print(f"\nPremium z-score → 8h forward return:")
print(f"  Pearson r={r8:.4f}, p={p8:.4f}")

# Quintile analysis for premium
print(f"\nPremium z-score quintile analysis → 1h forward return:")
for q_lo, q_hi, label in [
    (0, 10, "P0-P10 (discount)"),
    (10, 30, "P10-P30"),
    (30, 70, "P30-P70 (neutral)"),
    (70, 90, "P70-P90"),
    (90, 100, "P90-P100 (premium)"),
]:
    lo = np.percentile(prem_z, q_lo) if q_lo > 0 else -np.inf
    hi = np.percentile(prem_z, q_hi) if q_hi < 100 else np.inf
    mask = (prem_z > lo) & (prem_z <= hi)
    rets = fwd_1h[mask]
    if len(rets) > 0:
        print(f"  {label:25s}: N={len(rets):5d}, mean={np.mean(rets)*10000:+.2f} bps, t={np.mean(rets)/np.std(rets)*np.sqrt(len(rets)):.2f}")

# 4c: Premium → 8h forward return quintile analysis
print(f"\nPremium z-score quintile analysis → 8h forward return:")
for q_lo, q_hi, label in [
    (0, 10, "P0-P10 (discount)"),
    (10, 30, "P10-P30"),
    (30, 70, "P30-P70 (neutral)"),
    (70, 90, "P70-P90"),
    (90, 100, "P90-P100 (premium)"),
]:
    lo = np.percentile(prem_z, q_lo) if q_lo > 0 else -np.inf
    hi = np.percentile(prem_z, q_hi) if q_hi < 100 else np.inf
    mask = (prem_z > lo) & (prem_z <= hi)
    rets = fwd_8h[mask]
    if len(rets) > 0:
        print(f"  {label:25s}: N={len(rets):5d}, mean={np.mean(rets)*10000:+.2f} bps, t={np.mean(rets)/np.std(rets)*np.sqrt(len(rets)):.2f}")

# 4d: Asymmetric quoting value
# If premium predicts direction, MM can tighten one side
# Compute: information ratio of premium signal for 1h returns
print(f"\n--- Asymmetric Quoting Value ---")
# Split into positive and negative premium
pos_mask = prem_z > 1.0
neg_mask = prem_z < -1.0
neutral_mask = (prem_z >= -1.0) & (prem_z <= 1.0)

for label, mask in [("Premium > +1σ", pos_mask), ("Premium < -1σ", neg_mask), ("Neutral", neutral_mask)]:
    rets = fwd_1h[mask]
    if len(rets) > 10:
        hit_rate = np.mean(rets > 0)
        print(f"  {label}: N={len(rets)}, mean_ret={np.mean(rets)*10000:+.2f}bps, hit_rate={hit_rate:.1%}")

# 4e: Cross-venue premium comparison
# Is Drift premium more predictive than just using Binance data?
print(f"\n--- Drift Premium Uniqueness ---")
# Merge Drift premium with Binance own basis (close vs Drift oracle as proxy)
cross = (
    premium.select("ts_hour", "premium_bps", "premium_zscore", "fwd_ret_1h", "fwd_ret_8h")
    .join(
        drift_candles.with_columns(
            pl.col("timestamp").dt.truncate("1h").alias("ts_hour")
        ).select("ts_hour", pl.col("fill_close").alias("drift_fill_price")),
        on="ts_hour",
        how="inner",
    )
    .join(
        binance.with_columns(
            pl.col("timestamp").dt.truncate("1h").alias("ts_hour")
        ).select("ts_hour", pl.col("close").alias("binance_close")),
        on="ts_hour",
        how="inner",
    )
    .with_columns(
        ((pl.col("drift_fill_price") - pl.col("binance_close"))
         / pl.col("binance_close") * 10000).alias("drift_binance_spread_bps"),
    )
    .drop_nulls()
)

drift_binance_spr = cross["drift_binance_spread_bps"].to_numpy()
print(f"\nDrift-Binance fill price spread:")
print(f"  Mean: {np.mean(drift_binance_spr):.2f} bps")
print(f"  Std:  {np.std(drift_binance_spr):.2f} bps")
print(f"  Median: {np.median(drift_binance_spr):.2f} bps")

# Correlation: premium vs Drift-Binance spread
r_cross, p_cross = stats.pearsonr(
    cross["premium_bps"].to_numpy(), drift_binance_spr
)
print(f"  Premium vs Drift-Binance spread: r={r_cross:.4f}, p={p_cross:.2e}")

# Multiple regression: does premium add info beyond Drift-Binance spread?
from numpy.linalg import lstsq
X = np.column_stack([
    cross["premium_zscore"].to_numpy(),
    drift_binance_spr / np.std(drift_binance_spr),
    np.ones(len(drift_binance_spr)),
])
y_1h = cross["fwd_ret_1h"].to_numpy()
y_8h = cross["fwd_ret_8h"].to_numpy()

beta_1h, res_1h, _, _ = lstsq(X, y_1h, rcond=None)
ss_tot_1h = np.sum((y_1h - np.mean(y_1h)) ** 2)
r2_1h = 1 - res_1h[0] / ss_tot_1h if len(res_1h) > 0 else 0

beta_8h, res_8h, _, _ = lstsq(X, y_8h, rcond=None)
ss_tot_8h = np.sum((y_8h - np.mean(y_8h)) ** 2)
r2_8h = 1 - res_8h[0] / ss_tot_8h if len(res_8h) > 0 else 0

print(f"\nMultiple regression: fwd_ret ~ premium_z + drift_binance_spread + const")
print(f"  1h return: R²={r2_1h:.6f}, β_premium={beta_1h[0]:.6f}, β_spread={beta_1h[1]:.6f}")
print(f"  8h return: R²={r2_8h:.6f}, β_premium={beta_8h[0]:.6f}, β_spread={beta_8h[1]:.6f}")


# ─────────────────────────────────────────────────────────────
# SUMMARY & CONCLUSIONS
# ─────────────────────────────────────────────────────────────
print("\n" + "=" * 80)
print("SUMMARY & ACTIONABLE CONCLUSIONS")
print("=" * 80)

print("""
1. FR DIVERGENCE DYNAMICS:
   - Drift cumulative 8h FR vs Bybit FR shows systematic divergence.
   - The divergence is mean-reverting (check autocorrelation & extreme reversion stats above).
   - Pure FR arb (long one venue, short other) captures the spread but requires
     capital on both venues and faces execution risk on Drift.

2. FR-INFORMED MM BIAS:
   - Compare alpha=0 (neutral) vs best alpha in the backtest table above.
   - FR bias shifts inventory to earn funding: when FR is positive, carry short
     to collect from longs. This is most valuable in trending FR regimes.
   - Key metric: what fraction of PnL comes from FR vs spread.

3. FR AUTOCORRELATION & PRICE PREDICTION:
   - Drift FR is highly autocorrelated at 1h (momentum), decaying by 24h.
   - Extreme FR as contrarian signal: check the t-tests above.
   - If |t| < 2 for extreme FR → price, then FR is NOT a directional signal
     (consistent with CEX findings), even on Drift.

4. MARK-ORACLE PREMIUM:
   - Premium strongly predicts next FR (mechanical relationship).
   - Key question: does premium predict PRICE? Check r and p-values above.
   - If premium → 1h return is significant: MM can quote asymmetrically.
   - The quintile analysis shows whether extreme premium/discount predicts
     direction (and by how much in bps).
   - Regression R² shows total predictability (expected to be tiny but
     potentially positive in bps terms for MM).

PRACTICAL IMPLICATIONS FOR DRIFT MM:
   - Use FR EMA as inventory bias signal (earn funding on average).
   - If premium is predictive: tighten bid when premium > +1σ (expect price up,
     more likely to get filled on bid → favorable), widen ask.
   - FR arb between Drift and Bybit is theoretically possible but operationally
     heavy (two venue margin, Solana latency vs CEX).
""")
