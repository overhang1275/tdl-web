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
- Muestra progreso con HTMX polling.
- Explora archivos descargados desde la UI.

## Rutas de datos

Por defecto en producción:

- `/opt/telegram-downloader/app`
- `/opt/telegram-downloader/venv`
- `/opt/telegram-downloader/data/sessions`
- `/opt/telegram-downloader/data/exports`
- `/opt/telegram-downloader/data/downloads`
- `/opt/telegram-downloader/data/logs`
- `/etc/telegram-downloader/telegram-downloader.env`

Los exports se guardan por chat:

```text
/opt/telegram-downloader/data/exports/<chat_id>/export.json
```

Cada job genera su propio filtrado:

```text
/opt/telegram-downloader/data/exports/<chat_id>/filtered-job-<job_id>.json
```

Cuando creas un job desde `/jobs`, si ya existe `export.json` para ese chat, la UI pregunta si quieres actualizarlo. Si no marcas esa opción, el job reutiliza el export existente y solo vuelve a filtrar/descargar.

## Instalación en LXC Debian 12 / Ubuntu 24.04

1. Instala o copia este repo dentro del LXC.
2. Ejecuta:

```bash
sudo bash install.sh
```

El script instala Python, Redis, Nginx, `tdl`, crea el usuario `telegramdl`, crea el virtualenv, instala dependencias, copia systemd units y arranca los servicios.

Edita `/etc/telegram-downloader/telegram-downloader.env` y ajusta:

```bash
SECRET_KEY=...
TDL_BINARY=/usr/local/bin/tdl
```

Luego reinicia:

```bash
sudo systemctl restart telegram-downloader-web telegram-downloader-worker
```

## Instalar tdl

`install.sh` instala `tdl` automáticamente con el instalador oficial:

```bash
curl -sSL https://docs.iyear.me/tdl/install.sh | sudo bash
```

Si ya tienes `tdl` instalado, el script lo respeta y ajusta `TDL_BINARY` en `/etc/telegram-downloader/telegram-downloader.env`.

Verifica:

```bash
sudo -u telegramdl /usr/local/bin/tdl version
```

## Login de Telegram/tdl

La página `/setup` detecta si hay sesión activa y muestra el comando exacto para inicializarla. En `tdl 0.20.x`, el login es interactivo (`desktop`, `code` o `qr`) y no expone flags simples tipo `--phone --code --password`, así que debe inicializarse una sola vez por CLI:

```bash
sudo -u telegramdl HOME=/opt/telegram-downloader/data/sessions \
  /usr/local/bin/tdl --ns default \
  --storage type=bolt,path=/opt/telegram-downloader/data/sessions/tdl-data \
  login --type code
```

Después vuelve a `/setup`. La sesión debe aparecer activa y quedará persistida en `/opt/telegram-downloader/data/sessions`.

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

- El wrapper de `tdl` usa `subprocess.run([...])` sin `shell=True`.
- `chat_id`, filtros y subcarpetas se validan con Pydantic.
- Las rutas de descarga/export/log se construyen desde directorios base controlados.
- Se bloquea path traversal con `Path.resolve()`.
- No se aceptan rutas arbitrarias de usuario.
- El servicio corre como usuario sin login `telegramdl`.
- El `.env` debe quedar con permisos `640`, dueño `root:telegramdl`.

## API

- `GET /health`
- `GET /api/session/status`
- `POST /api/session/login`
- `GET /api/chats`
- `POST /api/jobs`
- `GET /api/jobs`
- `GET /api/jobs/{job_id}`
- `GET /api/jobs/{job_id}/logs`
- `GET /api/jobs/{job_id}/files`

Ejemplo:

```bash
curl -X POST http://127.0.0.1:8000/api/jobs \
  -H 'Content-Type: application/json' \
  -d '{"chat_id":"xxxxxxxxxx","media_type":"video","output_subfolder":"videos","skip_same":true}'
```

## Desarrollo local

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
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

Tests:

```bash
pytest
```

## Backup

Detén los servicios o haz snapshot del LXC:

```bash
sudo systemctl stop telegram-downloader-web telegram-downloader-worker
sudo tar -czf telegram-downloader-backup.tgz \
  /opt/telegram-downloader/data/telegram_downloader.sqlite3 \
  /opt/telegram-downloader/data/sessions \
  /opt/telegram-downloader/data/downloads \
  /opt/telegram-downloader/data/exports
sudo systemctl start telegram-downloader-web telegram-downloader-worker
```

## Troubleshooting

- `tdl binary not found`: ajusta `TDL_BINARY` en el env file.
- Redis no disponible al crear jobs: confirma `sudo systemctl status redis-server`.
- Sesión no detectada: ejecuta el login CLI como `telegramdl`.
- Jobs quedan pending: revisa Redis y `telegram-downloader-worker`.
- Errores de permisos: confirma dueño `telegramdl:telegramdl` en `/opt/telegram-downloader`.
- Nginx devuelve 502: revisa `systemctl status telegram-downloader-web`.
