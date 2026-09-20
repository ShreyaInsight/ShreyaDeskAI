#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"
SERVICE_NAME="shreyadesk"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
RUNTIME_DIR="${SHREYADESK_RUNTIME_DIR:-$HOME/.local/share/shreyadesk}"
APP_USER="$(id -un)"

if [[ "$(id -u)" -eq 0 ]]; then
  echo "Run this script as the application user, not root."
  exit 1
fi

command -v python3 >/dev/null || { echo "python3 is required." >&2; exit 1; }
command -v npm >/dev/null || { echo "Node.js/npm is required to build the frontend." >&2; exit 1; }
command -v sudo >/dev/null || { echo "sudo is required to install the systemd service." >&2; exit 1; }

python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/pip" install -r "$PROJECT_DIR/backend/requirements.txt"

pushd "$PROJECT_DIR/frontend" >/dev/null
npm ci
npm run build
popd >/dev/null

sudo install -d -o www-data -g www-data -m 0755 /var/www/shreyadesk
sudo cp -a "$PROJECT_DIR/frontend/dist/." /var/www/shreyadesk/

install -d -m 0700 "$RUNTIME_DIR"
if [[ ! -f "$RUNTIME_DIR/shreyadesk.env" ]]; then
  install -m 0600 "$PROJECT_DIR/.env.example" "$RUNTIME_DIR/shreyadesk.env"
  echo "Created $RUNTIME_DIR/shreyadesk.env; set unique credentials before use."
fi
chmod 600 "$RUNTIME_DIR/shreyadesk.env" "$RUNTIME_DIR/.kite_token.json" "$RUNTIME_DIR/scanner.sqlite3" "$RUNTIME_DIR/telegram.sqlite3" 2>/dev/null || true

SERVICE_TEMP="$(mktemp)"
trap 'rm -f "$SERVICE_TEMP"' EXIT
APP_USER="$APP_USER" PROJECT_DIR="$PROJECT_DIR" RUNTIME_DIR="$RUNTIME_DIR" SERVICE_TEMP="$SERVICE_TEMP" python3 - <<'SERVICE_TEMPLATE'
import os
from pathlib import Path
source = Path(os.environ['PROJECT_DIR'], 'deploy', 'shreyadesk.service').read_text()
for key in ('APP_USER', 'PROJECT_DIR', 'RUNTIME_DIR'):
    source = source.replace('@' + key + '@', os.environ[key])
Path(os.environ['SERVICE_TEMP']).write_text(source)
SERVICE_TEMPLATE
sudo install -m 0644 "$SERVICE_TEMP" "$SERVICE_PATH"
sudo systemctl daemon-reload
sudo systemctl enable --now "$SERVICE_NAME"
sudo systemctl --no-pager --full status "$SERVICE_NAME"

cat <<EOF

Backend is running on 127.0.0.1:8000.
The frontend bundle is at $PROJECT_DIR/frontend/dist.
Next: install Nginx and copy deploy/nginx-shreyadesk.conf to /etc/nginx/sites-available/shreyadesk,
then enable that site with your VM hostname or IP.
EOF