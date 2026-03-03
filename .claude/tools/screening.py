#!/usr/bin/env python3
"""
screening.py — Ops Monitor / Log Triage Tool

GCE サービスログを取得し、Gemini CLI でトリアージする。
重要度別に分類し、対応が必要なイベントをハイライトする。

Usage:
  # 直近ログを取得してトリアージ
  python3 .claude/tools/screening.py

  # 期間指定
  python3 .claude/tools/screening.py --since "6 hours ago"

  # stdin からログを受け取る
  cat logfile.txt | python3 .claude/tools/screening.py --stdin

  # Gemini モデル指定
  python3 .claude/tools/screening.py --model gemini-3-pro-preview

Environment:
  GCE_PASSPHRASE - SSH key passphrase (required unless --stdin)
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
LOGS_SH = PROJECT_DIR / "deploy" / "logs.sh"
ENV_FILE = PROJECT_DIR / ".env"

DEFAULT_MODEL = "gemini-3-flash-preview"

# --- Pre-screening patterns (tuned to actual Winston log messages) ---
CRITICAL_PATTERNS = [
    r"CRITICAL:",                          # rebalance.ts: fund-at-risk situations
    r"circuit breaker activated",          # scheduler.ts: max consecutive failures
    r"\[error\]",                          # Winston error level
    r"ECONNREFUSED",
    r"SIGTERM|SIGKILL",
    r"Failed to open position",            # position.ts
    r"Failed to close position",           # rebalance.ts
    r"swap failed.*Funds in wallet",       # rebalance.ts: position closed but stuck
]

IMPORTANT_PATTERNS = [
    r"Rebalance completed",                # rebalance.ts: successful rebalance
    r"Rebalance evaluation",               # rebalance.ts: trigger fired
    r"Harvest (evaluation|result)",        # compound.ts
    r"Price out of range",                 # trigger.ts
    r"Rebalance cooldown active",          # trigger.ts
    r"Daily rebalance limit",             # trigger.ts
    r"Profitability gate",                 # trigger.ts
    r"Idle (fund|deploy)",                 # rebalance.ts: idle fund operations
    r"Position opened",                    # position.ts
    r"New position detected",              # rebalance.ts
    r"Swap executed",                      # swap.ts
    r"Rebalance failed.*backing off",      # scheduler.ts
    r"\[warn\]",                           # Winston warn level
    r"STARTUP WARNING",                    # scheduler.ts
    r"Bot is paused",                      # scheduler.ts
]

NOISE_PATTERNS = [
    r"No managed positions found",         # scheduler.ts: normal when no positions
    r"Optimal range calculated",           # range.ts: every check cycle
    r"Volatility engine result",           # volatility.ts: every check
    r"Volatility.*insufficient swap events",  # volatility.ts: low activity
    r"Deposit ratio calculated",           # swap.ts: every check
    r"Balance analysis",                   # swap.ts: every check
    r"Restored .* from state\.json",       # startup messages
    r"Wallet loaded",                      # startup
    r"SuiClient initialized",             # startup
    r"Position auto-discovery",            # startup
    r"Position ID persisted",              # state.ts: routine persistence
    r"Managed position updated",           # scheduler.ts: routine tracking
    r"Next harvest scheduled",             # scheduler.ts: routine
    r"Starting scheduler",                 # startup
    r"Swap quote comparison",              # swap.ts: every swap evaluation
]


def load_env_var(name: str) -> str | None:
    """Load a variable from .env file."""
    if name in os.environ:
        return os.environ[name]
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip()
    return None


def fetch_logs(since: str = "6 hours ago") -> str:
    """Fetch logs from GCE via deploy/logs.sh."""
    passphrase = load_env_var("GCE_PASSPHRASE")
    if not passphrase:
        print("ERROR: GCE_PASSPHRASE not set", file=sys.stderr)
        sys.exit(1)

    env = os.environ.copy()
    env["GCE_PASSPHRASE"] = passphrase

    result = subprocess.run(
        ["bash", str(LOGS_SH), "--since", since],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    if result.returncode != 0:
        print(f"ERROR: logs.sh failed: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    return result.stdout


def pre_screen(logs: str) -> dict:
    """Fast local pre-screening before Gemini triage."""
    lines = logs.strip().splitlines()
    result = {
        "total_lines": len(lines),
        "critical": [],
        "important": [],
        "noise_filtered": 0,
        "filtered_log": [],
    }

    for line in lines:
        lower = line.lower()

        # Skip noise
        if any(re.search(p, line, re.IGNORECASE) for p in NOISE_PATTERNS):
            result["noise_filtered"] += 1
            continue

        # Classify
        if any(re.search(p, lower) for p in CRITICAL_PATTERNS):
            result["critical"].append(line)
            result["filtered_log"].append(line)
        elif any(re.search(p, line, re.IGNORECASE) for p in IMPORTANT_PATTERNS):
            result["important"].append(line)
            result["filtered_log"].append(line)
        else:
            result["filtered_log"].append(line)

    return result


def gemini_triage(logs: str, model: str) -> str:
    """Send filtered logs to Gemini CLI for triage."""
    prompt = """You are an operations monitor for a DeFi liquidity management bot (Cetus CLMM on Sui chain).
Analyze the following service logs and provide a triage report.

Classify each notable event as:
- 🔴 CRITICAL: Requires immediate action (errors, fund safety issues, service crashes)
- 🟡 WARNING: Needs attention soon (repeated cooldowns, high volatility, harvest failures)
- 🟢 INFO: Normal operations worth noting (successful rebalances, harvests, position changes)

Context:
- The bot manages concentrated liquidity positions on Cetus DEX
- "range-out" means price moved outside the LP position range
- "Cooldown" is a safety delay after range-out before rebalancing
- "idle deploy" uses idle wallet funds to add liquidity
- Price direction: pool price is coinB/coinA (inverse of exchange price)

Output format:
## Triage Summary
- Critical: N issues
- Warning: N issues
- Info: N events

## Events (newest first)
[emoji] [timestamp] [category] — [description]

## Recommendations
- Actionable items if any"""

    try:
        result = subprocess.run(
            ["gemini", "-m", model, "-p", prompt],
            input=logs,
            capture_output=True,
            text=True,
            timeout=120,
        )
        # Gemini CLI outputs IDE errors to stderr, ignore them
        return result.stdout.strip()
    except FileNotFoundError:
        return "(Gemini CLI not found — install with: npm i -g @anthropic-ai/gemini-cli or similar)"
    except subprocess.TimeoutExpired:
        return "(Gemini CLI timed out after 120s)"


def main():
    parser = argparse.ArgumentParser(description="Ops log screening with Gemini triage")
    parser.add_argument("--since", default="6 hours ago", help="Log time range (default: '6 hours ago')")
    parser.add_argument("--stdin", action="store_true", help="Read logs from stdin instead of GCE")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Gemini model (default: {DEFAULT_MODEL})")
    parser.add_argument("--local-only", action="store_true", help="Skip Gemini, only do local pre-screening")
    parser.add_argument("--json", action="store_true", help="Output pre-screening results as JSON")
    args = parser.parse_args()

    # Step 1: Get logs
    if args.stdin:
        logs = sys.stdin.read()
    else:
        print(f"Fetching logs (since: {args.since})...", file=sys.stderr)
        logs = fetch_logs(args.since)

    if not logs.strip():
        print("No logs found.", file=sys.stderr)
        sys.exit(0)

    # Step 2: Local pre-screening
    pre = pre_screen(logs)

    if args.json:
        print(json.dumps({
            "total_lines": pre["total_lines"],
            "critical_count": len(pre["critical"]),
            "important_count": len(pre["important"]),
            "noise_filtered": pre["noise_filtered"],
            "critical_lines": pre["critical"][:20],
            "important_lines": pre["important"][:20],
        }, indent=2, ensure_ascii=False))
        return

    # Step 3: Print local summary
    print(f"=== Pre-Screening ({pre['total_lines']} lines, {pre['noise_filtered']} noise filtered) ===")
    if pre["critical"]:
        print(f"\n🔴 Critical signals: {len(pre['critical'])}")
        for line in pre["critical"][:10]:
            print(f"  {line[:200]}")
    if pre["important"]:
        print(f"\n🟡 Important signals: {len(pre['important'])}")
        for line in pre["important"][:10]:
            print(f"  {line[:200]}")

    if args.local_only:
        return

    # Step 4: Gemini triage (send filtered log, not full noise)
    filtered = "\n".join(pre["filtered_log"][-200:])  # Last 200 non-noise lines
    print(f"\n=== Gemini Triage ({args.model}) ===\n", file=sys.stderr)
    triage = gemini_triage(filtered, args.model)
    print(triage)


if __name__ == "__main__":
    main()
