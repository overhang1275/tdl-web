#!/usr/bin/env bash
set -euo pipefail

APP_USER="telegramdl"
APP_ROOT="${APP_ROOT:-/opt/tld-web}"
APP_DIR="$APP_ROOT/app"
VENV_DIR="$APP_ROOT/venv"
DATA_DIR="$APP_ROOT/data"
ENV_FILE="/etc/telegram-downloader/telegram-downloader.env"
SERVICES=(telegram-downloader-web telegram-downloader-worker)
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
UPDATE_MODE="copy"
HAS_CHANGES=1
PASSWORD_CHANGED=0
CONFIG_CHANGED=0

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

ensure_wipe() {
  if ! command -v wipe >/dev/null 2>&1; then
    apt-get update
    apt-get install -y wipe
  fi
  find /etc/sudoers.d -maxdepth 1 -type f -name "telegram-downloader-*" -delete
}

env_value() {
  [[ -f "$ENV_FILE" ]] || return 0
  grep -E "^$1=" "$ENV_FILE" | tail -n 1 | cut -d= -f2- || true
}

set_env_value() {
  local key="$1"
  local value="$2"
  local escaped_value=""
  escaped_value="$(printf '%s' "$value" | sed -e 's/[\/&|]/\\&/g')"
  mkdir -p "$(dirname "$ENV_FILE")"
  touch "$ENV_FILE"
  chown root:"$APP_USER" "$ENV_FILE"
  chmod 640 "$ENV_FILE"
  if grep -q "^$key=" "$ENV_FILE"; then
    sed -i "s|^$key=.*|$key=$escaped_value|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

ensure_web_password() {
  local password=""
  password="$(env_value WEB_PASSWORD)"
  if [[ -z "$password" || "$password" == change-* ]]; then
    password="$(random_value)"
    set_env_value WEB_PASSWORD "$password"
    PASSWORD_CHANGED=1
    echo "generated web password: $password"
  fi
}

ensure_transfer_settings() {
  local key value
  for key in DOWNLOAD_BATCH_SIZE DOWNLOAD_IDLE_TIMEOUT_SECONDS EXPORT_BATCH_SIZE; do
    case "$key" in
      DOWNLOAD_BATCH_SIZE) value=100 ;;
      DOWNLOAD_IDLE_TIMEOUT_SECONDS) value=600 ;;
      EXPORT_BATCH_SIZE) value=5000 ;;
    esac
    if [[ -z "$(env_value "$key")" ]]; then
      set_env_value "$key" "$value"
      CONFIG_CHANGED=1
    fi
  done
}

migrate_database() {
  (
    cd "$APP_DIR"
    runuser -u "$APP_USER" -- "$VENV_DIR/bin/python" -c '
import sys
from pathlib import Path
from app.config import load_env_file, settings
load_env_file(Path(sys.argv[1]), override=True)
settings.__init__()
from app.database import init_db
init_db()
print("Database migration OK")
' "$ENV_FILE"
  )
}

ask_yes_no() {
  local prompt="$1"
  local default="${2:-n}"
  local answer=""
  if [[ ! -t 0 ]]; then
    [[ "$default" =~ ^[YySs]$ ]]
    return
  fi
  read -r -p "$prompt " answer
  answer="${answer:-$default}"
  [[ "$answer" =~ ^[YySs]$ ]]
}

backup_before_update() {
  local db_url db_path downloads_dir backup_dir stamp
  db_url="$(env_value DATABASE_URL)"
  if [[ "$db_url" == sqlite:///* ]]; then
    db_path="${db_url#sqlite:///}"
  else
    db_path="$DATA_DIR/telegram_downloader.sqlite3"
  fi
  [[ "$db_path" == /* ]] || db_path="$APP_DIR/$db_path"
  downloads_dir="$(env_value DOWNLOADS_DIR)"
  downloads_dir="${downloads_dir:-$DATA_DIR/downloads}"
  backup_dir="$DATA_DIR/backups"
  stamp="$(date +%Y%m%d-%H%M%S)"

  mkdir -p "$backup_dir"
  if [[ -f "$db_path" ]]; then
    "$VENV_DIR/bin/python" - "$db_path" "$backup_dir/update-$stamp.sqlite3" <<'PY'
import sqlite3
import sys
from pathlib import Path

with sqlite3.connect(Path(sys.argv[1]).resolve().as_uri() + "?mode=ro", uri=True) as source:
    with sqlite3.connect(sys.argv[2]) as destination:
        source.backup(destination)
print("SQLite backup:", sys.argv[2])
PY
  fi
  if [[ -d "$downloads_dir" ]] && ask_yes_no "Respaldar descargas? Puede tardar mucho. [s/N]" n; then
    tar -czf "$backup_dir/downloads-$stamp.tgz" "$downloads_dir"
    echo "Downloads backup: $backup_dir/downloads-$stamp.tgz"
  fi
}

check_updates() {
  if [[ -d "$APP_DIR/.git" ]]; then
    UPDATE_MODE="git"
    git config --global --add safe.directory "$APP_DIR"
    git -C "$APP_DIR" fetch --quiet
    if ! git -C "$APP_DIR" rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1; then
      if [[ "$REPO_ROOT" == "$APP_DIR" ]]; then
        echo "No upstream configured in $APP_DIR."
        HAS_CHANGES=0
        return
      fi
      echo "No upstream configured in $APP_DIR, updating with current files."
      UPDATE_MODE="copy"
      return
    fi
    if [[ "$(git -C "$APP_DIR" rev-parse HEAD)" == "$(git -C "$APP_DIR" rev-parse '@{u}')" ]]; then
      HAS_CHANGES=0
    fi
    return
  fi

  if [[ -d "$REPO_ROOT/.git" ]]; then
    git config --global --add safe.directory "$REPO_ROOT"
    git -C "$REPO_ROOT" fetch --quiet
    if git -C "$REPO_ROOT" rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1 &&
      [[ "$(git -C "$REPO_ROOT" rev-parse HEAD)" == "$(git -C "$REPO_ROOT" rev-parse '@{u}')" ]]; then
      HAS_CHANGES=0
    fi
  else
    echo "No git repo found, updating with current files."
  fi
}

apply_update() {
  if [[ "$UPDATE_MODE" == "git" ]]; then
    git -C "$APP_DIR" pull --ff-only
    return
  fi

  rsync -a --delete \
  --exclude ".venv" \
  --exclude "venv" \
  --exclude "__pycache__" \
  --exclude ".pytest_cache" \
  --exclude "data" \
  --exclude "*.sqlite3" \
  --exclude ".env" \
  "$REPO_ROOT/" "$APP_DIR/"
}

install_service_units() {
  cp "$APP_DIR/deploy/systemd/telegram-downloader-web.service" /etc/systemd/system/telegram-downloader-web.service
  cp "$APP_DIR/deploy/systemd/telegram-downloader-worker.service" /etc/systemd/system/telegram-downloader-worker.service
  sed -i "s|/opt/tld-web|$APP_ROOT|g" /etc/systemd/system/telegram-downloader-web.service
  sed -i "s|/opt/tld-web|$APP_ROOT|g" /etc/systemd/system/telegram-downloader-worker.service
}

stop_services() {
  systemctl stop "${SERVICES[@]}"
}

start_services() {
  systemctl daemon-reload
  systemctl start "${SERVICES[@]}"
  for service in "${SERVICES[@]}"; do
    systemctl is-active --quiet "$service"
  done
}

ensure_wipe
ensure_web_password
ensure_transfer_settings
check_updates
if [[ "$HAS_CHANGES" -eq 0 && "$PASSWORD_CHANGED" -eq 0 && "$CONFIG_CHANGED" -eq 0 ]]; then
  echo "No hay actualizaciones disponibles."
  exit 0
fi

stop_services
trap 'systemctl stop "${SERVICES[@]}" || true; echo "La actualización falló. Revisa el error antes de iniciar los servicios." >&2' ERR
backup_before_update
apply_update
install_service_units
"$VENV_DIR/bin/pip" install -r "$APP_DIR/requirements.txt"
chown -R "$APP_USER:$APP_USER" "$APP_ROOT"
migrate_database
start_services
trap - ERR
echo "Updated."
