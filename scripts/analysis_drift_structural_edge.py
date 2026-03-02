"""
Drift Protocol Structural Edge Discovery
=========================================
Deep analysis of Drift SOL-PERP vs CEX data to find actionable edges.
"""
import polars as pl
import numpy as np
from scipy import stats, signal

# ============================================================
# Load data
# ============================================================
drift = pl.read_parquet("data/drift_sol_perp_candles_1h.parquet")
binance = pl.read_parquet("data/binance_solusdt_1h_full.parquet")
drift_fr = pl.read_parquet("data/drift_sol_perp_funding_rates.parquet")
bybit_fr = pl.read_parquet("data/bybit_solusdt_funding_rate_full.parquet")

# Normalize timestamps to hourly UTC (no tz, truncated, microsecond precision)
drift = drift.with_columns(
    pl.col("timestamp").dt.replace_time_zone(None).cast(pl.Datetime("us")).dt.truncate("1h").alias("ts_hour")
)
binance = binance.with_columns(
    pl.col("timestamp").dt.replace_time_zone(None).cast(pl.Datetime("us")).dt.truncate("1h").alias("ts_hour")
)
drift_fr = drift_fr.with_columns(
    pl.col("timestamp").dt.replace_time_zone(None).cast(pl.Datetime("us")).dt.truncate("1h").alias("ts_hour")
)
bybit_fr = bybit_fr.with_columns(
    pl.col("timestamp").dt.replace_time_zone(None).cast(pl.Datetime("us")).dt.truncate("1h").alias("ts_hour")
)

# Filter out rows where Drift oracle prices are 0 (early data issue)
drift = drift.filter(pl.col("close") > 0)

print("=" * 80)
print("DRIFT PROTOCOL STRUCTURAL EDGE DISCOVERY")
print("=" * 80)

# ============================================================
# 1. Oracle-CEX Price Divergence
# ============================================================
print("\n" + "=" * 80)
print("1. ORACLE-CEX PRICE DIVERGENCE (Drift Oracle vs Binance)")
print("=" * 80)

merged = drift.join(binance, on="ts_hour", suffix="_bn")
merged = merged.with_columns(
    ((pl.col("close") - pl.col("close_bn")) / pl.col("close_bn") * 10000).alias("div_bps")
)

div = merged["div_bps"].drop_nulls().drop_nans().to_numpy()
print(f"\nSample size: {len(div):,} hourly observations")
print(f"Date range: {merged['ts_hour'].min()} to {merged['ts_hour'].max()}")
print(f"\nDivergence Distribution (bps):")
print(f"  Mean:   {np.mean(div):+.3f}")
print(f"  Median: {np.median(div):+.3f}")
print(f"  Std:    {np.std(div):.3f}")
print(f"  P5:     {np.percentile(div, 5):+.3f}")
print(f"  P25:    {np.percentile(div, 25):+.3f}")
print(f"  P75:    {np.percentile(div, 75):+.3f}")
print(f"  P95:    {np.percentile(div, 95):+.3f}")
print(f"  P99:    {np.percentile(div, 99):+.3f}")
print(f"  Min:    {np.min(div):+.3f}")
print(f"  Max:    {np.max(div):+.3f}")
print(f"  |div| > 10bps: {np.mean(np.abs(div) > 10):.2%}")
print(f"  |div| > 50bps: {np.mean(np.abs(div) > 50):.2%}")

# Autocorrelation of divergence
acf_lags = [1, 2, 3, 6, 12, 24]
print(f"\nAutocorrelation of divergence:")
for lag in acf_lags:
    if len(div) > lag:
        r = np.corrcoef(div[:-lag], div[lag:])[0, 1]
        flag = " *** ACTIONABLE" if abs(r) > 0.1 else ""
        print(f"  Lag {lag:2d}h: r = {r:+.4f}{flag}")

# Mean-reversion half-life (AR(1) model)
r1 = np.corrcoef(div[:-1], div[1:])[0, 1]
if 0 < r1 < 1:
    half_life = -np.log(2) / np.log(r1)
    print(f"\nMean-reversion half-life: {half_life:.2f} hours")
    # Expected arb return: if we trade when |div| > 2*std, expect reversion to 0
    threshold = 2 * np.std(div)
    extreme = np.abs(div) > threshold
    if extreme.sum() > 0:
        avg_extreme = np.mean(np.abs(div[extreme]))
        print(f"  Extreme events (|div| > {threshold:.1f}bps): {extreme.sum()} ({extreme.mean():.2%})")
        print(f"  Avg extreme divergence: {avg_extreme:.1f} bps")
        print(f"  Expected arb return (full reversion): {avg_extreme:.1f} bps per trade")
elif r1 <= 0:
    print(f"\nDivergence is anti-correlated (r1={r1:.4f}) - oscillatory, not persistent")
else:
    print(f"\nDivergence is highly persistent (r1={r1:.4f}) - unit root risk")

# Yearly breakdown
merged_yearly = merged.with_columns(pl.col("ts_hour").dt.year().alias("year"))
print("\nDivergence by year:")
for year in sorted(merged_yearly["year"].unique().to_list()):
    yearly_div = merged_yearly.filter(pl.col("year") == year)["div_bps"].drop_nulls().drop_nans().to_numpy()
    if len(yearly_div) > 0:
        print(f"  {year}: mean={np.mean(yearly_div):+.2f}bps, std={np.std(yearly_div):.2f}bps, n={len(yearly_div)}")


# ============================================================
# 2. Fill Price vs Oracle Price Gap (vAMM Slippage)
# ============================================================
print("\n" + "=" * 80)
print("2. FILL PRICE vs ORACLE PRICE GAP (vAMM Slippage)")
print("=" * 80)

# Use only rows where both fill_close and close are valid
fill_gap = drift.filter(
    (pl.col("fill_close") > 0) & (pl.col("close") > 0)
).with_columns(
    ((pl.col("fill_close") - pl.col("close")) / pl.col("close") * 10000).alias("slippage_bps")
)

slip = fill_gap["slippage_bps"].drop_nulls().drop_nans().to_numpy()
print(f"\nSample size: {len(slip):,}")
print(f"\nFill Slippage Distribution (bps):")
print(f"  Mean:   {np.mean(slip):+.3f}")
print(f"  Median: {np.median(slip):+.3f}")
print(f"  Std:    {np.std(slip):.3f}")
print(f"  P5:     {np.percentile(slip, 5):+.3f}")
print(f"  P95:    {np.percentile(slip, 95):+.3f}")
print(f"  |slip| > 5bps:  {np.mean(np.abs(slip) > 5):.2%}")
print(f"  |slip| > 20bps: {np.mean(np.abs(slip) > 20):.2%}")

# Slippage vs realized volatility
# Compute hourly rvol as |log return| on Drift oracle
fill_gap2 = fill_gap.with_columns(
    pl.col("close").log().diff().abs().alias("abs_log_ret")
).drop_nulls()

slip2 = fill_gap2["slippage_bps"].to_numpy()
rvol2 = fill_gap2["abs_log_ret"].to_numpy()

# Remove NaN/Inf
mask = np.isfinite(slip2) & np.isfinite(rvol2)
slip2, rvol2 = slip2[mask], rvol2[mask]

r_slip_vol, p_slip_vol = stats.spearmanr(np.abs(slip2), rvol2)
print(f"\nSlippage magnitude vs hourly |return|:")
print(f"  Spearman r = {r_slip_vol:+.4f}, p = {p_slip_vol:.2e}")
flag = " *** ACTIONABLE" if abs(r_slip_vol) > 0.1 and p_slip_vol < 0.05 else ""
print(f"  {flag}")

# Slippage vs volume
vol_arr = fill_gap.select("volume_quote").to_numpy().flatten()
slip_arr = fill_gap["slippage_bps"].to_numpy()
mask = np.isfinite(vol_arr) & np.isfinite(slip_arr) & (vol_arr > 0)
r_slip_volq, p_slip_volq = stats.spearmanr(np.abs(slip_arr[mask]), vol_arr[mask])
print(f"\nSlippage magnitude vs Drift volume_quote:")
print(f"  Spearman r = {r_slip_volq:+.4f}, p = {p_slip_volq:.2e}")
flag = " *** ACTIONABLE" if abs(r_slip_volq) > 0.1 and p_slip_volq < 0.05 else ""
print(f"  {flag}")

# Yearly breakdown
fill_gap_yearly = fill_gap.with_columns(pl.col("ts_hour").dt.year().alias("year"))
print("\nSlippage by year:")
for year in sorted(fill_gap_yearly["year"].unique().to_list()):
    ys = fill_gap_yearly.filter(pl.col("year") == year)["slippage_bps"].drop_nulls().drop_nans().to_numpy()
    if len(ys) > 0:
        print(f"  {year}: mean={np.mean(ys):+.2f}bps, std={np.std(ys):.2f}bps, |mean|={np.abs(np.mean(ys)):.2f}bps")

# ============================================================
# 3. Drift Volume Structure
# ============================================================
print("\n" + "=" * 80)
print("3. VOLUME STRUCTURE (Drift vs Binance)")
print("=" * 80)

vol_merged = drift.join(binance, on="ts_hour", suffix="_bn").filter(
    (pl.col("volume_quote") > 0) & (pl.col("volume") > 0)
).with_columns([
    pl.col("ts_hour").dt.hour().alias("hour"),
    pl.col("ts_hour").dt.weekday().alias("dow"),  # 1=Mon, 7=Sun
])

# Hour-of-day seasonality
print("\nHour-of-day volume (normalized to hourly mean):")
print(f"{'Hour':>4}  {'Drift':>10}  {'Binance':>10}  {'Drift/Binance':>14}  {'Flag':>10}")
print("-" * 55)

drift_vol_mean = vol_merged["volume_quote"].mean()
bn_vol_mean = vol_merged["volume"].mean()

hourly_stats = []
for h in range(24):
    hdf = vol_merged.filter(pl.col("hour") == h)
    d_ratio = hdf["volume_quote"].mean() / drift_vol_mean
    b_ratio = hdf["volume"].mean() / bn_vol_mean
    rel = d_ratio / b_ratio if b_ratio > 0 else 0
    flag = " *** HIGH" if rel > 1.15 else (" * low" if rel < 0.85 else "")
    hourly_stats.append((h, d_ratio, b_ratio, rel, flag))
    print(f"{h:4d}  {d_ratio:10.3f}  {b_ratio:10.3f}  {rel:14.3f}  {flag}")

# Day-of-week
print("\nDay-of-week volume (normalized):")
print(f"{'DOW':>4}  {'Drift':>10}  {'Binance':>10}  {'Drift/Binance':>14}")
print("-" * 45)
dow_names = {1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 7: "Sun"}
for d in range(1, 8):
    ddf = vol_merged.filter(pl.col("dow") == d)
    if len(ddf) == 0:
        continue
    d_ratio = ddf["volume_quote"].mean() / drift_vol_mean
    b_ratio = ddf["volume"].mean() / bn_vol_mean
    rel = d_ratio / b_ratio if b_ratio > 0 else 0
    flag = " *** HIGH" if rel > 1.10 else (" * low" if rel < 0.90 else "")
    print(f"{dow_names[d]:>4}  {d_ratio:10.3f}  {b_ratio:10.3f}  {rel:14.3f}{flag}")

# Volume ratio distribution
vol_ratio = vol_merged.with_columns(
    (pl.col("volume_quote") / (pl.col("volume") * pl.col("close_bn"))).alias("drift_share")
)
vs = vol_ratio["drift_share"].drop_nulls().drop_nans().to_numpy()
vs = vs[np.isfinite(vs) & (vs > 0) & (vs < 1)]  # reasonable range
print(f"\nDrift volume as share of Binance notional:")
print(f"  Mean:   {np.mean(vs):.6f} ({np.mean(vs)*100:.4f}%)")
print(f"  Median: {np.median(vs):.6f}")
print(f"  P95:    {np.percentile(vs, 95):.6f}")

# ============================================================
# 4. Drift Funding Rate vs Bybit Funding Rate
# ============================================================
print("\n" + "=" * 80)
print("4. FUNDING RATE ANALYSIS (Drift vs Bybit)")
print("=" * 80)

# Drift FR is hourly, Bybit is 8-hourly
# First analyze Drift FR alone
dfr = drift_fr["funding_rate"].drop_nulls().drop_nans().to_numpy()
print(f"\nDrift FR stats (hourly, n={len(dfr):,}):")
print(f"  Mean:   {np.mean(dfr)*100:.6f}%")
print(f"  Std:    {np.std(dfr)*100:.6f}%")
print(f"  Annualized mean: {np.mean(dfr)*8760*100:.2f}%")
print(f"  Positive rate: {(dfr > 0).mean():.2%}")

# Aggregate Drift FR to 8h periods to match Bybit
# Bybit pays at 00:00, 08:00, 16:00 UTC
# Drift accrues hourly; sum 8 hours for comparison
drift_fr_8h = drift_fr.with_columns(
    pl.col("ts_hour").dt.truncate("8h").alias("ts_8h")
).group_by("ts_8h").agg([
    pl.col("funding_rate").sum().alias("drift_fr_8h"),
    pl.col("funding_rate").count().alias("n_hours"),
]).filter(pl.col("n_hours") >= 7)  # require at least 7 of 8 hours

bybit_fr_clean = bybit_fr.with_columns(
    pl.col("ts_hour").dt.truncate("8h").alias("ts_8h")
)

fr_merged = drift_fr_8h.join(bybit_fr_clean, on="ts_8h")

drift_fr_arr = fr_merged["drift_fr_8h"].to_numpy()
bybit_fr_arr = fr_merged["funding_rate"].to_numpy()
mask = np.isfinite(drift_fr_arr) & np.isfinite(bybit_fr_arr)
drift_fr_arr, bybit_fr_arr = drift_fr_arr[mask], bybit_fr_arr[mask]

print(f"\n8h-matched observations: {len(drift_fr_arr):,}")
print(f"Date range: {fr_merged['ts_8h'].min()} to {fr_merged['ts_8h'].max()}")

r_fr, p_fr = stats.pearsonr(drift_fr_arr, bybit_fr_arr)
print(f"\nDrift vs Bybit FR correlation:")
print(f"  Pearson r = {r_fr:+.4f}, p = {p_fr:.2e}")
flag = " *** ACTIONABLE" if abs(r_fr) > 0.1 and p_fr < 0.05 else ""
print(f"  {flag}")

div_fr = (drift_fr_arr - bybit_fr_arr) * 100
print(f"\nFR divergence (Drift - Bybit, %):")
print(f"  Mean:   {np.mean(div_fr):+.6f}%")
print(f"  Std:    {np.std(div_fr):.6f}%")
print(f"  P5:     {np.percentile(div_fr, 5):+.6f}%")
print(f"  P95:    {np.percentile(div_fr, 95):+.6f}%")

# Cross-predictability: Granger-like test
# Does Drift FR predict next Bybit FR? (and vice versa)
print("\nCross-predictability (1-period ahead, 8h):")
# Drift t -> Bybit t+1
if len(drift_fr_arr) > 2:
    r_d2b, p_d2b = stats.pearsonr(drift_fr_arr[:-1], bybit_fr_arr[1:])
    flag = " *** ACTIONABLE" if abs(r_d2b) > 0.1 and p_d2b < 0.05 else ""
    print(f"  Drift(t) -> Bybit(t+1): r = {r_d2b:+.4f}, p = {p_d2b:.2e}{flag}")

    r_b2d, p_b2d = stats.pearsonr(bybit_fr_arr[:-1], drift_fr_arr[1:])
    flag = " *** ACTIONABLE" if abs(r_b2d) > 0.1 and p_b2d < 0.05 else ""
    print(f"  Bybit(t) -> Drift(t+1): r = {r_b2d:+.4f}, p = {p_b2d:.2e}{flag}")

    # 2-period ahead
    r_d2b2, p_d2b2 = stats.pearsonr(drift_fr_arr[:-2], bybit_fr_arr[2:])
    flag = " *** ACTIONABLE" if abs(r_d2b2) > 0.1 and p_d2b2 < 0.05 else ""
    print(f"  Drift(t) -> Bybit(t+2): r = {r_d2b2:+.4f}, p = {p_d2b2:.2e}{flag}")

    r_b2d2, p_b2d2 = stats.pearsonr(bybit_fr_arr[:-2], drift_fr_arr[2:])
    flag = " *** ACTIONABLE" if abs(r_b2d2) > 0.1 and p_b2d2 < 0.05 else ""
    print(f"  Bybit(t) -> Drift(t+2): r = {r_b2d2:+.4f}, p = {p_b2d2:.2e}{flag}")

# Hourly Drift FR predictability (using hourly data)
print("\nDrift hourly FR autocorrelation:")
for lag in [1, 2, 4, 8, 24]:
    if len(dfr) > lag:
        r = np.corrcoef(dfr[:-lag], dfr[lag:])[0, 1]
        flag = " *** ACTIONABLE" if abs(r) > 0.1 else ""
        print(f"  Lag {lag:2d}h: r = {r:+.4f}{flag}")

# FR regime: is Drift FR consistently above or below Bybit?
print("\nDrift vs Bybit FR regime:")
drift_higher = (drift_fr_arr > bybit_fr_arr).mean()
print(f"  Drift FR > Bybit FR: {drift_higher:.2%}")
print(f"  Mean Drift 8h FR:  {np.mean(drift_fr_arr)*100:.6f}%")
print(f"  Mean Bybit 8h FR:  {np.mean(bybit_fr_arr)*100:.6f}%")
ann_drift = np.mean(drift_fr_arr) * 3 * 365 * 100  # 3 periods/day * 365 days
ann_bybit = np.mean(bybit_fr_arr) * 3 * 365 * 100
print(f"  Annualized Drift:  {ann_drift:+.2f}%")
print(f"  Annualized Bybit:  {ann_bybit:+.2f}%")
print(f"  Annualized spread: {ann_drift - ann_bybit:+.2f}%")
if abs(ann_drift - ann_bybit) > 1.0:
    print("  *** ACTIONABLE: persistent FR spread -> FR arb opportunity")

# ============================================================
# 5. Predictable Fill Patterns
# ============================================================
print("\n" + "=" * 80)
print("5. PREDICTABLE FILL PATTERNS")
print("=" * 80)

# Volume autocorrelation on Drift
drift_vol = drift.filter(pl.col("volume_quote") > 0)["volume_quote"].to_numpy()
drift_log_vol = np.log(drift_vol[drift_vol > 0])

print(f"\nDrift volume stats:")
print(f"  Mean hourly volume: ${np.mean(drift_vol):,.0f}")
print(f"  Median hourly volume: ${np.median(drift_vol):,.0f}")
print(f"  Std: ${np.std(drift_vol):,.0f}")

print(f"\nDrift log-volume autocorrelation:")
for lag in [1, 2, 4, 8, 12, 24, 48]:
    if len(drift_log_vol) > lag:
        r = np.corrcoef(drift_log_vol[:-lag], drift_log_vol[lag:])[0, 1]
        flag = " *** ACTIONABLE" if abs(r) > 0.1 else ""
        print(f"  Lag {lag:2d}h: r = {r:+.4f}{flag}")

# Does CEX vol spike predict Drift vol spike?
print("\nCross-venue volume prediction:")
vol_cross = drift.join(binance, on="ts_hour", suffix="_bn").filter(
    (pl.col("volume_quote") > 0) & (pl.col("volume") > 0)
).sort("ts_hour")

d_vol = np.log(vol_cross["volume_quote"].to_numpy())
b_vol = np.log(vol_cross["volume"].to_numpy())
mask = np.isfinite(d_vol) & np.isfinite(b_vol)
d_vol, b_vol = d_vol[mask], b_vol[mask]

# Contemporaneous
r_cont, p_cont = stats.pearsonr(d_vol, b_vol)
print(f"  Contemporaneous: r = {r_cont:+.4f}, p = {p_cont:.2e}")

# Binance(t) -> Drift(t+1)
for lag in [1, 2, 4]:
    r, p = stats.pearsonr(b_vol[:-lag], d_vol[lag:])
    flag = " *** ACTIONABLE" if abs(r) > 0.1 and p < 0.05 else ""
    print(f"  Binance(t) -> Drift(t+{lag}): r = {r:+.4f}, p = {p:.2e}{flag}")

# Drift(t) -> Binance(t+1)
for lag in [1, 2, 4]:
    r, p = stats.pearsonr(d_vol[:-lag], b_vol[lag:])
    flag = " *** ACTIONABLE" if abs(r) > 0.1 and p < 0.05 else ""
    print(f"  Drift(t) -> Binance(t+{lag}): r = {r:+.4f}, p = {p:.2e}{flag}")

# High-activity detection: does extreme Binance vol predict Drift vol spike?
b_vol_zscore = (b_vol - np.mean(b_vol)) / np.std(b_vol)
d_vol_zscore = (d_vol - np.mean(d_vol)) / np.std(d_vol)

# When Binance vol > 2 std, what happens to Drift vol next hour?
high_bn = b_vol_zscore[:-1] > 2.0
if high_bn.sum() > 10:
    next_d_vol = d_vol_zscore[1:]
    avg_d_after_high = np.mean(next_d_vol[high_bn])
    avg_d_after_normal = np.mean(next_d_vol[~high_bn])
    t_stat, p_val = stats.ttest_ind(next_d_vol[high_bn], next_d_vol[~high_bn])
    print(f"\n  After Binance vol spike (>2 std):")
    print(f"    N events: {high_bn.sum()}")
    print(f"    Avg Drift vol z-score next hour: {avg_d_after_high:+.3f} (vs normal: {avg_d_after_normal:+.3f})")
    print(f"    t-stat: {t_stat:.3f}, p = {p_val:.2e}")
    if p_val < 0.05:
        print(f"    *** ACTIONABLE: CEX vol spikes predict elevated Drift activity")

# ============================================================
# 6. Summary of Actionable Findings
# ============================================================
print("\n" + "=" * 80)
print("SUMMARY: ACTIONABLE FINDINGS")
print("=" * 80)

findings = []

# Oracle divergence
if abs(np.mean(div)) > 1.0:
    findings.append(f"Oracle-CEX divergence has non-zero mean ({np.mean(div):+.2f}bps) -> systematic bias")
if 0 < r1 < 1:
    hl = -np.log(2) / np.log(r1)
    if hl < 24:
        findings.append(f"Oracle divergence is mean-reverting (half-life {hl:.1f}h) -> stat-arb on extreme divergences")

# Slippage
if abs(r_slip_vol) > 0.1:
    findings.append(f"Slippage correlates with vol (r={r_slip_vol:+.3f}) -> adjust quotes wider in vol spikes")

# FR spread
if abs(ann_drift - ann_bybit) > 1.0:
    findings.append(f"Persistent FR spread (Drift-Bybit = {ann_drift - ann_bybit:+.1f}% ann.) -> FR arbitrage")

# FR predictability
if abs(r_d2b) > 0.1 and p_d2b < 0.05:
    findings.append(f"Drift FR predicts Bybit FR (r={r_d2b:+.3f}) -> front-run CEX FR payments")
if abs(r_b2d) > 0.1 and p_b2d < 0.05:
    findings.append(f"Bybit FR predicts Drift FR (r={r_b2d:+.3f}) -> anticipate Drift FR direction")

# Volume prediction
if abs(r_cont) > 0.1:
    findings.append(f"Cross-venue volume correlation (r={r_cont:+.3f}) -> use CEX vol as flow indicator")

for i, f in enumerate(findings, 1):
    print(f"\n  {i}. {f}")

if not findings:
    print("\n  No strongly actionable findings at conventional thresholds.")

print(f"\n{'=' * 80}")
print("Analysis complete.")
print(f"{'=' * 80}")
