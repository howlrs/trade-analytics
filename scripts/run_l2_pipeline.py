#!/usr/bin/env python3
"""L2 analysis pipeline: sync → convert → analyze → backtest → report.

Usage:
    python scripts/run_l2_pipeline.py           # Full run (sync from GCS)
    python scripts/run_l2_pipeline.py --skip-sync  # Skip GCS sync (reuse local data)
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

# Repo root
REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
GCE_DIR = DATA_DIR / "drift-gce"
L2_PARQUET = DATA_DIR / "drift_solusdc_l2_snapshots.parquet"
GCS_BUCKET = "gs://crypto-bitflyer-drift-data"

# Add paths for imports
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "systems" / "drift-mm"))

from src.l2_analysis import (  # noqa: E402
    load_l2_data,
    load_trades,
    load_candles_and_fr,
    compute_spread_distribution,
    compute_oracle_divergence_dynamics,
    compute_book_shape,
    estimate_fill_probability,
    measure_adverse_selection,
    recommend_parameters,
)
from l2_backtester import L2PaperTrader, L2MMConfig  # noqa: E402


def parse_args():
    parser = argparse.ArgumentParser(description="L2 analysis pipeline")
    parser.add_argument("--skip-sync", action="store_true", help="Skip GCS sync")
    parser.add_argument("--skip-backtest", action="store_true", help="Skip backtest")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Step 1: GCS Sync
# ---------------------------------------------------------------------------

def sync_from_gcs():
    print("=" * 60)
    print("STEP 1: Syncing from GCS")
    print("=" * 60)
    GCE_DIR.mkdir(parents=True, exist_ok=True)
    cmd = ["gsutil", "-m", "rsync", "-r", f"{GCS_BUCKET}/", str(GCE_DIR) + "/"]
    print(f"  Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  Warning: gsutil sync failed: {result.stderr}")
    else:
        print(f"  Sync complete")


# ---------------------------------------------------------------------------
# Step 2: JSONL → Parquet conversion
# ---------------------------------------------------------------------------

def convert_jsonl():
    print("\n" + "=" * 60)
    print("STEP 2: Converting JSONL → Parquet")
    print("=" * 60)
    converter = REPO_ROOT / "systems" / "drift-data-collector" / "convert_jsonl_to_parquet.py"
    cmd = [
        sys.executable, str(converter),
        "--input", str(GCE_DIR),
        "--output", str(L2_PARQUET),
        "--append",
    ]
    print(f"  Running converter...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    print(result.stdout)
    if result.returncode != 0:
        print(f"  Warning: conversion issues: {result.stderr}")


# ---------------------------------------------------------------------------
# Step 3: L2 Analysis
# ---------------------------------------------------------------------------

def run_analysis() -> dict:
    print("\n" + "=" * 60)
    print("STEP 3: Running L2 Analysis")
    print("=" * 60)

    print("  Loading L2 data...")
    l2 = load_l2_data(DATA_DIR)
    print(f"  L2 snapshots: {len(l2):,} rows")
    print(f"  Period: {l2['timestamp'].min()} → {l2['timestamp'].max()}")

    print("  Loading trades...")
    trades = load_trades(DATA_DIR)
    print(f"  Trades: {len(trades):,} rows")

    print("\n  Computing spread distribution...")
    spread = compute_spread_distribution(l2)
    s = spread["overall"]
    print(f"    Mean: {s['mean']:.1f} bp, Median: {s['median']:.1f} bp, "
          f"Std: {s['std']:.1f} bp")

    print("  Computing oracle divergence...")
    divergence = compute_oracle_divergence_dynamics(l2)
    d = divergence["distribution"]
    print(f"    Mean div: {d['mean']:.2f} bp, |div|: {d['abs_mean']:.2f} bp, "
          f"Half-life: {divergence['half_life_seconds']}s")

    print("  Computing book shape...")
    book = compute_book_shape(l2)
    ss = book["source_shares"]
    print(f"    Bid vAMM: {ss.get('bid_vamm_pct', 0):.1f}%, "
          f"Ask vAMM: {ss.get('ask_vamm_pct', 0):.1f}%")

    print("  Estimating fill probability...")
    fill = estimate_fill_probability(l2, trades)
    print(f"    Overall fill rate (1min): {fill['overall_fill_rate']:.1%}")

    print("  Measuring adverse selection...")
    adverse = measure_adverse_selection(l2, trades)
    for h in adverse["by_horizon"]:
        print(f"    {h['horizon_s']:>4}s: mean={h['mean_move_bp']:+.2f} bp, "
              f"median={h['median_move_bp']:+.2f} bp")

    print("\n  Generating parameter recommendations...")
    rec = recommend_parameters(spread, divergence, book, fill, adverse)
    for r in rec["reasoning"]:
        print(f"    → {r}")

    return {
        "l2": l2,
        "trades": trades,
        "spread": spread,
        "divergence": divergence,
        "book": book,
        "fill": fill,
        "adverse": adverse,
        "recommendations": rec,
    }


# ---------------------------------------------------------------------------
# Step 4: Backtest
# ---------------------------------------------------------------------------

def run_backtest(analysis: dict) -> dict:
    print("\n" + "=" * 60)
    print("STEP 4: Running L2 Backtest")
    print("=" * 60)

    l2 = analysis["l2"]
    trades = analysis["trades"]
    rec = analysis["recommendations"]

    # Load FR
    try:
        _, fr = load_candles_and_fr(DATA_DIR)
    except FileNotFoundError:
        fr = pl.DataFrame()

    # Resample L2
    print("  Resampling L2 to 1-min bars...")
    l2_1m = L2PaperTrader.resample_l2(l2, seconds=60)
    print(f"  Resampled: {len(l2_1m):,} bars")

    configs = [
        ("Recommended", L2MMConfig(
            gamma=rec["gamma"],
            inv_limit=rec["max_inventory"],
            active_start=rec["active_start"],
            active_end=rec["active_end"],
            oracle_div_alpha=rec["oracle_div_alpha"],
            adverse_selection_guard_bps=rec["half_spread_bps"] * 0.3,
        )),
        ("Baseline (no oracle div)", L2MMConfig(
            gamma=rec["gamma"],
            inv_limit=rec["max_inventory"],
            oracle_div_alpha=0.0,
            adverse_selection_guard_bps=0.0,
        )),
        ("Conservative", L2MMConfig(
            gamma=0.2,
            inv_limit=3,
            adverse_selection_guard_bps=3.0,
        )),
    ]

    results = {}
    print(f"\n  {'Config':<30} {'Sharpe':>7} {'PnL($)':>9} {'Fills':>6} "
          f"{'BidF%':>6} {'AskF%':>6} {'Sprd':>6}")
    print("  " + "-" * 80)

    for name, cfg in configs:
        trader = L2PaperTrader(cfg)
        metrics = trader.run(l2_1m, trades, fr)
        results[name] = metrics
        print(f"  {name:<30} {metrics['sharpe']:>7.2f} {metrics['total_pnl']:>9.2f} "
              f"{metrics['n_fills']:>6} {metrics['fill_rate_bid']*100:>5.1f}% "
              f"{metrics['fill_rate_ask']*100:>5.1f}% {metrics['avg_spread_bps']:>5.1f}")

    return results


# ---------------------------------------------------------------------------
# Step 5: Report
# ---------------------------------------------------------------------------

def write_report(analysis: dict, backtest: dict | None):
    now = datetime.now(timezone.utc).strftime("%Y%m%d")
    report_path = DATA_DIR / f"l2_pipeline_report_{now}.txt"

    lines = []
    lines.append("=" * 70)
    lines.append("L2 ANALYSIS PIPELINE REPORT")
    lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    lines.append("=" * 70)

    # 1. Data Overview
    l2 = analysis["l2"]
    trades = analysis["trades"]
    lines.append("\n1. DATA OVERVIEW")
    lines.append("-" * 40)
    lines.append(f"  L2 snapshots:  {len(l2):>10,}")
    lines.append(f"  Trades:        {len(trades):>10,}")
    lines.append(f"  L2 period:     {l2['timestamp'].min()} → {l2['timestamp'].max()}")
    lines.append(f"  Trade period:  {trades['timestamp'].min()} → {trades['timestamp'].max()}")

    # 2. Spread Analysis
    s = analysis["spread"]["overall"]
    lines.append("\n2. SPREAD ANALYSIS")
    lines.append("-" * 40)
    lines.append(f"  Mean:   {s['mean']:.2f} bp")
    lines.append(f"  Median: {s['median']:.2f} bp")
    lines.append(f"  Std:    {s['std']:.2f} bp")
    lines.append(f"  P5-P95: {s['p5']:.2f} - {s['p95']:.2f} bp")

    by_regime = analysis["spread"]["by_regime"]
    if not by_regime.is_empty():
        lines.append("\n  By Regime:")
        for row in by_regime.iter_rows(named=True):
            lines.append(f"    {row['regime']:<6}: mean={row['mean_spread']:.2f} bp, "
                        f"median={row['median_spread']:.2f} bp (n={row['count']})")

    # 3. Oracle Divergence
    d = analysis["divergence"]["distribution"]
    lines.append("\n3. ORACLE DIVERGENCE")
    lines.append("-" * 40)
    lines.append(f"  Mean:      {d['mean']:.3f} bp")
    lines.append(f"  Std:       {d['std']:.3f} bp")
    lines.append(f"  |Mean|:    {d['abs_mean']:.3f} bp")
    lines.append(f"  Half-life: {analysis['divergence']['half_life_seconds']}s")
    lines.append("\n  ACF:")
    for lag_s, r in analysis["divergence"]["acf"]:
        lines.append(f"    {lag_s:>5}s: {r:.4f}")

    # 4. Book Shape
    lines.append("\n4. BOOK SHAPE")
    lines.append("-" * 40)
    ss = analysis["book"]["source_shares"]
    lines.append(f"  Bid vAMM: {ss.get('bid_vamm_pct', 0):.1f}%")
    lines.append(f"  Ask vAMM: {ss.get('ask_vamm_pct', 0):.1f}%")

    dbl = analysis["book"]["depth_by_level"]
    if not dbl.is_empty():
        lines.append("\n  Depth by Level:")
        for row in dbl.iter_rows(named=True):
            lines.append(f"    L{row['level']}: bid={row['mean_bid_size']:.2f} SOL, "
                        f"ask={row['mean_ask_size']:.2f} SOL")

    # 5. Fill Probability
    lines.append("\n5. FILL PROBABILITY")
    lines.append("-" * 40)
    lines.append(f"  Overall (1min): {analysis['fill']['overall_fill_rate']:.1%}")

    by_spread = analysis["fill"]["by_spread_bucket"]
    if not isinstance(by_spread, pl.DataFrame) or not by_spread.is_empty():
        lines.append("\n  By Spread:")
        for row in by_spread.iter_rows(named=True):
            lines.append(f"    {row['spread_bucket']:<8}: {row['fill_rate']:.1%} (n={row['count']})")

    # 6. Adverse Selection
    lines.append("\n6. ADVERSE SELECTION")
    lines.append("-" * 40)
    for h in analysis["adverse"]["by_horizon"]:
        lines.append(f"  {h['horizon_s']:>4}s: mean={h['mean_move_bp']:+.2f} bp, "
                    f"median={h['median_move_bp']:+.2f} bp (n={h['count']})")

    # 7. Backtest Results
    if backtest:
        lines.append("\n7. BACKTEST RESULTS")
        lines.append("-" * 40)
        for name, m in backtest.items():
            lines.append(f"\n  {name}:")
            lines.append(f"    Sharpe:     {m['sharpe']:.2f}")
            lines.append(f"    Total PnL:  ${m['total_pnl']:.2f} ({m['total_pnl_bps']:.0f} bps)")
            lines.append(f"    Max DD:     ${m['max_dd']:.2f}")
            lines.append(f"    Fills:      {m['n_fills']} (bid={m['n_bid_fills']}, ask={m['n_ask_fills']})")
            lines.append(f"    Avg Spread: {m['avg_spread_bps']:.1f} bps")
            lines.append(f"    FR Earnings: ${m['fr_earnings']:.2f}")

    # 8. Recommended Parameters
    rec = analysis["recommendations"]
    lines.append("\n8. RECOMMENDED PARAMETERS")
    lines.append("-" * 40)
    lines.append(f"  half_spread_bps:   {rec['half_spread_bps']:.1f}")
    lines.append(f"  gamma:             {rec['gamma']}")
    lines.append(f"  active_hours:      {rec['active_start']}-{rec['active_end']} UTC")
    lines.append(f"  max_inventory:     {rec['max_inventory']}")
    lines.append(f"  oracle_div_alpha:  {rec['oracle_div_alpha']}")
    lines.append("\n  Reasoning:")
    for r in rec["reasoning"]:
        lines.append(f"    → {r}")

    report = "\n".join(lines) + "\n"

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report)
    print(f"\nReport written to: {report_path}")
    print(report)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    # Step 1: Sync
    if not args.skip_sync:
        sync_from_gcs()
    else:
        print("Skipping GCS sync (--skip-sync)")

    # Step 2: Convert
    convert_jsonl()

    # Step 3: Analysis
    analysis = run_analysis()

    # Step 4: Backtest
    backtest = None
    if not args.skip_backtest:
        backtest = run_backtest(analysis)

    # Step 5: Report
    # Remove large DataFrames before report (they're in the dicts already)
    write_report(analysis, backtest)


if __name__ == "__main__":
    main()
