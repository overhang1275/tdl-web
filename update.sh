#!/usr/bin/env bash
set -euo pipefail

APP_USER="telegramdl"
APP_ROOT="${APP_ROOT:-/opt/tld-web}"
APP_DIR="$APP_ROOT/app"
VENV_DIR="$APP_ROOT/venv"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root." >&2
  exit 1
fi

rsync -a --delete \
  --exclude ".git" \
  --exclude ".venv" \
  --exclude "venv" \
  --exclude "__pycache__" \
  --exclude ".pytest_cache" \
  --exclude "data" \
  --exclude "*.sqlite3" \
  --exclude ".env" \
  ./ "$APP_DIR/"
"$VENV_DIR/bin/pip" install -r "$APP_DIR/requirements.txt"
chown -R "$APP_USER:$APP_USER" "$APP_ROOT"
systemctl daemon-reload
systemctl restart telegram-downloader-web telegram-downloader-worker
echo "Updated."
