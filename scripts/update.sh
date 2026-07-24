#!/usr/bin/env bash
set -euo pipefail

APP_USER="telegramdl"
APP_ROOT="${APP_ROOT:-/opt/tld-web}"
APP_DIR="$APP_ROOT/app"
VENV_DIR="$APP_ROOT/venv"
DATA_DIR="$APP_ROOT/data"
ENV_FILE="/etc/telegram-downloader/telegram-downloader.env"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root." >&2
  exit 1
fi

env_value() {
  [[ -f "$ENV_FILE" ]] || return 0
  grep -E "^$1=" "$ENV_FILE" | tail -n 1 | cut -d= -f2- || true
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

update_repo() {
  if [[ ! -d "$REPO_ROOT/.git" ]]; then
    echo "No git repo found, using current files."
    return
  fi
  git -C "$REPO_ROOT" fetch --quiet
  if ! git -C "$REPO_ROOT" rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1; then
    echo "No upstream configured, using current branch."
    return
  fi
  if [[ "$(git -C "$REPO_ROOT" rev-parse HEAD)" != "$(git -C "$REPO_ROOT" rev-parse '@{u}')" ]]; then
    git -C "$REPO_ROOT" pull --ff-only
  else
    echo "Repo already up to date."
  fi
}

backup_before_update
update_repo

rsync -a --delete \
  --exclude ".git" \
  --exclude ".venv" \
  --exclude "venv" \
  --exclude "__pycache__" \
  --exclude ".pytest_cache" \
  --exclude "data" \
  --exclude "*.sqlite3" \
  --exclude ".env" \
  "$REPO_ROOT/" "$APP_DIR/"
"$VENV_DIR/bin/pip" install -r "$APP_DIR/requirements.txt"
chown -R "$APP_USER:$APP_USER" "$APP_ROOT"
systemctl daemon-reload
systemctl restart telegram-downloader-web telegram-downloader-worker
echo "Updated."
