#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# GCP Compute Engine Initial Setup for sui-auto-lp
# Run this from your LOCAL machine (project root) with gcloud CLI configured.
#
# Builds locally, transfers via SCP. No git needed on VM.
#
# Usage:
#   export SUI_PRIVATE_KEY='your-base64-key'
#   bash deploy/setup.sh
# ==============================================================================

PROJECT_ID="crypto-bitflyer-418902"
ZONE="us-central1-a"
INSTANCE_NAME="sui-auto-lp"

echo "=== Sui Auto LP - GCP Setup ==="
echo "Project: ${PROJECT_ID}"
echo "Zone:    ${ZONE}"
echo "VM:      ${INSTANCE_NAME}"
echo ""

# --------------------------------------------------
# Step 1: Set project & enable APIs
# --------------------------------------------------
echo "[1/6] Setting project and enabling APIs..."
gcloud config set project "${PROJECT_ID}"
gcloud services enable secretmanager.googleapis.com

# --------------------------------------------------
# Step 2: Store SUI_PRIVATE_KEY in Secret Manager
# --------------------------------------------------
echo "[2/6] Storing secret in Secret Manager..."
if gcloud secrets describe sui-private-key --project="${PROJECT_ID}" &>/dev/null; then
  echo "  Secret 'sui-private-key' already exists. Skipping."
  echo "  To update: echo -n \"\$SUI_PRIVATE_KEY\" | gcloud secrets versions add sui-private-key --data-file=-"
else
  if [ -z "${SUI_PRIVATE_KEY:-}" ]; then
    echo "  ERROR: SUI_PRIVATE_KEY env var is not set."
    echo "  Export it first: export SUI_PRIVATE_KEY='your-base64-key'"
    exit 1
  fi
  echo -n "${SUI_PRIVATE_KEY}" | gcloud secrets create sui-private-key \
    --data-file=- \
    --replication-policy=automatic \
    --project="${PROJECT_ID}"
  echo "  Secret stored."
fi

# --------------------------------------------------
# Step 3: Create e2-micro VM (free tier)
# --------------------------------------------------
echo "[3/6] Creating VM instance..."
if gcloud compute instances describe "${INSTANCE_NAME}" --zone="${ZONE}" &>/dev/null; then
  echo "  VM '${INSTANCE_NAME}' already exists. Skipping creation."
else
  gcloud compute instances create "${INSTANCE_NAME}" \
    --project="${PROJECT_ID}" \
    --zone="${ZONE}" \
    --machine-type=e2-micro \
    --image-family=debian-12 \
    --image-project=debian-cloud \
    --boot-disk-size=30GB \
    --boot-disk-type=pd-standard \
    --tags=sui-bot \
    --scopes=cloud-platform \
    --metadata=enable-oslogin=TRUE
  echo "  VM created. Waiting 30s for SSH to be ready..."
  sleep 30
fi

# --------------------------------------------------
# Step 4: Grant Secret Manager access to VM service account
# --------------------------------------------------
echo "[4/6] Granting Secret Manager access..."
SA_EMAIL=$(gcloud compute instances describe "${INSTANCE_NAME}" \
  --zone="${ZONE}" \
  --format='get(serviceAccounts[0].email)')

gcloud secrets add-iam-policy-binding sui-private-key \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/secretmanager.secretAccessor" \
  --project="${PROJECT_ID}" \
  --quiet

# --------------------------------------------------
# Step 5: Firewall rule — SSH only
# --------------------------------------------------
echo "[5/6] Configuring firewall..."
if gcloud compute firewall-rules describe allow-ssh-sui-bot --project="${PROJECT_ID}" &>/dev/null; then
  echo "  Firewall rule already exists. Skipping."
else
  gcloud compute firewall-rules create allow-ssh-sui-bot \
    --project="${PROJECT_ID}" \
    --direction=INGRESS \
    --priority=1000 \
    --network=default \
    --action=ALLOW \
    --rules=tcp:22 \
    --source-ranges=0.0.0.0/0 \
    --target-tags=sui-bot
  echo "  Firewall rule created."
fi

# --------------------------------------------------
# Step 6: Build locally, SCP to VM, provision
# --------------------------------------------------
echo "[6/6] Building locally and deploying to VM..."

# Build locally
echo "  Building project..."
npm run build

# Create tarball (no src/, no node_modules/, no .env)
echo "  Creating deployment tarball..."
TARBALL=$(mktemp /tmp/sui-auto-lp-XXXXXX.tar.gz)
tar czf "${TARBALL}" \
  --exclude='node_modules' \
  --exclude='src' \
  --exclude='.env' \
  --exclude='.git' \
  package.json package-lock.json dist/ deploy/

# Verify .env exists locally
if [ ! -f .env ]; then
  echo "  ERROR: .env file not found in project root."
  echo "  Create it from .env.example and fill in all values."
  rm -f "${TARBALL}"
  exit 1
fi

# SCP tarball and .env to VM
echo "  Transferring files to VM..."
gcloud compute scp "${TARBALL}" "${INSTANCE_NAME}:/tmp/sui-auto-lp.tar.gz" --zone="${ZONE}"
gcloud compute scp .env "${INSTANCE_NAME}:/tmp/sui-auto-lp.env" --zone="${ZONE}"

# Run provisioning on VM
echo "  Provisioning VM..."
gcloud compute ssh "${INSTANCE_NAME}" --zone="${ZONE}" --command="$(cat <<'REMOTE_SCRIPT'
set -euo pipefail

echo "--- Installing Node.js 22 ---"
if ! command -v node &>/dev/null || [[ "$(node -v)" != v22* ]]; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
  sudo apt-get install -y nodejs
fi
echo "Node: $(node -v), npm: $(npm -v)"

echo "--- Setting up application ---"
sudo mkdir -p /opt/sui-auto-lp/logs

# Extract tarball
cd /opt/sui-auto-lp
sudo tar xzf /tmp/sui-auto-lp.tar.gz --strip-components=0
rm -f /tmp/sui-auto-lp.tar.gz

# Install production dependencies only
sudo npm ci --omit=dev

# Place .env
sudo mv /tmp/sui-auto-lp.env /opt/sui-auto-lp/.env
sudo chmod 600 /opt/sui-auto-lp/.env

# Create service user if not exists
if ! id sui-bot &>/dev/null; then
  sudo useradd --system --no-create-home --shell /usr/sbin/nologin sui-bot
fi

# Fix ownership
sudo chown -R sui-bot:sui-bot /opt/sui-auto-lp

echo "--- Setting up systemd service ---"
sudo cp /opt/sui-auto-lp/deploy/sui-auto-lp.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable sui-auto-lp
sudo systemctl start sui-auto-lp

echo "--- Done! Checking status ---"
sudo systemctl status sui-auto-lp --no-pager || true
REMOTE_SCRIPT
)"

# Cleanup local tarball
rm -f "${TARBALL}"

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Verify with:"
echo "  gcloud compute ssh ${INSTANCE_NAME} --zone=${ZONE} --command='sudo systemctl status sui-auto-lp'"
echo "  gcloud compute ssh ${INSTANCE_NAME} --zone=${ZONE} --command='sudo journalctl -u sui-auto-lp -f'"
