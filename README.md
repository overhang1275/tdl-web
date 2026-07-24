# Telegram Downloader Web

Aplicación FastAPI para convertir un flujo `tdl` basado en bash en una interfaz web operable dentro de un LXC Debian/Ubuntu, sin Docker como requisito principal.

## Funciones

- Detecta sesión activa de Telegram/`tdl`.
- Lista chats/canales usando `tdl`.
- Crea jobs con filtros por hashtag, tipo de medio, texto libre y rango de fechas.
- Reutiliza `export.json` por chat para evitar exportar historiales grandes en cada job.
- Ejecuta exportación, filtrado JSON y descarga en segundo plano con Redis + RQ.
- Guarda historial en SQLite.
- Escribe logs por job.
- Elimina jobs con `secure-delete` (`srm --recursive --gutmann`) antes de borrar el registro.
- Muestra progreso con HTMX polling.
- Explora archivos descargados desde la UI.

## Rutas de datos

Por defecto en producción:

- `/opt/tld-web/app`
- `/opt/tld-web/venv`
- `/opt/tld-web/data/sessions`
- `/opt/tld-web/data/exports`
- `/opt/tld-web/data/downloads`
- `/opt/tld-web/data/logs`
- `/etc/telegram-downloader/telegram-downloader.env`

Los exports se guardan por chat:

```text
/opt/tld-web/data/exports/<chat_id>/export.json
```

Cada job genera su propio filtrado:

```text
/opt/tld-web/data/exports/<chat_id>/filtered-job-<job_id>.json
```

Cuando creas un job desde `/jobs`, si ya existe `export.json` para ese chat, la UI pregunta si quieres actualizarlo. Si no marcas esa opción, el job reutiliza el export existente y solo vuelve a filtrar/descargar.

## Instalación en LXC Debian 12 / Ubuntu 24.04

1. Instala o copia este repo dentro del LXC.
2. Ejecuta:

```bash
sudo bash scripts/install.sh
```

El script instala Python, Redis, Nginx, `tdl`, crea el usuario `telegramdl`, crea el virtualenv, instala dependencias, copia systemd units y arranca los servicios. También pregunta dónde guardar media/descargas y genera una contraseña web robusta.

Si necesitas cambiarlo después, edita `/etc/telegram-downloader/telegram-downloader.env`:

```bash
SECRET_KEY=...
WEB_PASSWORD=...
DOWNLOADS_DIR=/opt/tld-web/data/downloads
TDL_BINARY=/usr/local/bin/tdl
```

Luego reinicia:

```bash
sudo systemctl restart telegram-downloader-web telegram-downloader-worker
```

## Instalar tdl

`scripts/install.sh` instala `tdl` automáticamente con el instalador oficial:

```bash
curl -sSL https://docs.iyear.me/tdl/install.sh | sudo bash
```

Si ya tienes `tdl` instalado, el script lo respeta y ajusta `TDL_BINARY` en `/etc/telegram-downloader/telegram-downloader.env`.

Verifica:

```bash
sudo -u telegramdl /usr/local/bin/tdl version
```

## Ejecutar con Docker Compose

Docker no reemplaza la instalación LXC; es otra forma rápida de probar o desplegar sin instalar Python/Redis/tdl en el host. El contenedor instala `tdl`, levanta la web y un worker separado, y Redis corre como servicio aparte.

```bash
docker compose up --build
```

Abre:

```text
http://localhost:8000
```

Los datos quedan persistidos en `./data`:

```text
./data/sessions
./data/exports
./data/downloads
./data/logs
./data/telegram_downloader.sqlite3
```

Para ejecutar en segundo plano:

```bash
docker compose up -d --build
docker compose logs -f web
docker compose logs -f worker
```

Para detener:

```bash
docker compose down
```

### Login de tdl en Docker

La sesión se guarda en `./data/sessions`, compartida por `web` y `worker`. Haz login una vez así:

```bash
docker compose run --rm web \
  tdl --ns default \
  --storage type=bolt,path=/data/sessions/tdl-data \
  login --type code
```

Después levanta la app:

```bash
docker compose up -d
```

## Login de Telegram/tdl

La página `/setup` detecta si hay sesión activa y muestra el comando exacto para inicializarla. En `tdl 0.20.x`, el login es interactivo (`desktop`, `code` o `qr`) y no expone flags simples tipo `--phone --code --password`, así que debe inicializarse una sola vez por CLI:

```bash
sudo -u telegramdl HOME=/opt/tld-web/data/sessions \
  /usr/local/bin/tdl --ns default \
  --storage type=bolt,path=/opt/tld-web/data/sessions/tdl-data \
  login --type code
```

Después vuelve a `/setup`. La sesión debe aparecer activa y quedará persistida en `/opt/tld-web/data/sessions`.

## Servicios

```bash
sudo systemctl status telegram-downloader-web
sudo systemctl status telegram-downloader-worker
sudo systemctl restart telegram-downloader-web telegram-downloader-worker
sudo journalctl -u telegram-downloader-web -f
sudo journalctl -u telegram-downloader-worker -f
```

Web local:

```text
http://IP_DEL_LXC:8000
```

## Nginx opcional

```bash
sudo cp deploy/nginx/telegram-downloader.conf /etc/nginx/sites-available/telegram-downloader.conf
sudo ln -s /etc/nginx/sites-available/telegram-downloader.conf /etc/nginx/sites-enabled/telegram-downloader.conf
sudo nginx -t
sudo systemctl reload nginx
```

## Seguridad

- El wrapper de `tdl` usa `subprocess.Popen([...])` sin `shell=True`.
- `chat_id`, filtros y subcarpetas se validan con Pydantic.
- Las rutas de descarga/export/log se construyen desde directorios base controlados.
- Se bloquea path traversal con `Path.resolve()`.
- No se aceptan rutas arbitrarias de usuario.
- El servicio corre como usuario sin login `telegramdl`.
- El `.env` debe quedar con permisos `640`, dueño `root:telegramdl`.
- En producción `WEB_PASSWORD` debe existir; `scripts/install.sh` lo genera automáticamente.

## API

- `GET /health`
- `GET /api/jobs/notifications` endpoint interno usado por la UI para toasts.

Las acciones principales se hacen desde la interfaz web. La API CRUD antigua se eliminó porque duplicaba la UI y no tenía consumidores reales dentro del proyecto.

## Desarrollo local

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

Los pins actuales están validados con Python 3.11+ y también instalan correctamente en Python 3.14. Si ya tenías un `.venv` creado antes de actualizar dependencias, recrearlo suele ser más limpio:

```bash
rm -rf .venv
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

Worker:

```bash
redis-server
python -m app.rq_worker
```

Web:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Para probar sin instalar servicios, puedes dejar el worker en segundo plano y correr la web en la misma terminal:

```bash
cd /ruta/al/tdl-web
.venv/bin/python -m app.rq_worker &
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Tests:

```bash
pytest
```

## Actualizar

Desde el repo:

```bash
sudo bash scripts/update.sh
```

El script revisa cambios en el repo instalado (`/opt/tld-web/app`), sale sin detener servicios si no hay nada nuevo, detiene web + worker cuando sí hay update, respalda la base SQLite, pregunta si también quieres respaldar descargas, genera `WEB_PASSWORD` si falta, aplica `git pull --ff-only` o copia los archivos actuales, instala dependencias y vuelve a iniciar los servicios. Los backups quedan en `/opt/tld-web/data/backups`.

## Backup

Detén los servicios o haz snapshot del LXC:

```bash
sudo systemctl stop telegram-downloader-web telegram-downloader-worker
sudo tar -czf telegram-downloader-backup.tgz \
  /opt/tld-web/data/telegram_downloader.sqlite3 \
  /opt/tld-web/data/sessions \
  /opt/tld-web/data/downloads \
  /opt/tld-web/data/exports
sudo systemctl start telegram-downloader-web telegram-downloader-worker
```

## Troubleshooting

- `tdl binary not found`: ajusta `TDL_BINARY` en el env file.
- `secure-delete no está instalado`: ejecuta `sudo apt install -y secure-delete` o vuelve a correr `sudo bash scripts/update.sh`.
- Redis no disponible al crear jobs: confirma `sudo systemctl status redis-server`.
- Sesión no detectada: ejecuta el login CLI como `telegramdl`.
- Jobs quedan pending: revisa Redis y `telegram-downloader-worker`.
- Errores de permisos: confirma dueño `telegramdl:telegramdl` en `/opt/tld-web`.
- Nginx devuelve 502: revisa `systemctl status telegram-downloader-web`.
