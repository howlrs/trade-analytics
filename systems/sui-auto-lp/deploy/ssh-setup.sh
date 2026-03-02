#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Load GCP Compute Engine SSH key into ssh-agent
#
# The gcloud CLI uses ~/.ssh/google_compute_engine for VM access.
# If this key has a passphrase, it must be loaded into ssh-agent before
# running deploy.sh or any gcloud compute ssh/scp commands.
#
# Usage:
#   source deploy/ssh-setup.sh                    # interactive (passphrase prompt)
#   GCE_PASSPHRASE=xxx source deploy/ssh-setup.sh # non-interactive
#   GCE_PASSPHRASE=xxx bash deploy/ssh-setup.sh   # non-interactive (subshell)
#
# After sourcing, SSH_AUTH_SOCK is exported and the key is available for
# gcloud compute ssh/scp in the current shell session.
#
# deploy.sh calls this automatically when the key is not loaded.
# ==============================================================================

GCE_KEY="${HOME}/.ssh/google_compute_engine"

if [ ! -f "${GCE_KEY}" ]; then
  echo "ERROR: GCE SSH key not found at ${GCE_KEY}"
  echo "Run: gcloud compute ssh <instance> to generate it"
  return 1 2>/dev/null || exit 1
fi

# Find or start ssh-agent.
# Priority: 1) current SSH_AUTH_SOCK  2) existing agent socket  3) new agent
if [ -z "${SSH_AUTH_SOCK:-}" ] || ! ssh-add -l &>/dev/null; then
  # Look for an existing agent socket on the system
  _EXISTING_SOCK=$(find /tmp/ssh-* -name 'agent.*' -type s 2>/dev/null | head -1 || true)
  if [ -n "${_EXISTING_SOCK}" ] && SSH_AUTH_SOCK="${_EXISTING_SOCK}" ssh-add -l &>/dev/null; then
    export SSH_AUTH_SOCK="${_EXISTING_SOCK}"
    echo "Attached to existing ssh-agent (${SSH_AUTH_SOCK})"
  else
    eval "$(ssh-agent -s)"
    export SSH_AUTH_SOCK SSH_AGENT_PID
    echo "Started new ssh-agent (PID ${SSH_AGENT_PID})"
  fi
fi

# Check if key is already loaded
if ssh-add -l 2>/dev/null | grep -q "google_compute_engine"; then
  echo "GCE SSH key already loaded in agent"
  return 0 2>/dev/null || exit 0
fi

# Load key
if [ -n "${GCE_PASSPHRASE:-}" ]; then
  # Non-interactive: use temporary askpass script
  ASKPASS_SCRIPT=$(mktemp /tmp/gce-askpass-XXXXXX.sh)
  printf '#!/bin/sh\necho "%s"\n' "${GCE_PASSPHRASE}" > "${ASKPASS_SCRIPT}"
  chmod +x "${ASKPASS_SCRIPT}"

  DISPLAY=x SSH_ASKPASS="${ASKPASS_SCRIPT}" SSH_ASKPASS_REQUIRE=force \
    ssh-add "${GCE_KEY}" </dev/null

  rm -f "${ASKPASS_SCRIPT}"
  echo "GCE SSH key loaded (non-interactive)"
else
  # Interactive: prompt for passphrase
  ssh-add "${GCE_KEY}"
  echo "GCE SSH key loaded"
fi
