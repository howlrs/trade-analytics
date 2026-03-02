#!/usr/bin/env bash
# Setup hourly trade history fetcher on GCE VM.
# Installs Python + polars, deploys fetch script, and adds cron job.
#
# Usage: bash deploy/setup_trades_cron.sh
set -euo pipefail

VM="sui-auto-lp"
ZONE="us-central1-a"
PROJECT="crypto-bitflyer-418902"
GCS_BUCKET="gs://crypto-bitflyer-drift-data"
SSH="gcloud compute ssh $VM --zone=$ZONE --project=$PROJECT"

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"

echo "=== Step 1: Install Python3 + polars on $VM ==="
$SSH --command "
  if python3 -c 'import polars' 2>/dev/null; then
    echo 'Python + polars already installed'
    python3 --version
  else
    sudo apt-get update
    sudo apt-get install -y python3 python3-pip python3-venv
    sudo python3 -m venv /opt/drift-trades-venv
    sudo /opt/drift-trades-venv/bin/pip install polars requests
    echo 'Python + polars installed'
  fi
"

echo "=== Step 2: Upload fetch_drift_trades.py ==="
# scp to /tmp first (user writable), then sudo mv to /opt
gcloud compute scp \
  "$REPO_ROOT/scripts/fetch_drift_trades.py" \
  "$VM":/tmp/fetch_drift_trades.py \
  --zone="$ZONE" --project="$PROJECT"

$SSH --command "sudo mv /tmp/fetch_drift_trades.py /opt/drift-collector/fetch_drift_trades.py"

echo "=== Step 3: Setup hourly cron ==="
$SSH --command "
  sudo mkdir -p /opt/drift-data/trades

  # Wrapper script for cron (activates venv, runs fetch, logs output)
  cat <<'WRAPPER' | sudo tee /opt/drift-collector/run_trades_fetch.sh > /dev/null
#!/usr/bin/env bash
set -euo pipefail
LOGFILE=/opt/drift-data/trades/fetch.log
echo \"[\$(date -u +%Y-%m-%dT%H:%M:%SZ)] Starting trade fetch\" >> \$LOGFILE
/opt/drift-trades-venv/bin/python /opt/drift-collector/fetch_drift_trades.py \
  --symbol SOL --limit 1000 --output-dir /opt/drift-data/trades >> \$LOGFILE 2>&1
echo \"[\$(date -u +%Y-%m-%dT%H:%M:%SZ)] Fetch complete\" >> \$LOGFILE
WRAPPER
  sudo chmod +x /opt/drift-collector/run_trades_fetch.sh

  # Add cron entry (every hour at minute 15, avoid conflict with other jobs)
  CRON_CMD='15 * * * * /opt/drift-collector/run_trades_fetch.sh'
  (crontab -l 2>/dev/null | grep -v 'run_trades_fetch' ; echo \"\$CRON_CMD\") | crontab -
  echo 'Cron entry added:'
  crontab -l | grep run_trades_fetch
"

echo "=== Step 4: Run initial fetch ==="
$SSH --command "sudo /opt/drift-collector/run_trades_fetch.sh"

echo "=== Setup complete ==="
echo "Trades will be fetched hourly and synced to GCS at 06:00 UTC."
echo "Check logs: gcloud compute ssh $VM --zone=$ZONE --project=$PROJECT --command 'tail -20 /opt/drift-data/trades/fetch.log'"
