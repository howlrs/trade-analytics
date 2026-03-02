#!/usr/bin/env bash
# ==============================================================================
# Pre-deploy check for sui-auto-lp
#
# Validates and syncs configuration before deploying to GCE.
# Run this before deploy.sh to ensure pool/position IDs are correct.
#
# Usage:
#   GCE_PASSPHRASE=$(grep GCE_PASSPHRASE .env | cut -d= -f2) bash deploy/pre-deploy-check.sh
#   bash deploy/pre-deploy-check.sh   # if SSH key is already loaded
# ==============================================================================
set -euo pipefail

PROJECT_ID="crypto-bitflyer-418902"
ZONE="us-central1-a"
INSTANCE_NAME="sui-auto-lp"
GCE_KEY="${HOME}/.ssh/google_compute_engine"
LOCAL_ENV=".env"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

ok()   { echo -e "  ${GREEN}[OK]${NC}   $*"; }
warn() { echo -e "  ${YELLOW}[WARN]${NC} $*"; }
fail() { echo -e "  ${RED}[FAIL]${NC} $*"; FAILED=1; }

FAILED=0

echo "======================================================================"
echo "  Pre-Deploy Check — sui-auto-lp"
echo "  $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo "======================================================================"

# --- SSH key setup ---
if [ -f "${GCE_KEY}" ] && ! ssh-add -l 2>/dev/null | grep -q "google_compute_engine"; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  source "${SCRIPT_DIR}/ssh-setup.sh"
fi

# --- 1. Type-check ---
echo ""
echo "--- 1. TypeScript type-check ---"
if npm run type-check --silent 2>/dev/null; then
  ok "type-check passed"
else
  fail "type-check failed — fix errors before deploying"
fi

# --- 2. Fetch VM state ---
echo ""
echo "--- 2. Fetching VM state ---"
VM_INFO=$(gcloud compute ssh "${INSTANCE_NAME}" \
  --zone="${ZONE}" --project="${PROJECT_ID}" \
  --command="
    echo 'SERVICE_STATUS='$(sudo systemctl is-active sui-auto-lp 2>/dev/null || echo unknown)
    sudo grep -E '^POOL_IDS=|^POSITION_IDS=' /opt/sui-auto-lp/.env 2>/dev/null || true
    echo 'STATE_JSON='$(sudo cat /opt/sui-auto-lp/state.json 2>/dev/null | tr -d '\n' || echo '{}')
  " 2>/dev/null)

# Parse VM values
VM_SERVICE_STATUS=$(echo "$VM_INFO" | grep '^SERVICE_STATUS=' | cut -d= -f2 || echo "unknown")
VM_POOL_IDS=$(echo "$VM_INFO"       | grep '^POOL_IDS='       | cut -d= -f2 || echo "")
VM_POSITION_IDS=$(echo "$VM_INFO"   | grep '^POSITION_IDS='   | cut -d= -f2 || echo "")
STATE_JSON=$(echo "$VM_INFO"        | grep '^STATE_JSON='      | sed 's/^STATE_JSON=//' || echo "{}")

# Parse state.json for current position
STATE_POSITION=$(echo "$STATE_JSON" | grep -o '"positionId":"[^"]*"' | head -1 | cut -d'"' -f4 || echo "")

# --- 3. Service status ---
echo ""
echo "--- 3. Service status ---"
if [ "$VM_SERVICE_STATUS" = "active" ]; then
  ok "Service is running (active)"
else
  warn "Service status: ${VM_SERVICE_STATUS} (will be restarted by deploy)"
fi

# --- 4. Pool ID check ---
echo ""
echo "--- 4. Pool ID check ---"
LOCAL_POOL_IDS=$(grep '^POOL_IDS=' "${LOCAL_ENV}" 2>/dev/null | cut -d= -f2 || echo "")
if [ -z "$LOCAL_POOL_IDS" ]; then
  fail "POOL_IDS not set in local .env"
elif [ "$LOCAL_POOL_IDS" = "$VM_POOL_IDS" ]; then
  ok "POOL_IDS match: ${LOCAL_POOL_IDS:0:20}..."
else
  fail "POOL_IDS mismatch!"
  echo "       Local: ${LOCAL_POOL_IDS}"
  echo "       VM:    ${VM_POOL_IDS}"
fi

# --- 5. Position ID check and sync ---
echo ""
echo "--- 5. Position ID check ---"
LOCAL_POSITION_IDS=$(grep '^POSITION_IDS=' "${LOCAL_ENV}" 2>/dev/null | cut -d= -f2 || echo "")

echo "  Local .env:  ${LOCAL_POSITION_IDS:0:20}..."
echo "  VM .env:     ${VM_POSITION_IDS:0:20}..."
echo "  state.json:  ${STATE_POSITION:0:20}..."

# state.json is the source of truth
if [ -n "$STATE_POSITION" ] && [ "$STATE_POSITION" != "$LOCAL_POSITION_IDS" ]; then
  warn "POSITION_IDS out of sync — state.json has a newer position"
  echo "       Syncing local .env and VM .env to: ${STATE_POSITION:0:20}..."

  # Update local .env
  sed -i "s|^POSITION_IDS=.*|POSITION_IDS=${STATE_POSITION}|" "${LOCAL_ENV}"
  ok "Local .env updated"

  # Update VM .env
  gcloud compute ssh "${INSTANCE_NAME}" \
    --zone="${ZONE}" --project="${PROJECT_ID}" \
    --command="sudo sed -i 's|^POSITION_IDS=.*|POSITION_IDS=${STATE_POSITION}|' /opt/sui-auto-lp/.env" \
    2>/dev/null
  ok "VM .env updated"
elif [ -n "$STATE_POSITION" ]; then
  ok "POSITION_IDS in sync with state.json"
else
  warn "state.json has no positionId — using .env value (normal on first deploy)"
fi

# --- 6. DRY_RUN check ---
echo ""
echo "--- 6. DRY_RUN check ---"
VM_DRY_RUN=$(gcloud compute ssh "${INSTANCE_NAME}" \
  --zone="${ZONE}" --project="${PROJECT_ID}" \
  --command="sudo grep '^DRY_RUN=' /opt/sui-auto-lp/.env 2>/dev/null || echo 'DRY_RUN=unknown'" \
  2>/dev/null | grep '^DRY_RUN=' | cut -d= -f2 || echo "unknown")
if [ "$VM_DRY_RUN" = "false" ]; then
  ok "DRY_RUN=false (live mode)"
elif [ "$VM_DRY_RUN" = "true" ]; then
  warn "DRY_RUN=true — bot will not execute transactions after deploy"
else
  warn "DRY_RUN not confirmed (${VM_DRY_RUN})"
fi

# --- Result ---
echo ""
echo "======================================================================"
if [ "$FAILED" -eq 0 ]; then
  echo -e "  ${GREEN}RESULT: All checks passed — safe to deploy${NC}"
  echo ""
  echo "  Next step:"
  echo "    GCE_PASSPHRASE=\$(grep GCE_PASSPHRASE .env | cut -d= -f2) bash deploy/deploy.sh"
else
  echo -e "  ${RED}RESULT: Check failed — fix issues before deploying${NC}"
  exit 1
fi
echo "======================================================================"
