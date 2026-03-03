#!/usr/bin/env python3
"""
watcher.py — Log Pattern Watcher

サービスログを定期的に監視し、重要パターンを検知したら通知する。
Discord webhook、stdout、またはファイル出力に対応。

Usage:
  # ワンショット: 直近ログをチェックして通知
  python3 .claude/tools/watcher.py --once

  # デーモン: 5分間隔で継続監視
  python3 .claude/tools/watcher.py --interval 300

  # Discord webhook 通知
  python3 .claude/tools/watcher.py --once --discord-webhook $DISCORD_WEBHOOK_URL

  # 通知をファイルに追記
  python3 .claude/tools/watcher.py --once --output /tmp/watcher-alerts.jsonl

Environment:
  GCE_PASSPHRASE      - SSH key passphrase (required)
  DISCORD_WEBHOOK_URL  - Discord webhook URL (optional, --discord-webhook で上書き可)
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
LOGS_SH = PROJECT_DIR / "deploy" / "logs.sh"
ENV_FILE = PROJECT_DIR / ".env"
STATE_FILE = PROJECT_DIR / ".claude/tools/.watcher-state.json"

# --- Alert rules ---
RULES = [
    # --- Critical: requires immediate attention ---
    {
        "name": "critical_fund_risk",
        "severity": "critical",
        "pattern": r"CRITICAL:.*[Ff]unds in wallet|CRITICAL:.*failed to open|CRITICAL:.*also failed",
        "description": "Fund safety issue — position closed but new one not opened",
    },
    {
        "name": "circuit_breaker",
        "severity": "critical",
        "pattern": r"circuit breaker activated",
        "description": "Max consecutive failures — circuit breaker activated",
    },
    {
        "name": "service_error",
        "severity": "critical",
        "pattern": r"\[error\]",
        "exclude": r"(Event log stream error.*falling back|Failed to fetch position rewards)",
        "description": "Service error logged",
    },
    # --- Warning: needs attention soon ---
    {
        "name": "range_out",
        "severity": "warning",
        "pattern": r"Price out of range",
        "description": "Position is out of range",
    },
    {
        "name": "rebalance_backoff",
        "severity": "warning",
        "pattern": r"Rebalance failed.*backing off",
        "description": "Rebalance failed, backing off",
    },
    {
        "name": "daily_limit",
        "severity": "warning",
        "pattern": r"Daily rebalance limit reached",
        "description": "Daily rebalance limit reached",
    },
    {
        "name": "paused",
        "severity": "warning",
        "pattern": r"Bot is paused",
        "description": "Bot is paused (PAUSED=true)",
    },
    {
        "name": "startup_warning",
        "severity": "warning",
        "pattern": r"STARTUP WARNING",
        "description": "Startup validation warning",
    },
    # --- Info: normal operations worth tracking ---
    {
        "name": "rebalance_completed",
        "severity": "info",
        "pattern": r"Rebalance completed",
        "description": "Rebalance executed successfully",
    },
    {
        "name": "harvest",
        "severity": "info",
        "pattern": r"Harvest result",
        "description": "Fee/reward harvest executed",
    },
    {
        "name": "new_position",
        "severity": "info",
        "pattern": r"New position detected|Position opened",
        "description": "New position created",
    },
    {
        "name": "service_lifecycle",
        "severity": "info",
        "pattern": r"Sui Auto LP starting|Shutting down|SIGTERM",
        "description": "Service start/stop",
    },
    {
        "name": "profitability_gate",
        "severity": "info",
        "pattern": r"Profitability gate",
        "description": "Profitability gate evaluation",
    },
]


def load_env_var(name: str) -> str | None:
    if name in os.environ:
        return os.environ[name]
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip()
    return None


def fetch_logs(since: str = "10 minutes ago") -> str:
    passphrase = load_env_var("GCE_PASSPHRASE")
    if not passphrase:
        print("ERROR: GCE_PASSPHRASE not set", file=sys.stderr)
        return ""

    env = os.environ.copy()
    env["GCE_PASSPHRASE"] = passphrase

    try:
        result = subprocess.run(
            ["bash", str(LOGS_SH), "--since", since],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
        return result.stdout if result.returncode == 0 else ""
    except (subprocess.TimeoutExpired, Exception) as e:
        print(f"ERROR fetching logs: {e}", file=sys.stderr)
        return ""


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"last_check": None, "seen_hashes": []}


def save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def check_rules(logs: str) -> list[dict]:
    """Check log lines against all rules, return alerts."""
    alerts = []
    lines = logs.strip().splitlines()

    for line in lines:
        for rule in RULES:
            if re.search(rule["pattern"], line, re.IGNORECASE):
                # Check exclusion pattern
                if rule.get("exclude") and re.search(rule["exclude"], line, re.IGNORECASE):
                    continue

                alerts.append({
                    "rule": rule["name"],
                    "severity": rule["severity"],
                    "description": rule["description"],
                    "line": line[:500],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

    return alerts


def deduplicate(alerts: list[dict], state: dict) -> list[dict]:
    """Remove alerts we've already seen (by line hash)."""
    seen = set(state.get("seen_hashes", []))
    new_alerts = []
    new_hashes = []

    for alert in alerts:
        h = hash(alert["line"])
        if h not in seen:
            new_alerts.append(alert)
            new_hashes.append(h)

    # Keep last 1000 hashes to prevent unbounded growth
    all_hashes = list(seen) + new_hashes
    state["seen_hashes"] = all_hashes[-1000:]
    return new_alerts


def format_alert(alert: dict) -> str:
    icons = {"critical": "🔴", "warning": "🟡", "info": "🟢"}
    icon = icons.get(alert["severity"], "⚪")
    return f"{icon} **[{alert['severity'].upper()}]** {alert['description']}\n```\n{alert['line'][:300]}\n```"


def send_discord(alerts: list[dict], webhook_url: str):
    """Send alerts to Discord webhook."""
    if not alerts:
        return

    # Group by severity
    critical = [a for a in alerts if a["severity"] == "critical"]
    warning = [a for a in alerts if a["severity"] == "warning"]
    info = [a for a in alerts if a["severity"] == "info"]

    content_parts = [f"**Sui Auto LP Watcher** — {len(alerts)} alert(s)"]

    for group, label in [(critical, "Critical"), (warning, "Warning"), (info, "Info")]:
        if group:
            content_parts.append(f"\n__{label} ({len(group)})__")
            for a in group[:5]:  # Max 5 per severity to avoid message limit
                content_parts.append(format_alert(a))

    content = "\n".join(content_parts)
    # Discord limit: 2000 chars
    if len(content) > 1900:
        content = content[:1900] + "\n... (truncated)"

    payload = json.dumps({"content": content})

    try:
        subprocess.run(
            ["curl", "-s", "-X", "POST", webhook_url,
             "-H", "Content-Type: application/json",
             "-d", payload],
            capture_output=True,
            timeout=15,
        )
    except Exception as e:
        print(f"Discord send failed: {e}", file=sys.stderr)


def output_alerts(alerts: list[dict], output_file: str | None, discord_url: str | None):
    """Output alerts to configured destinations."""
    if not alerts:
        return

    # Always print to stdout
    for alert in alerts:
        print(format_alert(alert))

    # File output (JSONL)
    if output_file:
        with open(output_file, "a") as f:
            for alert in alerts:
                f.write(json.dumps(alert, ensure_ascii=False) + "\n")

    # Discord
    if discord_url:
        send_discord(alerts, discord_url)


def run_once(since: str, output_file: str | None, discord_url: str | None, no_dedup: bool = False) -> int:
    """Single check cycle. Returns number of new alerts."""
    state = load_state()
    logs = fetch_logs(since)
    if not logs.strip():
        return 0

    alerts = check_rules(logs)
    if not no_dedup:
        alerts = deduplicate(alerts, state)

    output_alerts(alerts, output_file, discord_url)

    state["last_check"] = datetime.now(timezone.utc).isoformat()
    save_state(state)
    return len(alerts)


def main():
    parser = argparse.ArgumentParser(description="Log pattern watcher with notifications")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument("--stdin", action="store_true", help="Read logs from stdin instead of GCE")
    parser.add_argument("--interval", type=int, default=300, help="Check interval in seconds (default: 300)")
    parser.add_argument("--since", default="10 minutes ago", help="Log time range for each check")
    parser.add_argument("--discord-webhook", help="Discord webhook URL")
    parser.add_argument("--output", help="Append alerts to JSONL file")
    parser.add_argument("--no-dedup", action="store_true", help="Don't deduplicate alerts")
    parser.add_argument("--reset", action="store_true", help="Reset seen state and exit")
    args = parser.parse_args()

    discord_url = args.discord_webhook or load_env_var("DISCORD_WEBHOOK_URL")

    if args.reset:
        if STATE_FILE.exists():
            STATE_FILE.unlink()
            print("Watcher state reset.")
        return

    # stdin mode: read logs from pipe and check rules directly
    if args.stdin:
        logs = sys.stdin.read()
        alerts = check_rules(logs)
        if not args.no_dedup:
            state = load_state()
            alerts = deduplicate(alerts, state)
            save_state(state)
        output_alerts(alerts, args.output, discord_url)
        print(f"\n--- {len(alerts)} alert(s) found ---", file=sys.stderr)
        return

    if args.once:
        count = run_once(args.since, args.output, discord_url, args.no_dedup)
        print(f"\n--- {count} alert(s) found ---", file=sys.stderr)
        return

    # Daemon mode
    print(f"Watcher started (interval: {args.interval}s, since: {args.since})", file=sys.stderr)
    try:
        while True:
            count = run_once(args.since, args.output, discord_url)
            if count:
                print(f"[{datetime.now().isoformat()}] {count} new alert(s)", file=sys.stderr)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nWatcher stopped.", file=sys.stderr)


if __name__ == "__main__":
    main()
