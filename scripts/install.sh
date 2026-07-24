#!/usr/bin/env bash
set -euo pipefail

APP_USER="telegramdl"
APP_ROOT="${APP_ROOT:-/opt/tld-web}"
APP_DIR="$APP_ROOT/app"
VENV_DIR="$APP_ROOT/venv"
DATA_DIR="$APP_ROOT/data"
ENV_DIR="/etc/telegram-downloader"
ENV_FILE="$ENV_DIR/telegram-downloader.env"
DEFAULT_MEDIA_DIR="$DATA_DIR/downloads"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root." >&2
  exit 1
fi

random_value() {
  python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
}

prompt_default() {
  local label="$1"
  local default="$2"
  local value=""
  read -r -p "$label [$default]: " value
  printf '%s\n' "${value:-$default}"
}

set_env_value() {
  local key="$1"
  local value="$2"
  local escaped_value=""
  escaped_value="$(printf '%s' "$value" | sed -e 's/[\/&|]/\\&/g')"
  if grep -q "^$key=" "$ENV_FILE"; then
    sed -i "s|^$key=.*|$key=$escaped_value|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

get_env_value() {
  grep -E "^$1=" "$ENV_FILE" | tail -n 1 | cut -d= -f2- || true
}

apt-get update
apt-get install -y python3 python3-venv python3-pip redis-server curl ca-certificates nginx rsync git wipe

if ! command -v tdl >/dev/null 2>&1; then
  echo "Installing tdl..."
  curl -sSL https://docs.iyear.me/tdl/install.sh | bash
fi

if [[ -t 0 ]]; then
  MEDIA_DIR="$(prompt_default "Carpeta para media/descargas" "$DEFAULT_MEDIA_DIR")"
else
  MEDIA_DIR="${DOWNLOADS_DIR:-$DEFAULT_MEDIA_DIR}"
fi
WEB_PASSWORD="$(random_value)"

if ! id "$APP_USER" >/dev/null 2>&1; then
  useradd --system --home "$APP_ROOT" --shell /usr/sbin/nologin "$APP_USER"
fi

mkdir -p "$APP_DIR" "$DATA_DIR/sessions" "$DATA_DIR/exports" "$MEDIA_DIR" "$DATA_DIR/logs" "$ENV_DIR"
rsync -a --delete \
  --exclude ".venv" \
  --exclude "venv" \
  --exclude "__pycache__" \
  --exclude ".pytest_cache" \
  --exclude "data" \
  --exclude "*.sqlite3" \
  --exclude ".env" \
  "$REPO_ROOT/" "$APP_DIR/"
if [[ -d "$APP_DIR/.git" ]]; then
  git config --global --add safe.directory "$APP_DIR"
fi

python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip wheel
"$VENV_DIR/bin/pip" install -r "$APP_DIR/requirements.txt"

if [[ ! -f "$ENV_FILE" ]]; then
  touch "$ENV_FILE"
  chmod 640 "$ENV_FILE"
fi

SECRET_KEY="$(get_env_value SECRET_KEY)"
if [[ -z "$SECRET_KEY" || "$SECRET_KEY" == change-* ]]; then
  SECRET_KEY="$(random_value)"
fi

set_env_value APP_ENV production
set_env_value SECRET_KEY "$SECRET_KEY"
set_env_value WEB_PASSWORD "$WEB_PASSWORD"
set_env_value DATA_DIR "$DATA_DIR"
set_env_value TDL_SESSIONS_DIR "$DATA_DIR/sessions"
set_env_value EXPORTS_DIR "$DATA_DIR/exports"
set_env_value DOWNLOADS_DIR "$MEDIA_DIR"
set_env_value LOGS_DIR "$DATA_DIR/logs"
set_env_value DATABASE_URL "sqlite:///$DATA_DIR/telegram_downloader.sqlite3"
set_env_value REDIS_URL "redis://127.0.0.1:6379/0"
set_env_value TDL_BINARY "$(command -v tdl || echo /usr/local/bin/tdl)"
set_env_value TDL_NAMESPACE default
set_env_value COMMAND_TIMEOUT_SECONDS 7200

cp "$APP_DIR/deploy/systemd/telegram-downloader-web.service" /etc/systemd/system/telegram-downloader-web.service
cp "$APP_DIR/deploy/systemd/telegram-downloader-worker.service" /etc/systemd/system/telegram-downloader-worker.service
sed -i "s|/opt/tld-web|$APP_ROOT|g" /etc/systemd/system/telegram-downloader-web.service
sed -i "s|/opt/tld-web|$APP_ROOT|g" /etc/systemd/system/telegram-downloader-worker.service

chown -R "$APP_USER:$APP_USER" "$APP_ROOT"
chown -R "$APP_USER:$APP_USER" "$MEDIA_DIR"
chown root:"$APP_USER" "$ENV_FILE"
chmod 750 "$APP_ROOT" "$DATA_DIR"
chmod 750 "$DATA_DIR/sessions" "$DATA_DIR/exports" "$MEDIA_DIR" "$DATA_DIR/logs"

systemctl enable --now redis-server
systemctl daemon-reload
systemctl enable telegram-downloader-web telegram-downloader-worker
systemctl restart telegram-downloader-web telegram-downloader-worker

echo "Installed. Open http://SERVER_IP:8000"
echo "tdl binary: $(command -v tdl || true)"
echo "app root: $APP_ROOT"
echo "media dir: $MEDIA_DIR"
echo "generated web password: $WEB_PASSWORD"
