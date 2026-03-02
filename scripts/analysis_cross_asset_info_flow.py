"""
Cross-Asset Information Flow & Structural Edges Analysis
=========================================================
Analyzes BTC/ETH/SOL 1h data (2022-11 ~ 2026-03) for:
1. Time-varying Granger causality with leadership rotation
2. Volatility contagion asymmetry (upside vs downside)
3. Correlation breakdown events and forward returns
4. Cross-asset volume lead-lag relationships
"""

import polars as pl
import numpy as np
from scipy import stats
from itertools import combinations
import warnings
warnings.filterwarnings("ignore")

# ─── Data Loading ───────────────────────────────────────────────────────────

def load_data():
    assets = {"BTC": "binance_btcusdt", "ETH": "binance_ethusdt", "SOL": "binance_solusdt"}
    dfs = {}
    for name, file_prefix in assets.items():
        df = pl.read_parquet(f"data/{file_prefix}_1h_full.parquet")
        df = df.sort("timestamp").select([
            "timestamp",
            pl.col("close").alias(f"{name}_close"),
            pl.col("volume").alias(f"{name}_vol"),
            pl.col("high").alias(f"{name}_high"),
            pl.col("low").alias(f"{name}_low"),
        ])
        dfs[name] = df

    # Join all on timestamp
    merged = dfs["BTC"]
    for name in ["ETH", "SOL"]:
        merged = merged.join(dfs[name], on="timestamp", how="inner")

    # Compute log returns
    for name in ["BTC", "ETH", "SOL"]:
        merged = merged.with_columns(
            (pl.col(f"{name}_close").log() - pl.col(f"{name}_close").shift(1).log()).alias(f"{name}_ret")
        )
        # Normalized volume (z-score over rolling 168h = 1 week)
        merged = merged.with_columns(
            pl.col(f"{name}_vol").alias(f"{name}_volume_raw")
        )

    merged = merged.drop_nulls()
    print(f"Data: {merged.shape[0]} rows, {merged['timestamp'].min()} to {merged['timestamp'].max()}")
    return merged


# ─── 1. Time-Varying Granger Causality ─────────────────────────────────────

def manual_granger_ftest(y: np.ndarray, x: np.ndarray, max_lag: int):
    """
    Granger causality F-test: does x Granger-cause y?
    Restricted model: y_t = a0 + a1*y_{t-1} + ... + a_p*y_{t-p}
    Unrestricted model: y_t = a0 + a1*y_{t-1} + ... + a_p*y_{t-p} + b1*x_{t-1} + ... + b_p*x_{t-p}
    Returns F-statistic and p-value.
    """
    n = len(y)
    if n <= 2 * max_lag + 2:
        return np.nan, np.nan

    # Build lagged matrices
    Y = y[max_lag:]
    T = len(Y)

    # Restricted: only own lags
    X_r = np.ones((T, max_lag + 1))
    for lag in range(1, max_lag + 1):
        X_r[:, lag] = y[max_lag - lag: n - lag]

    # Unrestricted: own lags + other's lags
    X_u = np.ones((T, 2 * max_lag + 1))
    for lag in range(1, max_lag + 1):
        X_u[:, lag] = y[max_lag - lag: n - lag]
        X_u[:, max_lag + lag] = x[max_lag - lag: n - lag]

    try:
        # OLS for restricted
        beta_r = np.linalg.lstsq(X_r, Y, rcond=None)[0]
        resid_r = Y - X_r @ beta_r
        ssr_r = np.sum(resid_r ** 2)

        # OLS for unrestricted
        beta_u = np.linalg.lstsq(X_u, Y, rcond=None)[0]
        resid_u = Y - X_u @ beta_u
        ssr_u = np.sum(resid_u ** 2)

        # F-test
        df1 = max_lag  # number of added regressors
        df2 = T - 2 * max_lag - 1
        if df2 <= 0 or ssr_u <= 0:
            return np.nan, np.nan

        F = ((ssr_r - ssr_u) / df1) / (ssr_u / df2)
        p_value = 1 - stats.f.cdf(F, df1, df2)
        return F, p_value
    except Exception:
        return np.nan, np.nan


def analyze_granger_causality(df: pl.DataFrame):
    print("\n" + "=" * 90)
    print("1. TIME-VARYING GRANGER CAUSALITY (Rolling 30-day windows, lags 1-4h)")
    print("=" * 90)

    pairs = [("BTC", "ETH"), ("BTC", "SOL"), ("ETH", "SOL")]
    window = 24 * 30  # 30 days in hours
    step = 24 * 7     # step by 1 week

    # Full-sample results first
    print("\n--- Full-Sample Granger Causality ---")
    print(f"{'Pair':<15} {'Lag':>4} {'F-stat':>10} {'p-value':>10} {'Significant':>12}")
    print("-" * 55)

    for a, b in pairs:
        y_a = df[f"{a}_ret"].to_numpy()
        y_b = df[f"{b}_ret"].to_numpy()
        for lag in [1, 2, 3, 4]:
            f_ab, p_ab = manual_granger_ftest(y_b, y_a, lag)  # a -> b
            sig = "***" if p_ab < 0.001 else "**" if p_ab < 0.01 else "*" if p_ab < 0.05 else ""
            print(f"{a}->{b:<10} {lag:>4} {f_ab:>10.3f} {p_ab:>10.6f} {sig:>12}")

            f_ba, p_ba = manual_granger_ftest(y_a, y_b, lag)  # b -> a
            sig = "***" if p_ba < 0.001 else "**" if p_ba < 0.01 else "*" if p_ba < 0.05 else ""
            print(f"{b}->{a:<10} {lag:>4} {f_ba:>10.3f} {p_ba:>10.6f} {sig:>12}")

    # Rolling analysis - track leadership rotation
    print("\n--- Leadership Rotation Analysis (30-day rolling) ---")
    print("Leadership = which asset Granger-causes the other more strongly (lower p-value)")

    timestamps = df["timestamp"].to_numpy()

    rotation_data = {f"{a}-{b}": [] for a, b in pairs}

    for a, b in pairs:
        y_a = df[f"{a}_ret"].to_numpy()
        y_b = df[f"{b}_ret"].to_numpy()

        for start in range(0, len(y_a) - window, step):
            end = start + window
            mid_ts = timestamps[start + window // 2]

            slice_a = y_a[start:end]
            slice_b = y_b[start:end]

            # Use lag=2 as representative
            f_ab, p_ab = manual_granger_ftest(slice_b, slice_a, 2)
            f_ba, p_ba = manual_granger_ftest(slice_a, slice_b, 2)

            leader = a if p_ab < p_ba else b
            rotation_data[f"{a}-{b}"].append({
                "date": str(mid_ts)[:10],
                "f_ab": f_ab, "p_ab": p_ab,
                "f_ba": f_ba, "p_ba": p_ba,
                "leader": leader
            })

    for pair_name, records in rotation_data.items():
        a, b = pair_name.split("-")
        print(f"\n  Pair: {pair_name}")
        print(f"  {'Date':<12} {'F('+a+'->'+b+')':>12} {'p':>10} {'F('+b+'->'+a+')':>12} {'p':>10} {'Leader':>8}")
        print("  " + "-" * 70)

        leader_counts = {}
        for r in records:
            sig_ab = "*" if r["p_ab"] < 0.05 else " "
            sig_ba = "*" if r["p_ba"] < 0.05 else " "
            print(f"  {r['date']:<12} {r['f_ab']:>11.2f}{sig_ab} {r['p_ab']:>10.4f} {r['f_ba']:>11.2f}{sig_ba} {r['p_ba']:>10.4f} {r['leader']:>8}")
            leader_counts[r["leader"]] = leader_counts.get(r["leader"], 0) + 1

        total = len(records)
        print(f"\n  Leadership share: ", end="")
        for asset, count in sorted(leader_counts.items()):
            print(f"{asset}: {count}/{total} ({100*count/total:.0f}%)  ", end="")
        print()

    # Regime-dependent: split by BTC volatility regime
    print("\n--- Granger Causality by BTC Volatility Regime (lag=2) ---")
    btc_abs_ret = np.abs(df["BTC_ret"].to_numpy())
    vol_30d = np.array([np.mean(btc_abs_ret[max(0,i-720):i]) if i > 24 else np.nan for i in range(len(btc_abs_ret))])

    vol_median = np.nanmedian(vol_30d)
    high_vol_mask = vol_30d > vol_median
    low_vol_mask = (~high_vol_mask) & (~np.isnan(vol_30d))

    print(f"\n  BTC vol median (30d mean |ret|): {vol_median:.6f}")
    print(f"  High-vol periods: {np.sum(high_vol_mask)} hours, Low-vol: {np.sum(low_vol_mask)} hours")

    print(f"\n  {'Pair':<15} {'Regime':<10} {'F-stat':>10} {'p-value':>10} {'Sig':>5}")
    print("  " + "-" * 55)

    for a, b in pairs:
        y_a = df[f"{a}_ret"].to_numpy()
        y_b = df[f"{b}_ret"].to_numpy()

        for regime_name, mask in [("High-Vol", high_vol_mask), ("Low-Vol", low_vol_mask)]:
            idx = np.where(mask)[0]
            # Take contiguous blocks >= 48h for valid Granger test
            if len(idx) < 100:
                continue
            # Use full arrays but weighted by regime (simpler: just take regime slices)
            a_slice = y_a[mask]
            b_slice = y_b[mask]

            f_ab, p_ab = manual_granger_ftest(b_slice, a_slice, 2)
            f_ba, p_ba = manual_granger_ftest(a_slice, b_slice, 2)

            sig_ab = "***" if p_ab < 0.001 else "**" if p_ab < 0.01 else "*" if p_ab < 0.05 else ""
            sig_ba = "***" if p_ba < 0.001 else "**" if p_ba < 0.01 else "*" if p_ba < 0.05 else ""
            print(f"  {a}->{b:<10} {regime_name:<10} {f_ab:>10.3f} {p_ab:>10.6f} {sig_ab:>5}")
            print(f"  {b}->{a:<10} {regime_name:<10} {f_ba:>10.3f} {p_ba:>10.6f} {sig_ba:>5}")


# ─── 2. Volatility Contagion Asymmetry ─────────────────────────────────────

def analyze_vol_contagion(df: pl.DataFrame):
    print("\n\n" + "=" * 90)
    print("2. VOLATILITY CONTAGION ASYMMETRY")
    print("=" * 90)

    assets = ["BTC", "ETH", "SOL"]
    max_lag = 8

    # Absolute returns as vol proxy
    abs_rets = {a: np.abs(df[f"{a}_ret"].to_numpy()) for a in assets}
    rets = {a: df[f"{a}_ret"].to_numpy() for a in assets}

    # 2a. Full cross-correlation of absolute returns
    print("\n--- Cross-Correlation of |returns| at lags 0-8h ---")
    print(f"  Positive lag = first asset LEADS second asset")
    print(f"\n  {'Pair':<12}", end="")
    for lag in range(-max_lag, max_lag + 1):
        print(f"  {lag:>5}h", end="")
    print()
    print("  " + "-" * (12 + 7 * (2 * max_lag + 1)))

    for a, b in combinations(assets, 2):
        print(f"  {a}-{b:<8}", end="")
        for lag in range(-max_lag, max_lag + 1):
            if lag >= 0:
                x = abs_rets[a][:len(abs_rets[a]) - lag] if lag > 0 else abs_rets[a]
                y = abs_rets[b][lag:] if lag > 0 else abs_rets[b]
            else:
                x = abs_rets[a][-lag:]
                y = abs_rets[b][:len(abs_rets[b]) + lag]
            r = np.corrcoef(x, y)[0, 1]
            marker = "*" if abs(r) > 0.1 else " "
            print(f" {r:>5.3f}{marker}", end="")
        print()

    # 2b. Asymmetry: split by BTC return sign
    print("\n--- Volatility Contagion ASYMMETRY (BTC down vs up) ---")
    print("  Cross-corr of |returns| conditioned on BTC return sign")
    print("  'Down': hours where BTC return < 0 | 'Up': hours where BTC return >= 0")

    btc_ret = rets["BTC"]
    down_mask = btc_ret < 0
    up_mask = btc_ret >= 0

    for condition_name, mask in [("BTC DOWN", down_mask), ("BTC UP", up_mask)]:
        print(f"\n  Condition: {condition_name} ({np.sum(mask)} hours)")
        print(f"  {'Pair':<12}", end="")
        for lag in [0, 1, 2, 3, 4, 6, 8]:
            print(f"  {lag:>5}h", end="")
        print()
        print("  " + "-" * 55)

        for target in ["ETH", "SOL"]:
            print(f"  BTC->{target:<6}", end="")
            for lag in [0, 1, 2, 3, 4, 6, 8]:
                # BTC |ret| at t, target |ret| at t+lag, conditioned on BTC direction at t
                if lag > 0:
                    m = mask[:-lag]
                    x = abs_rets["BTC"][:-lag][m]
                    y = abs_rets[target][lag:][m]
                else:
                    m = mask
                    x = abs_rets["BTC"][m]
                    y = abs_rets[target][m]

                if len(x) < 50:
                    print(f"    n/a", end="")
                    continue
                r = np.corrcoef(x, y)[0, 1]
                marker = "*" if abs(r) > 0.1 else " "
                print(f" {r:>5.3f}{marker}", end="")
            print()

    # Asymmetry differential
    print("\n--- Contagion Asymmetry Differential (Down - Up) ---")
    print("  Positive = stronger contagion when BTC falls")
    print(f"  {'Pair':<12}", end="")
    for lag in [0, 1, 2, 3, 4, 6, 8]:
        print(f"  {lag:>5}h", end="")
    print()
    print("  " + "-" * 55)

    for target in ["ETH", "SOL"]:
        print(f"  BTC->{target:<6}", end="")
        for lag in [0, 1, 2, 3, 4, 6, 8]:
            diffs = []
            for mask_sign, sign in [(down_mask, -1), (up_mask, 1)]:
                if lag > 0:
                    m = mask_sign[:-lag]
                    x = abs_rets["BTC"][:-lag][m]
                    y = abs_rets[target][lag:][m]
                else:
                    m = mask_sign
                    x = abs_rets["BTC"][m]
                    y = abs_rets[target][m]
                r = np.corrcoef(x, y)[0, 1]
                diffs.append(r)

            diff = diffs[0] - diffs[1]  # down - up
            marker = "**" if abs(diff) > 0.05 else "*" if abs(diff) > 0.02 else " "
            print(f" {diff:>+5.3f}{marker}", end="")
        print()

    # Time-decay of contagion
    print("\n--- Volatility Shock Half-Life ---")
    print("  How quickly does a BTC vol spike propagate? (autocorr & cross-corr decay)")

    btc_abs = abs_rets["BTC"]
    # Find BTC vol spikes (>3 std)
    btc_abs_mean = np.mean(btc_abs)
    btc_abs_std = np.std(btc_abs)
    spike_threshold = btc_abs_mean + 3 * btc_abs_std
    spike_idx = np.where(btc_abs > spike_threshold)[0]
    # Remove spikes too close together (within 24h)
    filtered_spikes = []
    last = -100
    for idx in spike_idx:
        if idx - last > 24:
            filtered_spikes.append(idx)
            last = idx
    spike_idx = np.array(filtered_spikes)

    print(f"  BTC vol spike threshold: {spike_threshold:.6f} (mean + 3*std)")
    print(f"  Number of spike events: {len(spike_idx)}")
    print(f"\n  {'Asset':<8}", end="")
    for h in range(0, 13):
        print(f"  t+{h:>2}h", end="")
    print()
    print("  " + "-" * 85)

    for asset in assets:
        print(f"  {asset:<8}", end="")
        for h in range(0, 13):
            vals = []
            for idx in spike_idx:
                if idx + h < len(abs_rets[asset]):
                    vals.append(abs_rets[asset][idx + h])
            if vals:
                mean_vol = np.mean(vals)
                print(f" {mean_vol:>.5f}", end="")
            else:
                print(f"     n/a", end="")
        print()

    unconditional = {a: np.mean(abs_rets[a]) for a in assets}
    print(f"\n  Unconditional mean |ret|: ", end="")
    for a in assets:
        print(f"{a}={unconditional[a]:.5f}  ", end="")
    print()
    print("  Multiplier vs unconditional at t+0 and t+4h:")
    for asset in assets:
        t0_vals = [abs_rets[asset][idx] for idx in spike_idx if idx < len(abs_rets[asset])]
        t4_vals = [abs_rets[asset][idx + 4] for idx in spike_idx if idx + 4 < len(abs_rets[asset])]
        t0_mult = np.mean(t0_vals) / unconditional[asset]
        t4_mult = np.mean(t4_vals) / unconditional[asset]
        print(f"    {asset}: t+0 = {t0_mult:.2f}x, t+4h = {t4_mult:.2f}x")


# ─── 3. Correlation Breakdown Events ───────────────────────────────────────

def analyze_correlation_breakdown(df: pl.DataFrame):
    print("\n\n" + "=" * 90)
    print("3. CORRELATION BREAKDOWN EVENTS & FORWARD RETURNS")
    print("=" * 90)

    window = 24  # 24h rolling correlation
    pairs = [("BTC", "ETH"), ("BTC", "SOL")]

    rets = {a: df[f"{a}_ret"].to_numpy() for a in ["BTC", "ETH", "SOL"]}
    timestamps = df["timestamp"].to_numpy()

    for a, b in pairs:
        print(f"\n--- Pair: {a}-{b} ---")

        # Compute rolling correlation
        r_a = rets[a]
        r_b = rets[b]
        n = len(r_a)

        rolling_corr = np.full(n, np.nan)
        for i in range(window, n):
            rolling_corr[i] = np.corrcoef(r_a[i - window:i], r_b[i - window:i])[0, 1]

        valid_mask = ~np.isnan(rolling_corr)
        valid_corr = rolling_corr[valid_mask]

        print(f"  Rolling {window}h correlation stats:")
        print(f"    Mean: {np.mean(valid_corr):.4f}")
        print(f"    Std:  {np.std(valid_corr):.4f}")
        print(f"    Min:  {np.min(valid_corr):.4f}")
        print(f"    5th percentile: {np.percentile(valid_corr, 5):.4f}")
        print(f"    Median: {np.median(valid_corr):.4f}")

        # Identify decorrelation events (corr < 0.3)
        decorr_threshold = 0.3
        decorr_mask = rolling_corr < decorr_threshold
        decorr_idx = np.where(decorr_mask)[0]

        # Cluster events (within 48h = same event)
        events = []
        last_end = -100
        for idx in decorr_idx:
            if idx - last_end > 48:
                events.append({"start": idx, "end": idx, "min_corr": rolling_corr[idx]})
            else:
                events[-1]["end"] = idx
                events[-1]["min_corr"] = min(events[-1]["min_corr"], rolling_corr[idx])
            last_end = idx

        print(f"\n  Decorrelation events (corr < {decorr_threshold}): {len(events)}")

        # Negative correlation events
        neg_events = [e for e in events if e["min_corr"] < 0]
        print(f"  Negative correlation events: {len(neg_events)}")

        # Forward returns after decorrelation
        horizons = [4, 8, 12, 24, 48, 72]
        print(f"\n  Forward returns AFTER decorrelation event ends:")
        print(f"  (Cumulative return over horizon, starting from event end)")
        print(f"\n  {'Horizon':<10} {'Mean '+a:>12} {'Mean '+b:>12} {'Mean Spread':>14} {'Spread SR':>10} {'Corr Recovery':>15} {'N':>5}")
        print("  " + "-" * 80)

        for h in horizons:
            fwd_a, fwd_b, fwd_corr = [], [], []
            for e in events:
                end_idx = e["end"]
                if end_idx + h < n:
                    cum_a = np.sum(r_a[end_idx + 1:end_idx + 1 + h])
                    cum_b = np.sum(r_b[end_idx + 1:end_idx + 1 + h])
                    fwd_a.append(cum_a)
                    fwd_b.append(cum_b)
                    if end_idx + h < len(rolling_corr) and not np.isnan(rolling_corr[end_idx + h]):
                        fwd_corr.append(rolling_corr[end_idx + h])

            if len(fwd_a) < 5:
                continue

            mean_a = np.mean(fwd_a) * 100
            mean_b = np.mean(fwd_b) * 100
            spread = np.array(fwd_a) - np.array(fwd_b)
            mean_spread = np.mean(spread) * 100
            spread_sr = np.mean(spread) / np.std(spread) if np.std(spread) > 0 else 0
            mean_corr_recovery = np.mean(fwd_corr) if fwd_corr else np.nan

            sig = " **" if abs(spread_sr) > 0.3 else " *" if abs(spread_sr) > 0.15 else ""
            print(f"  {h:>4}h     {mean_a:>+11.3f}% {mean_b:>+11.3f}% {mean_spread:>+13.3f}% {spread_sr:>9.3f}{sig} {mean_corr_recovery:>14.3f} {len(fwd_a):>5}")

        # Correlation regime-dependent returns
        print(f"\n  Returns by Correlation Regime ({a}-{b}):")
        print(f"  {'Regime':<25} {'Mean '+a+' ret':>14} {'Mean '+b+' ret':>14} {'Vol '+a:>10} {'Vol '+b:>10} {'N':>8}")
        print("  " + "-" * 85)

        regimes = [
            ("Decorrelated (<0.3)", lambda c: c < 0.3),
            ("Low Corr (0.3-0.6)", lambda c: (c >= 0.3) & (c < 0.6)),
            ("Normal Corr (0.6-0.8)", lambda c: (c >= 0.6) & (c < 0.8)),
            ("High Corr (>0.8)", lambda c: c >= 0.8),
            ("Negative Corr (<0)", lambda c: c < 0),
        ]

        for regime_name, cond in regimes:
            mask = cond(rolling_corr) & ~np.isnan(rolling_corr)
            if np.sum(mask) < 10:
                continue
            mean_ra = np.mean(r_a[mask]) * 100
            mean_rb = np.mean(r_b[mask]) * 100
            vol_ra = np.std(r_a[mask]) * 100
            vol_rb = np.std(r_b[mask]) * 100
            print(f"  {regime_name:<25} {mean_ra:>+13.5f}% {mean_rb:>+13.5f}% {vol_ra:>9.4f}% {vol_rb:>9.4f}% {np.sum(mask):>8}")

        # Key insight: correlation mean-reversion speed
        print(f"\n  Correlation Mean-Reversion After Breakdown:")
        # After corr drops below 0.3, how fast does it recover?
        recovery_times = []
        for e in events:
            end_idx = e["end"]
            for fwd in range(1, 200):
                if end_idx + fwd < n and not np.isnan(rolling_corr[end_idx + fwd]):
                    if rolling_corr[end_idx + fwd] > 0.6:
                        recovery_times.append(fwd)
                        break

        if recovery_times:
            print(f"    Median recovery time to corr>0.6: {np.median(recovery_times):.0f} hours")
            print(f"    Mean recovery time: {np.mean(recovery_times):.1f} hours")
            print(f"    25th/75th pctl: {np.percentile(recovery_times, 25):.0f}h / {np.percentile(recovery_times, 75):.0f}h")
            print(f"    Events recovering: {len(recovery_times)}/{len(events)} ({100*len(recovery_times)/len(events):.0f}%)")


# ─── 4. Volume Lead-Lag ────────────────────────────────────────────────────

def analyze_volume_leadlag(df: pl.DataFrame):
    print("\n\n" + "=" * 90)
    print("4. CROSS-ASSET VOLUME LEAD-LAG RELATIONSHIPS")
    print("=" * 90)

    assets = ["BTC", "ETH", "SOL"]
    rets = {a: df[f"{a}_ret"].to_numpy() for a in assets}
    vols = {}

    # Z-score normalize volume (rolling 168h)
    for a in assets:
        raw = df[f"{a}_vol"].to_numpy()
        # Simple z-score
        mean_v = np.mean(raw)
        std_v = np.std(raw)
        vols[a] = (raw - mean_v) / std_v

    max_lag = 8

    # 4a. Cross-asset: volume of X -> returns of Y
    print("\n--- Cross-Asset Volume -> Returns Correlation ---")
    print("  Corr(Volume_X at t, Return_Y at t+lag)")
    print(f"\n  {'Vol->Ret Pair':<18}", end="")
    for lag in range(0, max_lag + 1):
        print(f"  t+{lag}h", end="")
    print(f"  {'Peak':>8}")
    print("  " + "-" * 80)

    for vol_asset in assets:
        for ret_asset in assets:
            print(f"  Vol_{vol_asset}->Ret_{ret_asset}  ", end="")
            corrs = []
            for lag in range(0, max_lag + 1):
                if lag > 0:
                    v = vols[vol_asset][:-lag]
                    r = rets[ret_asset][lag:]
                else:
                    v = vols[vol_asset]
                    r = rets[ret_asset]
                c = np.corrcoef(v, r)[0, 1]
                corrs.append(c)
                marker = "*" if abs(c) > 0.03 else " "
                print(f"{c:>+.4f}{marker}", end="")

            peak_lag = np.argmax(np.abs(corrs))
            peak_val = corrs[peak_lag]
            print(f"  {peak_lag}h:{peak_val:>+.4f}")

    # 4b. Volume of X -> |returns| of Y (vol predicting volatility)
    print("\n--- Cross-Asset Volume -> Absolute Returns (Volatility Prediction) ---")
    print("  Corr(Volume_X at t, |Return_Y| at t+lag)")
    print(f"\n  {'Vol->|Ret| Pair':<18}", end="")
    for lag in range(0, max_lag + 1):
        print(f"  t+{lag}h", end="")
    print(f"  {'Peak':>8}")
    print("  " + "-" * 80)

    abs_rets = {a: np.abs(rets[a]) for a in assets}

    significant_findings = []

    for vol_asset in assets:
        for ret_asset in assets:
            print(f"  Vol_{vol_asset}->|{ret_asset}|   ", end="")
            corrs = []
            for lag in range(0, max_lag + 1):
                if lag > 0:
                    v = vols[vol_asset][:-lag]
                    r = abs_rets[ret_asset][lag:]
                else:
                    v = vols[vol_asset]
                    r = abs_rets[ret_asset]
                c = np.corrcoef(v, r)[0, 1]
                corrs.append(c)
                marker = "*" if abs(c) > 0.1 else " "
                print(f"{c:>+.4f}{marker}", end="")

                if abs(c) > 0.1 and lag > 0:
                    significant_findings.append((vol_asset, ret_asset, lag, c))

            peak_lag = np.argmax(np.abs(corrs))
            peak_val = corrs[peak_lag]
            print(f"  {peak_lag}h:{peak_val:>+.4f}")

    # 4c. Cross vs within-asset comparison
    print("\n--- Cross-Asset vs Within-Asset Volume->Return Predictability ---")
    print("  Comparing information content at lag=1h")
    print(f"\n  {'Relationship':<25} {'Corr(vol,ret)':>14} {'Corr(vol,|ret|)':>16} {'t-stat ret':>11} {'p-value':>10}")
    print("  " + "-" * 80)

    for vol_asset in assets:
        for ret_asset in assets:
            v = vols[vol_asset][:-1]
            r = rets[ret_asset][1:]
            ar = abs_rets[ret_asset][1:]

            c_ret = np.corrcoef(v, r)[0, 1]
            c_abs = np.corrcoef(v, ar)[0, 1]

            # t-test for correlation significance
            n = len(v)
            t_stat = c_ret * np.sqrt(n - 2) / np.sqrt(1 - c_ret**2)
            p_val = 2 * (1 - stats.t.cdf(abs(t_stat), n - 2))

            label = f"Vol_{vol_asset}->Ret_{ret_asset}"
            is_cross = vol_asset != ret_asset
            tag = " (CROSS)" if is_cross else " (SELF)"
            sig = "***" if p_val < 0.001 else "**" if p_val < 0.01 else "*" if p_val < 0.05 else ""

            print(f"  {label + tag:<25} {c_ret:>+13.5f} {c_abs:>+15.5f} {t_stat:>+10.3f} {p_val:>10.6f} {sig}")

    # 4d. Volume surprise -> forward returns
    print("\n--- Volume Surprise -> Forward Returns ---")
    print("  Volume surprise = z-score of current volume vs 7-day rolling mean")
    print("  Quintile analysis: mean forward return by volume surprise quintile")

    for vol_asset in assets:
        raw_vol = df[f"{vol_asset}_vol"].to_numpy()
        # Rolling 168h mean/std
        vol_surprise = np.full(len(raw_vol), np.nan)
        for i in range(168, len(raw_vol)):
            window_vol = raw_vol[i-168:i]
            vol_surprise[i] = (raw_vol[i] - np.mean(window_vol)) / np.std(window_vol)

        valid = ~np.isnan(vol_surprise)
        vs = vol_surprise[valid]
        quintiles = np.percentile(vs, [20, 40, 60, 80])

        print(f"\n  Volume source: {vol_asset}")
        print(f"  {'Quintile':<12}", end="")
        for ret_asset in assets:
            print(f" {'Fwd1h_'+ret_asset:>12} {'Fwd4h_'+ret_asset:>12}", end="")
        print()
        print("  " + "-" * 90)

        for q, (lo, hi, name) in enumerate([
            (-np.inf, quintiles[0], "Q1 (Low)"),
            (quintiles[0], quintiles[1], "Q2"),
            (quintiles[1], quintiles[2], "Q3"),
            (quintiles[2], quintiles[3], "Q4"),
            (quintiles[3], np.inf, "Q5 (High)"),
        ]):
            full_mask = valid.copy()
            full_mask[valid] = (vs >= lo) & (vs < hi) if hi < np.inf else (vs >= lo)
            full_mask[valid] &= (vs >= lo)

            # Simpler: get indices
            idx_valid = np.where(valid)[0]
            q_mask = (vs >= lo) & (vs < hi if hi != np.inf else True)
            q_idx = idx_valid[q_mask]

            print(f"  {name:<12}", end="")
            for ret_asset in assets:
                r_arr = rets[ret_asset]
                fwd1 = [r_arr[i + 1] for i in q_idx if i + 1 < len(r_arr)]
                fwd4 = [np.sum(r_arr[i + 1:i + 5]) for i in q_idx if i + 4 < len(r_arr)]
                m1 = np.mean(fwd1) * 10000 if fwd1 else 0  # in bps
                m4 = np.mean(fwd4) * 10000 if fwd4 else 0
                print(f" {m1:>+11.2f}bp {m4:>+11.2f}bp", end="")
            print()


# ─── 5. Summary & Actionable Findings ──────────────────────────────────────

def print_summary():
    print("\n\n" + "=" * 90)
    print("5. SUMMARY OF KEY FINDINGS & POTENTIAL EDGES")
    print("=" * 90)
    print("""
    Review the tables above for findings marked with * (significant).

    Key questions answered:
    1. GRANGER CAUSALITY: Does information flow reverse in different regimes?
       -> Compare High-Vol vs Low-Vol Granger results above.
       -> Leadership rotation table shows which asset leads over time.

    2. VOLATILITY CONTAGION: Is downside contagion stronger?
       -> Asymmetry differential table: positive = stronger contagion when BTC falls.
       -> Shock half-life table shows propagation speed.

    3. CORRELATION BREAKDOWN: Is there a tradeable pattern?
       -> Forward returns after decorrelation events.
       -> Correlation mean-reversion speed.
       -> If spread SR > 0.3, the pattern may be exploitable.

    4. VOLUME LEAD-LAG: Does cross-asset volume predict returns?
       -> Compare CROSS vs SELF volume->return correlations.
       -> Volume surprise quintile analysis shows monotonic return patterns (if any).

    Significance markers:
      * p < 0.05 or |r| > 0.1
     ** p < 0.01 or notable effect
    *** p < 0.001
    """)


# ─── Main ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Cross-Asset Information Flow & Structural Edges Analysis")
    print("BTC / ETH / SOL - Binance 1h - 2022-11 to 2026-03")
    print("=" * 90)

    df = load_data()

    analyze_granger_causality(df)
    analyze_vol_contagion(df)
    analyze_correlation_breakdown(df)
    analyze_volume_leadlag(df)
    print_summary()
