#!/usr/bin/env bash
set -euo pipefail

APP_USER="telegramdl"
APP_ROOT="${APP_ROOT:-/opt/tld-web}"
APP_DIR="$APP_ROOT/app"
VENV_DIR="$APP_ROOT/venv"
DATA_DIR="$APP_ROOT/data"
ENV_FILE="/etc/telegram-downloader/telegram-downloader.env"
SUDOERS_FILE="/etc/sudoers.d/telegram-downloader-srm"
SERVICES=(telegram-downloader-web telegram-downloader-worker)
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
UPDATE_MODE="copy"
HAS_CHANGES=1
PASSWORD_CHANGED=0

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

ensure_secure_delete() {
  if ! command -v srm >/dev/null 2>&1 || ! command -v sudo >/dev/null 2>&1; then
    apt-get update
    apt-get install -y secure-delete sudo
  fi
  echo "$APP_USER ALL=(root) NOPASSWD: $(command -v srm || echo /usr/bin/srm)" > "$SUDOERS_FILE"
  chmod 440 "$SUDOERS_FILE"
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
  local db_url db_path downloads_dir backup_dir stamp tar_args=()
  db_url="$(env_value DATABASE_URL)"
  if [[ "$db_url" == sqlite:///* ]]; then
    db_path="${db_url#sqlite:///}"
  else
    db_path="$DATA_DIR/telegram_downloader.sqlite3"
  fi
  downloads_dir="$(env_value DOWNLOADS_DIR)"
  downloads_dir="${downloads_dir:-$DATA_DIR/downloads}"
  backup_dir="$DATA_DIR/backups"
  stamp="$(date +%Y%m%d-%H%M%S)"

  mkdir -p "$backup_dir"
  [[ -f "$db_path" ]] && tar_args+=("$db_path")
  if [[ -d "$downloads_dir" ]] && ask_yes_no "Respaldar descargas? Puede tardar mucho. [s/N]" n; then
    tar_args+=("$downloads_dir")
  fi
  if [[ "${#tar_args[@]}" -gt 0 ]]; then
    tar -czf "$backup_dir/update-$stamp.tgz" "${tar_args[@]}"
    echo "Backup: $backup_dir/update-$stamp.tgz"
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
}

ensure_secure_delete
ensure_web_password
check_updates
if [[ "$HAS_CHANGES" -eq 0 && "$PASSWORD_CHANGED" -eq 0 ]]; then
  echo "No hay actualizaciones disponibles."
  exit 0
fi

stop_services
trap start_services EXIT
backup_before_update
apply_update
install_service_units
"$VENV_DIR/bin/pip" install -r "$APP_DIR/requirements.txt"
chown -R "$APP_USER:$APP_USER" "$APP_ROOT"
start_services
trap - EXIT
echo "Updated."
