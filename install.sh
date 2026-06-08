#!/usr/bin/env bash
set -euo pipefail

APP_USER="telegramdl"
APP_ROOT="/opt/telegram-downloader"
APP_DIR="$APP_ROOT/app"
VENV_DIR="$APP_ROOT/venv"
DATA_DIR="$APP_ROOT/data"
ENV_DIR="/etc/telegram-downloader"
ENV_FILE="$ENV_DIR/telegram-downloader.env"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root." >&2
  exit 1
fi

apt-get update
apt-get install -y python3 python3-venv python3-pip redis-server curl ca-certificates nginx rsync

if ! command -v tdl >/dev/null 2>&1; then
  echo "Installing tdl..."
  curl -sSL https://docs.iyear.me/tdl/install.sh | bash
fi

if ! id "$APP_USER" >/dev/null 2>&1; then
  useradd --system --home "$APP_ROOT" --shell /usr/sbin/nologin "$APP_USER"
fi

mkdir -p "$APP_DIR" "$DATA_DIR/sessions" "$DATA_DIR/exports" "$DATA_DIR/downloads" "$DATA_DIR/logs" "$ENV_DIR"
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

python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip wheel
"$VENV_DIR/bin/pip" install -r "$APP_DIR/requirements.txt"

if [[ ! -f "$ENV_FILE" ]]; then
  cp "$APP_DIR/.env.example" "$ENV_FILE"
  if command -v tdl >/dev/null 2>&1; then
    sed -i "s|^TDL_BINARY=.*|TDL_BINARY=$(command -v tdl)|" "$ENV_FILE"
  fi
  chmod 640 "$ENV_FILE"
fi

cp "$APP_DIR/deploy/systemd/telegram-downloader-web.service" /etc/systemd/system/telegram-downloader-web.service
cp "$APP_DIR/deploy/systemd/telegram-downloader-worker.service" /etc/systemd/system/telegram-downloader-worker.service

chown -R "$APP_USER:$APP_USER" "$APP_ROOT"
chown root:"$APP_USER" "$ENV_FILE"
chmod 750 "$APP_ROOT" "$DATA_DIR"
chmod 750 "$DATA_DIR/sessions" "$DATA_DIR/exports" "$DATA_DIR/downloads" "$DATA_DIR/logs"

systemctl enable --now redis-server
systemctl daemon-reload
systemctl enable telegram-downloader-web telegram-downloader-worker
systemctl restart telegram-downloader-web telegram-downloader-worker

echo "Installed. Open http://SERVER_IP:8000"
echo "tdl binary: $(command -v tdl || true)"
