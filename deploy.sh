#!/bin/bash
# Deploy NetworkStatus to a Raspberry Pi (or any remote Linux host).
# Usage: ./deploy.sh <user@host>
#   e.g. ./deploy.sh mikek@192.168.1.42
#        ./deploy.sh mikek@raspberrypi.local

set -e

TARGET="${1:-}"
if [[ -z "$TARGET" ]]; then
  echo "Usage: $0 <user@host>"
  echo "  e.g. $0 mikek@raspberrypi.local"
  exit 1
fi

USER="${TARGET%%@*}"
HOST="${TARGET##*@}"
REMOTE_DIR="/home/$USER/NetworkStatus"

echo "▶ Syncing files to $TARGET:$REMOTE_DIR …"
rsync -av --progress \
  --exclude='.venv' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.git' \
  --exclude='*.db' \
  --exclude='netmon.service' \
  "$(dirname "$0")/" "$TARGET:$REMOTE_DIR/"

echo "▶ Updating service file with user=$USER and path=$REMOTE_DIR …"
ssh "$TARGET" bash << REMOTE
  set -e
  cd "$REMOTE_DIR"

  # Install/update Python dependencies
  if [[ ! -d .venv ]]; then
    echo "  Creating venv…"
    python3 -m venv .venv
  fi
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -r requirements.txt

  # Install service file only if not already present
  if [[ ! -f /etc/systemd/system/netmon.service ]]; then
    echo "  Installing systemd service…"
    sed "s|User=.*|User=$USER|; s|WorkingDirectory=.*|WorkingDirectory=$REMOTE_DIR|; s|ExecStart=.*|ExecStart=$REMOTE_DIR/.venv/bin/uvicorn web.app:app --host 0.0.0.0 --port 8080 --app-dir $REMOTE_DIR|" \
      netmon.service | sudo tee /etc/systemd/system/netmon.service > /dev/null
    sudo systemctl daemon-reload
    sudo systemctl enable netmon
  else
    echo "  Service file already exists — skipping (edit /etc/systemd/system/netmon.service manually if needed)"
  fi

  if sudo systemctl is-active --quiet netmon; then
    echo "  Restarting netmon service…"
    sudo systemctl restart netmon
  else
    echo "  Starting netmon service…"
    sudo systemctl start netmon
  fi

  echo "  Waiting for service to come up…"
  sleep 3
  sudo systemctl status netmon --no-pager -l
REMOTE

echo ""
echo "✓ Done. NetMon should be running at http://$HOST:8080"
