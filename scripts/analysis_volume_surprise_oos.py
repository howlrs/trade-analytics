"""
Volume Surprise -> Forward Returns: Rigorous Out-of-Sample Validation
=====================================================================
Walk-forward validation with proper train/test separation.
Tests within-asset and cross-asset predictive power.
Includes transaction cost analysis, vol-regime conditioning, and statistical tests.

Prior finding: Volume Q5 (spike) -> SOL 4h forward +10.94bp (full sample).
Objective: Determine if this survives OOS testing with honest statistics.
"""

import polars as pl
import numpy as np
from scipy import stats
from datetime import timedelta
from itertools import product
import warnings

warnings.filterwarnings("ignore")

# =============================================================================
# Configuration
# =============================================================================
SYMBOLS = ["btcusdt", "ethusdt", "solusdt"]
HOLD_PERIODS = [1, 4, 8]  # hours
TRAIN_DAYS = 90
TEST_DAYS = 30
N_QUINTILES = 5
COST_SCENARIOS = {
    "taker_2bp": 0.0002,
    "maker_0bp": 0.0,
    "drift_rebate": -0.00005,
}
BOOTSTRAP_N = 1000
BONFERRONI_TESTS = len(SYMBOLS) * len(HOLD_PERIODS) * len(COST_SCENARIOS)  # for correction

np.random.seed(42)


# =============================================================================
# Data Loading
# =============================================================================
def load_data():
    """Load all parquet files into a dict."""
    data = {}
    for sym in SYMBOLS:
        df = pl.read_parquet(f"data/binance_{sym}_1h_full.parquet")
        df = df.sort("timestamp")
        # Compute log returns
        df = df.with_columns(
            (pl.col("close") / pl.col("close").shift(1)).log().alias("log_ret"),
        )
        # Compute 24h realized vol
        df = df.with_columns(
            pl.col("log_ret").rolling_std(24).alias("rvol_24h"),
        )
        # Forward returns at various horizons
        for h in HOLD_PERIODS:
            df = df.with_columns(
                (pl.col("close").shift(-h) / pl.col("close") - 1).alias(f"fwd_ret_{h}h"),
            )
        data[sym] = df.drop_nulls(subset=["log_ret", "rvol_24h"])
    return data


# =============================================================================
# Step 1: Walk-Forward Validation
# =============================================================================
def walk_forward_validation(data: dict):
    """
    Rolling 90d train / 30d test walk-forward.
    Quintile thresholds computed on training data ONLY.
    Returns dict of results per (signal_asset, target_asset, hold_period).
    """
    results = {}

    for sig_sym in SYMBOLS:
        for tgt_sym in SYMBOLS:
            sig_df = data[sig_sym]
            tgt_df = data[tgt_sym]

            # Align timestamps
            ts_min = max(sig_df["timestamp"].min(), tgt_df["timestamp"].min())
            ts_max = min(sig_df["timestamp"].max(), tgt_df["timestamp"].max())

            sig_df = sig_df.filter(
                (pl.col("timestamp") >= ts_min) & (pl.col("timestamp") <= ts_max)
            )
            tgt_df = tgt_df.filter(
                (pl.col("timestamp") >= ts_min) & (pl.col("timestamp") <= ts_max)
            )

            # Walk-forward windows
            start = ts_min + timedelta(days=TRAIN_DAYS)
            end = ts_max - timedelta(days=TEST_DAYS)

            fold_start = start
            fold_results = {h: {q: [] for q in range(1, N_QUINTILES + 1)} for h in HOLD_PERIODS}

            n_folds = 0
            while fold_start + timedelta(days=TEST_DAYS) <= ts_max:
                train_end = fold_start
                train_start = fold_start - timedelta(days=TRAIN_DAYS)
                test_start = fold_start
                test_end = fold_start + timedelta(days=TEST_DAYS)

                # Training data: compute volume quintile thresholds
                train_sig = sig_df.filter(
                    (pl.col("timestamp") >= train_start) & (pl.col("timestamp") < train_end)
                )
                if len(train_sig) < 100:
                    fold_start += timedelta(days=TEST_DAYS)
                    continue

                vol_values = train_sig["volume"].to_numpy()
                thresholds = np.quantile(vol_values, [0.2, 0.4, 0.6, 0.8])

                # Test data: apply thresholds
                test_sig = sig_df.filter(
                    (pl.col("timestamp") >= test_start) & (pl.col("timestamp") < test_end)
                )
                test_tgt = tgt_df.filter(
                    (pl.col("timestamp") >= test_start) & (pl.col("timestamp") < test_end)
                )

                if len(test_sig) < 24 or len(test_tgt) < 24:
                    fold_start += timedelta(days=TEST_DAYS)
                    continue

                # Assign quintiles to test signal data
                test_vol = test_sig["volume"].to_numpy()
                quintiles = np.digitize(test_vol, thresholds) + 1  # 1-5

                # Join test target returns by timestamp
                test_merged = test_sig.select("timestamp").with_columns(
                    pl.Series("quintile", quintiles)
                ).join(
                    test_tgt.select(["timestamp"] + [f"fwd_ret_{h}h" for h in HOLD_PERIODS]),
                    on="timestamp",
                    how="inner",
                )

                for h in HOLD_PERIODS:
                    col = f"fwd_ret_{h}h"
                    for q in range(1, N_QUINTILES + 1):
                        rets = test_merged.filter(pl.col("quintile") == q)[col].drop_nulls().to_numpy()
                        fold_results[h][q].extend(rets.tolist())

                n_folds += 1
                fold_start += timedelta(days=TEST_DAYS)

            for h in HOLD_PERIODS:
                key = (sig_sym, tgt_sym, h)
                results[key] = {
                    "quintile_returns": {q: np.array(fold_results[h][q]) for q in range(1, N_QUINTILES + 1)},
                    "n_folds": n_folds,
                }

    return results


# =============================================================================
# Step 2: Signal Performance with Transaction Costs
# =============================================================================
def compute_signal_metrics(results: dict):
    """Compute performance metrics for Q5 (long on volume spike) strategy."""
    print("=" * 100)
    print("STEP 2: Q5 (Volume Spike) Signal — Walk-Forward OOS Performance")
    print("=" * 100)

    summary_rows = []

    for (sig_sym, tgt_sym, h), res in sorted(results.items()):
        if sig_sym != tgt_sym:
            continue  # within-asset only for Step 2

        q5_rets = res["quintile_returns"][5]
        q1_rets = res["quintile_returns"][1]
        n_folds = res["n_folds"]

        if len(q5_rets) < 30:
            continue

        mean_q5 = np.mean(q5_rets)
        std_q5 = np.std(q5_rets, ddof=1)
        n_q5 = len(q5_rets)
        mean_q1 = np.mean(q1_rets) if len(q1_rets) > 0 else np.nan

        # t-test for Q5 mean != 0
        t_stat, p_val = stats.ttest_1samp(q5_rets, 0)

        # Bootstrap 95% CI
        boot_means = []
        for _ in range(BOOTSTRAP_N):
            sample = np.random.choice(q5_rets, size=n_q5, replace=True)
            boot_means.append(np.mean(sample))
        ci_lo, ci_hi = np.percentile(boot_means, [2.5, 97.5])

        # Annualized Sharpe (assume ~8760 hours/year)
        periods_per_year = 8760 / h
        trades_per_year = periods_per_year * 0.2  # Q5 = top 20%
        sharpe_raw = (mean_q5 / std_q5) * np.sqrt(trades_per_year) if std_q5 > 0 else 0

        for cost_name, cost in COST_SCENARIOS.items():
            net_ret = mean_q5 - cost
            net_sharpe = ((mean_q5 - cost) / std_q5) * np.sqrt(trades_per_year) if std_q5 > 0 else 0

            summary_rows.append({
                "signal": sig_sym.replace("usdt", "").upper(),
                "target": tgt_sym.replace("usdt", "").upper(),
                "hold_h": h,
                "n_obs": n_q5,
                "n_folds": n_folds,
                "mean_bp": mean_q5 * 10000,
                "q1_mean_bp": mean_q1 * 10000,
                "q5_q1_spread_bp": (mean_q5 - mean_q1) * 10000,
                "std_bp": std_q5 * 10000,
                "t_stat": t_stat,
                "p_val": p_val,
                "ci_lo_bp": ci_lo * 10000,
                "ci_hi_bp": ci_hi * 10000,
                "cost_name": cost_name,
                "cost_bp": cost * 10000,
                "net_ret_bp": net_ret * 10000,
                "sharpe_raw": sharpe_raw,
                "sharpe_net": net_sharpe,
            })

    # Print
    print(f"\n{'Signal':<8} {'Hold':<6} {'Cost':<15} {'N_obs':<8} {'Folds':<7} "
          f"{'Q5 Mean(bp)':<13} {'Q1 Mean(bp)':<13} {'Spread(bp)':<12} "
          f"{'t-stat':<8} {'p-val':<10} {'CI_lo':<8} {'CI_hi':<8} "
          f"{'Net(bp)':<9} {'Sharpe_net':<10}")
    print("-" * 160)

    for r in summary_rows:
        sig_marker = ""
        if r["p_val"] < 0.05 / BONFERRONI_TESTS:
            sig_marker = " ***"
        elif r["p_val"] < 0.05:
            sig_marker = " *"

        print(f"{r['signal']:<8} {r['hold_h']:<6} {r['cost_name']:<15} {r['n_obs']:<8} {r['n_folds']:<7} "
              f"{r['mean_bp']:>+10.2f}   {r['q1_mean_bp']:>+10.2f}   {r['q5_q1_spread_bp']:>+9.2f}   "
              f"{r['t_stat']:>+6.2f}  {r['p_val']:>9.4f}{sig_marker:<5} "
              f"{r['ci_lo_bp']:>+7.2f} {r['ci_hi_bp']:>+7.2f}  "
              f"{r['net_ret_bp']:>+7.2f}   {r['sharpe_net']:>+7.3f}")

    return summary_rows


# =============================================================================
# Step 3: Cross-Asset Volume Surprise
# =============================================================================
def cross_asset_analysis(results: dict):
    """Test if BTC/ETH volume spikes predict SOL returns."""
    print("\n" + "=" * 100)
    print("STEP 3: Cross-Asset Volume Surprise (Signal Asset -> SOL Forward Returns)")
    print("=" * 100)

    print(f"\n{'Signal':<8} {'Target':<8} {'Hold':<6} {'N_obs':<8} "
          f"{'Q5 Mean(bp)':<13} {'Q1 Mean(bp)':<13} {'Spread(bp)':<12} "
          f"{'t-stat':<8} {'p-val':<10}")
    print("-" * 110)

    for (sig_sym, tgt_sym, h), res in sorted(results.items()):
        if tgt_sym != "solusdt":
            continue  # only SOL as target

        q5_rets = res["quintile_returns"][5]
        q1_rets = res["quintile_returns"][1]

        if len(q5_rets) < 30:
            continue

        mean_q5 = np.mean(q5_rets)
        mean_q1 = np.mean(q1_rets) if len(q1_rets) > 0 else np.nan

        t_stat, p_val = stats.ttest_1samp(q5_rets, 0)
        sig_marker = ""
        if p_val < 0.05 / BONFERRONI_TESTS:
            sig_marker = " ***"
        elif p_val < 0.05:
            sig_marker = " *"

        sig_label = sig_sym.replace("usdt", "").upper()
        tgt_label = tgt_sym.replace("usdt", "").upper()

        print(f"{sig_label:<8} {tgt_label:<8} {h:<6} {len(q5_rets):<8} "
              f"{mean_q5 * 10000:>+10.2f}   {mean_q1 * 10000:>+10.2f}   "
              f"{(mean_q5 - mean_q1) * 10000:>+9.2f}   "
              f"{t_stat:>+6.2f}  {p_val:>9.4f}{sig_marker}")


# =============================================================================
# Step 4: Conditional Analysis (Vol Regime)
# =============================================================================
def vol_regime_analysis(data: dict, results_wf: dict):
    """
    Re-run walk-forward but split test observations by vol regime.
    High Vol = rvol_24h > training-period median, Low Vol = below.
    """
    print("\n" + "=" * 100)
    print("STEP 4: Vol-Regime Conditional Analysis (Q5 Signal, Within-Asset)")
    print("=" * 100)

    for sym in SYMBOLS:
        df = data[sym]
        ts_min = df["timestamp"].min()
        ts_max = df["timestamp"].max()

        fold_start = ts_min + timedelta(days=TRAIN_DAYS)
        regime_results = {
            h: {"high_vol": [], "low_vol": []} for h in HOLD_PERIODS
        }

        while fold_start + timedelta(days=TEST_DAYS) <= ts_max:
            train_start = fold_start - timedelta(days=TRAIN_DAYS)
            train_end = fold_start
            test_start = fold_start
            test_end = fold_start + timedelta(days=TEST_DAYS)

            train = df.filter(
                (pl.col("timestamp") >= train_start) & (pl.col("timestamp") < train_end)
            )
            test = df.filter(
                (pl.col("timestamp") >= test_start) & (pl.col("timestamp") < test_end)
            )

            if len(train) < 100 or len(test) < 24:
                fold_start += timedelta(days=TEST_DAYS)
                continue

            # Volume quintile thresholds from training
            vol_thresholds = np.quantile(train["volume"].to_numpy(), [0.2, 0.4, 0.6, 0.8])
            # Vol regime threshold from training
            rvol_median = np.median(train["rvol_24h"].drop_nulls().to_numpy())

            test_vol = test["volume"].to_numpy()
            quintiles = np.digitize(test_vol, vol_thresholds) + 1
            rvol_vals = test["rvol_24h"].to_numpy()

            for h in HOLD_PERIODS:
                fwd = test[f"fwd_ret_{h}h"].to_numpy()
                for i in range(len(test)):
                    if quintiles[i] == 5 and not np.isnan(fwd[i]) and not np.isnan(rvol_vals[i]):
                        bucket = "high_vol" if rvol_vals[i] > rvol_median else "low_vol"
                        regime_results[h][bucket].append(fwd[i])

            fold_start += timedelta(days=TEST_DAYS)

        label = sym.replace("usdt", "").upper()
        print(f"\n--- {label} ---")
        print(f"{'Hold':<6} {'Regime':<10} {'N_obs':<8} {'Mean(bp)':<12} {'Std(bp)':<12} {'t-stat':<8} {'p-val':<10}")
        print("-" * 70)

        for h in HOLD_PERIODS:
            for regime in ["high_vol", "low_vol"]:
                rets = np.array(regime_results[h][regime])
                if len(rets) < 10:
                    print(f"{h:<6} {regime:<10} {len(rets):<8} {'insufficient data'}")
                    continue
                mean_r = np.mean(rets)
                std_r = np.std(rets, ddof=1)
                t_s, p_v = stats.ttest_1samp(rets, 0)
                sig = " *" if p_v < 0.05 else ""
                print(f"{h:<6} {regime:<10} {len(rets):<8} {mean_r * 10000:>+9.2f}    {std_r * 10000:>9.2f}    "
                      f"{t_s:>+6.2f}  {p_v:>9.4f}{sig}")


# =============================================================================
# Step 5: Statistical Significance Summary
# =============================================================================
def statistical_summary(summary_rows: list):
    """Final honest assessment with Bonferroni correction."""
    print("\n" + "=" * 100)
    print("STEP 5: Statistical Significance Summary")
    print("=" * 100)

    print(f"\nTotal tests (Bonferroni correction): {BONFERRONI_TESTS}")
    print(f"Corrected alpha: {0.05 / BONFERRONI_TESTS:.6f}")

    # Filter to maker_0bp for cleaner comparison (no cost distortion)
    base = [r for r in summary_rows if r["cost_name"] == "maker_0bp"]

    n_nominal = sum(1 for r in base if r["p_val"] < 0.05)
    n_bonferroni = sum(1 for r in base if r["p_val"] < 0.05 / BONFERRONI_TESTS)

    print(f"\nWithin-asset Q5 tests (maker_0bp cost):")
    print(f"  Nominally significant (p < 0.05): {n_nominal} / {len(base)}")
    print(f"  Bonferroni-significant (p < {0.05 / BONFERRONI_TESTS:.6f}): {n_bonferroni} / {len(base)}")

    # Best case scenario
    if base:
        best = max(base, key=lambda r: r["mean_bp"])
        print(f"\n  Best Q5 mean return: {best['signal']} {best['hold_h']}h = {best['mean_bp']:+.2f} bp "
              f"(t={best['t_stat']:+.2f}, p={best['p_val']:.4f})")
        print(f"  Bootstrap 95% CI: [{best['ci_lo_bp']:+.2f}, {best['ci_hi_bp']:+.2f}] bp")

        worst = min(base, key=lambda r: r["mean_bp"])
        print(f"  Worst Q5 mean return: {worst['signal']} {worst['hold_h']}h = {worst['mean_bp']:+.2f} bp "
              f"(t={worst['t_stat']:+.2f}, p={worst['p_val']:.4f})")

    # Honest verdict
    print("\n" + "=" * 100)
    print("VERDICT")
    print("=" * 100)

    any_survives = n_bonferroni > 0
    any_positive = any(r["mean_bp"] > 0 and r["p_val"] < 0.05 for r in base)
    any_profitable = any(r["net_ret_bp"] > 0 and r["p_val"] < 0.05
                         for r in summary_rows if r["cost_name"] == "taker_2bp")

    if any_survives:
        print("Some signals survive Bonferroni correction.")
    else:
        print("NO signal survives Bonferroni correction.")

    if any_positive:
        print("Some Q5 mean returns are nominally positive and significant (p < 0.05).")
    else:
        print("NO Q5 mean return is both positive and nominally significant.")

    if any_profitable:
        print("Some signals are profitable after 2bp taker costs.")
    else:
        print("NO signal is profitable after 2bp round-trip taker costs.")

    # Quintile monotonicity check
    print("\n--- Quintile Monotonicity Check (within-asset, all holds) ---")
    for sym in SYMBOLS:
        label = sym.replace("usdt", "").upper()
        for h in HOLD_PERIODS:
            q_means = []
            for q in range(1, 6):
                # Find from results directly — we need the raw results
                pass  # printed below from walk_forward results


def quintile_monotonicity(results: dict):
    """Check if returns increase monotonically from Q1 to Q5."""
    print("\n--- Quintile Monotonicity (Within-Asset, OOS Walk-Forward) ---")
    print(f"{'Asset':<8} {'Hold':<6} {'Q1(bp)':<10} {'Q2(bp)':<10} {'Q3(bp)':<10} {'Q4(bp)':<10} {'Q5(bp)':<10} {'Monotonic?':<12}")
    print("-" * 90)

    for sym in SYMBOLS:
        for h in HOLD_PERIODS:
            key = (sym, sym, h)
            if key not in results:
                continue
            res = results[key]
            q_means = []
            for q in range(1, 6):
                rets = res["quintile_returns"][q]
                q_means.append(np.mean(rets) * 10000 if len(rets) > 0 else np.nan)

            # Check monotonicity
            valid = [m for m in q_means if not np.isnan(m)]
            is_mono = all(valid[i] <= valid[i + 1] for i in range(len(valid) - 1)) if len(valid) >= 2 else False

            label = sym.replace("usdt", "").upper()
            print(f"{label:<8} {h:<6} "
                  f"{q_means[0]:>+8.2f}  {q_means[1]:>+8.2f}  {q_means[2]:>+8.2f}  "
                  f"{q_means[3]:>+8.2f}  {q_means[4]:>+8.2f}  "
                  f"{'YES' if is_mono else 'NO'}")


# =============================================================================
# Main
# =============================================================================
def main():
    print("Loading data...")
    data = load_data()
    for sym in SYMBOLS:
        label = sym.replace("usdt", "").upper()
        print(f"  {label}: {len(data[sym])} rows, "
              f"{data[sym]['timestamp'].min()} to {data[sym]['timestamp'].max()}")

    print(f"\nWalk-forward config: {TRAIN_DAYS}d train / {TEST_DAYS}d test, non-overlapping")
    print(f"Hold periods: {HOLD_PERIODS}h")
    print(f"Cost scenarios: {COST_SCENARIOS}")
    print(f"Bonferroni tests: {BONFERRONI_TESTS}")

    print("\nRunning walk-forward validation (this may take a moment)...")
    results = walk_forward_validation(data)

    # Step 1 output: quintile monotonicity
    print("\n" + "=" * 100)
    print("STEP 1: Walk-Forward Quintile Returns")
    print("=" * 100)
    quintile_monotonicity(results)

    # Step 2
    summary_rows = compute_signal_metrics(results)

    # Step 3
    cross_asset_analysis(results)

    # Step 4
    vol_regime_analysis(data, results)

    # Step 5
    statistical_summary(summary_rows)


if __name__ == "__main__":
    main()
