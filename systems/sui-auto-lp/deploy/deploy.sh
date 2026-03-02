#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Deploy update to GCP Compute Engine VM
# Run this from your LOCAL machine (project root).
#
# Builds locally, transfers via SCP. No git needed on VM.
#
# Usage:
#   bash deploy/deploy.sh
#
# If SSH auth fails, run first:
#   source deploy/ssh-setup.sh
# ==============================================================================

PROJECT_ID="crypto-bitflyer-418902"
ZONE="us-central1-a"
INSTANCE_NAME="sui-auto-lp"
GCE_KEY="${HOME}/.ssh/google_compute_engine"

# Ensure GCE SSH key is loaded in ssh-agent
if [ -f "${GCE_KEY}" ] && ! ssh-add -l 2>/dev/null | grep -q "google_compute_engine"; then
  echo "--- Loading GCE SSH key into agent ---"
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  source "${SCRIPT_DIR}/ssh-setup.sh"
fi

echo "=== Deploying update to ${INSTANCE_NAME} ==="

# Build locally
echo "--- Building locally ---"
npm run build

# Create tarball (no src/, no node_modules/, no .env)
echo "--- Creating deployment tarball ---"
TARBALL=$(mktemp /tmp/sui-auto-lp-XXXXXX.tar.gz)
tar czf "${TARBALL}" \
  --exclude='node_modules' \
  --exclude='src' \
  --exclude='.env' \
  --exclude='.git' \
  package.json package-lock.json dist/ deploy/

# SCP tarball to VM
echo "--- Transferring to VM ---"
gcloud compute scp "${TARBALL}" "${INSTANCE_NAME}:/tmp/sui-auto-lp.tar.gz" \
  --zone="${ZONE}" --project="${PROJECT_ID}"

# Cleanup local tarball
rm -f "${TARBALL}"

# Run update on VM
echo "--- Updating on VM ---"
gcloud compute ssh "${INSTANCE_NAME}" --zone="${ZONE}" --project="${PROJECT_ID}" --command="$(cat <<'REMOTE_SCRIPT'
set -euo pipefail

cd /opt/sui-auto-lp

echo "--- Extracting update ---"
sudo tar xzf /tmp/sui-auto-lp.tar.gz --strip-components=0
rm -f /tmp/sui-auto-lp.tar.gz

echo "--- Installing dependencies ---"
sudo npm ci --omit=dev

echo "--- Fixing ownership ---"
sudo chown -R sui-bot:sui-bot /opt/sui-auto-lp

echo "--- Updating systemd service ---"
sudo cp /opt/sui-auto-lp/deploy/sui-auto-lp.service /etc/systemd/system/sui-auto-lp.service
sudo systemctl daemon-reload

echo "--- Restarting service ---"
sudo systemctl restart sui-auto-lp

echo "--- Waiting for startup ---"
sleep 3

echo "--- Service status ---"
sudo systemctl status sui-auto-lp --no-pager

echo ""
echo "Deploy complete! View logs:"
echo "  sudo journalctl -u sui-auto-lp -f"
REMOTE_SCRIPT
)"
