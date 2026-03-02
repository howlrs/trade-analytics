#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Fetch logs from GCP Compute Engine VM
#
# Handles SSH agent setup automatically, then runs journalctl on the VM.
# All arguments are passed to journalctl as filters.
#
# Usage:
#   GCE_PASSPHRASE=xxx bash deploy/logs.sh                    # last 50 lines
#   GCE_PASSPHRASE=xxx bash deploy/logs.sh --since "1 hour ago"
#   GCE_PASSPHRASE=xxx bash deploy/logs.sh --since "1 hour ago" -g compound
#   GCE_PASSPHRASE=xxx bash deploy/logs.sh -f                 # follow (tail -f)
#   GCE_PASSPHRASE=xxx bash deploy/logs.sh status             # systemctl status only
#
# Environment:
#   GCE_PASSPHRASE  - SSH key passphrase (required for non-interactive use)
# ==============================================================================

PROJECT_ID="crypto-bitflyer-418902"
ZONE="us-central1-a"
INSTANCE_NAME="sui-auto-lp"
SERVICE_NAME="sui-auto-lp"

# --- SSH agent setup ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/ssh-setup.sh"

# --- Resolve SSH_AUTH_SOCK for gcloud ---
# gcloud spawns a child ssh process that needs access to the agent.
# Export it so gcloud's subprocess inherits it.
export SSH_AUTH_SOCK

# --- Parse -g option (extract grep pattern before passing rest to journalctl) ---
GREP_PATTERN=""
JOURNALCTL_ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    -g)
      shift
      GREP_PATTERN="${1:-}"
      shift
      ;;
    *)
      JOURNALCTL_ARGS+=("$1")
      shift
      ;;
  esac
done

# --- Build remote command ---
if [ "${JOURNALCTL_ARGS[0]:-}" = "status" ]; then
  REMOTE_CMD="sudo systemctl status ${SERVICE_NAME} --no-pager"
elif [ ${#JOURNALCTL_ARGS[@]} -eq 0 ]; then
  # Default: last 50 lines
  REMOTE_CMD="sudo journalctl -u ${SERVICE_NAME} --no-pager -n 50"
else
  # Pass remaining arguments to journalctl (preserve quoting)
  ARGS=""
  for arg in "${JOURNALCTL_ARGS[@]}"; do
    ARGS="${ARGS} '${arg}'"
  done
  REMOTE_CMD="sudo journalctl -u ${SERVICE_NAME} --no-pager ${ARGS}"
fi

# Append grep pipe if -g was specified
if [ -n "${GREP_PATTERN}" ]; then
  REMOTE_CMD="${REMOTE_CMD} | grep -E '${GREP_PATTERN}'"
fi

echo "--- ${INSTANCE_NAME} (${ZONE}) ---"
gcloud compute ssh "${INSTANCE_NAME}" \
  --zone="${ZONE}" \
  --project="${PROJECT_ID}" \
  --command="${REMOTE_CMD}"
