#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Issue #29 Post-Rebalance Verification Script
#
# Checks production logs after the first rebalance following the #29 deploy
# to verify all new guardrails and parameter changes are working correctly.
#
# Usage:
#   GCE_PASSPHRASE=xxx bash scripts/verify-issue29.sh
#
# The script fetches logs since the deploy time and checks for expected patterns.
# ==============================================================================

DEPLOY_TIME="2026-02-19 05:51"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

pass() { echo -e "  ${GREEN}PASS${NC} $1"; }
fail() { echo -e "  ${RED}FAIL${NC} $1"; }
warn() { echo -e "  ${YELLOW}WARN${NC} $1"; }
info() { echo -e "  ${YELLOW}INFO${NC} $1"; }

echo "============================================"
echo "Issue #29 Post-Deploy Verification"
echo "Deploy time: ${DEPLOY_TIME} UTC"
echo "============================================"
echo ""

# Fetch logs since deploy
echo "--- Fetching logs since deploy ---"
LOGS=$(GCE_PASSPHRASE="${GCE_PASSPHRASE:-}" bash "${PROJECT_ROOT}/deploy/logs.sh" --since "${DEPLOY_TIME}" 2>&1 | grep -v "^---\|^Identity\|^GCE\|^$" || true)

if [ -z "$LOGS" ]; then
  fail "No logs found since ${DEPLOY_TIME}"
  exit 1
fi

LINE_COUNT=$(echo "$LOGS" | wc -l)
echo "Fetched ${LINE_COUNT} log lines"
echo ""

# --- Check 1: Service started successfully ---
echo "=== 1. Service Startup ==="
if echo "$LOGS" | grep -q "Starting scheduler"; then
  pass "Scheduler started"
else
  fail "Scheduler start not found in logs"
fi

if echo "$LOGS" | grep -q "Restored position ID from state.json"; then
  pass "Position ID restored from state.json"
else
  warn "Position ID restore not found (may be first run)"
fi

echo ""

# --- Check 2: Rebalance evaluation running ---
echo "=== 2. Rebalance Monitoring ==="
EVAL_COUNT=$(echo "$LOGS" | grep -c "Rebalance evaluation" || true)
if [ "$EVAL_COUNT" -gt 0 ]; then
  pass "Rebalance evaluations: ${EVAL_COUNT} checks"
else
  fail "No rebalance evaluations found"
fi

# Check for "within range" (normal operation)
INRANGE_COUNT=$(echo "$LOGS" | grep -c "Price is within range and threshold" || true)
if [ "$INRANGE_COUNT" -gt 0 ]; then
  pass "In-range checks: ${INRANGE_COUNT}"
fi

echo ""

# --- Check 3: Guardrail activation ---
echo "=== 3. New Guardrails ==="

# Range-out wait
RANGEOUT_WAIT=$(echo "$LOGS" | grep -c "Range-out.*detected, waiting" || true)
RANGEOUT_ACTIVE=$(echo "$LOGS" | grep -c "Range-out.*wait:" || true)
if [ "$RANGEOUT_WAIT" -gt 0 ] || [ "$RANGEOUT_ACTIVE" -gt 0 ]; then
  pass "Range-out wait triggered: ${RANGEOUT_WAIT} initial, ${RANGEOUT_ACTIVE} ongoing"
else
  info "Range-out wait not triggered yet (price still in range)"
fi

# Daily limit
DAILY_LIMIT=$(echo "$LOGS" | grep -c "Daily rebalance limit" || true)
if [ "$DAILY_LIMIT" -gt 0 ]; then
  pass "Daily limit enforced: ${DAILY_LIMIT} times"
else
  info "Daily limit not hit (< 3 rebalances today)"
fi

# Min time in range
MIN_TIME=$(echo "$LOGS" | grep -c "position too new" || true)
if [ "$MIN_TIME" -gt 0 ]; then
  pass "Min-time-in-range guard active: ${MIN_TIME} suppressions"
else
  info "Min-time-in-range not triggered (position old enough or no threshold trigger)"
fi

# Cooldown
COOLDOWN=$(echo "$LOGS" | grep -c "Rebalance cooldown active" || true)
if [ "$COOLDOWN" -gt 0 ]; then
  COOLDOWN_30M=$(echo "$LOGS" | grep "cooldownSec.*1800" | wc -l || true)
  COOLDOWN_60M=$(echo "$LOGS" | grep "cooldownSec.*3600" | wc -l || true)
  pass "Cooldown active: ${COOLDOWN} times (30min: ${COOLDOWN_30M}, 60min: ${COOLDOWN_60M})"
else
  info "No cooldown triggered (no recent rebalance)"
fi

# Profitability gate
PROFIT_GATE=$(echo "$LOGS" | grep -c "Profitability gate" || true)
if [ "$PROFIT_GATE" -gt 0 ]; then
  # Check maxBreakevenHours value
  if echo "$LOGS" | grep "Profitability gate" | grep -q "maxBreakevenHours.*48"; then
    pass "Profitability gate: maxBreakevenHours=48 confirmed"
  else
    warn "Profitability gate found but maxBreakevenHours not 48"
  fi
else
  info "Profitability gate not evaluated (no range-out with poolFeeRate)"
fi

echo ""

# --- Check 4: Rebalance execution ---
echo "=== 4. Rebalance Execution ==="
REBAL_TRIGGERED=$(echo "$LOGS" | grep -c "rebalance_triggered\|Rebalance completed" || true)
if [ "$REBAL_TRIGGERED" -gt 0 ]; then
  pass "Rebalances executed: ${REBAL_TRIGGERED}"

  # Check new range width
  if echo "$LOGS" | grep -q "Optimal range calculated.*volatility"; then
    pass "Volatility-based range used for new position"
    # Extract tick width
    VOL_WIDTH=$(echo "$LOGS" | grep "Volatility-based tick width" | tail -1 || true)
    if [ -n "$VOL_WIDTH" ]; then
      info "Latest: ${VOL_WIDTH}"
    fi
  fi

  # Check for errors
  REBAL_ERRORS=$(echo "$LOGS" | grep -c "rebalance_error\|CRITICAL" || true)
  if [ "$REBAL_ERRORS" -gt 0 ]; then
    fail "Rebalance errors found: ${REBAL_ERRORS}"
    echo "$LOGS" | grep "rebalance_error\|CRITICAL" | tail -3
  else
    pass "No rebalance errors"
  fi
else
  info "No rebalances executed yet (price in range)"
fi

echo ""

# --- Check 5: Compound ---
echo "=== 5. Compound/Harvest ==="
COMPOUND=$(echo "$LOGS" | grep -c "compound\|harvest" || true)
if [ "$COMPOUND" -gt 0 ]; then
  pass "Compound/harvest activity: ${COMPOUND} log lines"
else
  info "No compound activity yet (next scheduled shown at startup)"
fi

echo ""

# --- Summary ---
echo "============================================"
echo "Verification Summary"
echo "============================================"

REBAL_TOTAL=$(echo "$LOGS" | grep -c "Rebalance completed" || true)
ERROR_TOTAL=$(echo "$LOGS" | grep -c "rebalance_error\|CRITICAL" || true)
SKIP_TOTAL=$((RANGEOUT_WAIT + RANGEOUT_ACTIVE + DAILY_LIMIT + MIN_TIME + COOLDOWN))

echo "  Checks run:        ${EVAL_COUNT}"
echo "  In-range:          ${INRANGE_COUNT}"
echo "  Rebalances:        ${REBAL_TOTAL}"
echo "  Guardrail skips:   ${SKIP_TOTAL}"
echo "  Errors:            ${ERROR_TOTAL}"
echo ""

if [ "$ERROR_TOTAL" -gt 0 ]; then
  echo -e "${RED}ACTION NEEDED: Errors detected. Check logs.${NC}"
elif [ "$REBAL_TOTAL" -gt 0 ]; then
  echo -e "${GREEN}Rebalance observed with new guardrails active.${NC}"
else
  echo -e "${YELLOW}No rebalance yet. Re-run after price moves out of range.${NC}"
fi
