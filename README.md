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
- Elimina jobs en background con `wipe` (`wipe -rfiq`) antes de borrar el registro, mostrando la salida en logs para evitar timeouts de túneles.
- Registra eliminaciones y fallos en `/opt/tld-web/data/logs/deleted-jobs.log`.
- Si la carpeta del job ya no existe, elimina solo el registro y deja rastro en logs.
- Muestra progreso con HTMX polling.
- Explora archivos descargados desde la UI.
- Descarga por lotes independientes con registro por archivo y botón **Reintentar pendientes**.

## Descargas grandes y reanudación

Cada job conserva su selección y el estado de cada archivo en SQLite. El botón
**Reintentar pendientes** continúa el mismo job sin volver a descargar sus archivos
verificados. Aparece también si el job falla mientras estás mirando el progreso.
Los temporales de tdl no cuentan como completados. Al reintentar se comprueba que
los archivos registrados siguen existiendo y tienen el tamaño guardado.

La exportación se divide en rangos de IDs y guarda su cursor. El cache del canal
se reemplaza únicamente al completar todos los rangos. Cada lote de descarga y
cada rango de exportación se ejecuta en una invocación RQ independiente.
Los errores de descarga se reintentan hasta tres veces, aislando los mensajes
problemáticos y esperando 15 y 60 segundos. Otros archivos continúan; los que
agotan sus intentos quedan disponibles para reintento manual.

Configuración opcional: `DOWNLOAD_BATCH_SIZE=100`, `EXPORT_BATCH_SIZE=5000`,
`DOWNLOAD_IDLE_TIMEOUT_SECONDS=600`. `COMMAND_TIMEOUT_SECONDS` sigue siendo el
límite máximo de cada invocación tdl. El scheduler de `app.rq_worker` debe estar
activo para los reintentos con espera (ya se inicia con `with_scheduler=True`).

Las nuevas descargas se guardan en la subcarpeta `job-<id>/<mensaje>/` del destino.
Las descargas verificadas de otros jobs del mismo canal y destino se reutilizan
cuando «omitir iguales» está activo. El JSON histórico de IDs se conserva, pero
no se toma como prueba de que un archivo existe. Usa **Nuevo parecido** para
hacer una búsqueda nueva; reintentar conserva la búsqueda original.

## Tecnologías y librerías

- Python 3.12+.
- FastAPI `0.136.3` para la aplicación web.
- Uvicorn `0.34.0` como servidor ASGI.
- Jinja2 `3.1.6` para templates HTML.
- HTMX `1.9.12` para polling y actualizaciones parciales en la UI.
- SQLAlchemy `2.0.50` con SQLite para persistencia local.
- Redis `5.2.1` + RQ `2.0.0` para cola y worker de jobs.
- Pydantic `2.13.4` para configuración/modelos de entrada.
- python-multipart `0.0.20` para formularios.
- `tdl` como CLI de Telegram.
- `wipe` para borrado seguro de carpetas de jobs.
- Nginx y systemd para despliegue en LXC Debian/Ubuntu.
- Docker Compose opcional con Redis `7-alpine`.
- pytest `8.3.4` y httpx `0.28.1` para pruebas.

## Rutas de datos

Por defecto en producción:

- `/opt/tld-web/app`
- `/opt/tld-web/venv`
- `/opt/tld-web/data/sessions`
- `/opt/tld-web/data/exports`
- `/opt/tld-web/data/downloads`
- `/opt/tld-web/data/logs`
- `/etc/telegram-downloader/telegram-downloader.env`

### Carpetas de descargas

La carpeta base se configura con `DOWNLOADS_DIR`. Las nuevas descargas siguen
este formato:

```text
<DOWNLOADS_DIR>/<chat_id>/<subcarpeta>/job-<job_id>/<message_id>/<archivo>
```

Por ejemplo:

```text
/opt/tld-web/data/downloads/
└── 123456789/              # Canal o grupo
    └── videos/             # Subcarpeta elegida al crear el job
        └── job-42/         # Trabajo de descarga
            ├── 1501/       # Mensaje de Telegram
            │   └── video.mp4
            └── 1502/
                └── documento.pdf
```

- **Canal o grupo:** separa el contenido de cada chat. Su ID se normaliza para
  poder utilizarlo como nombre de carpeta.
- **Subcarpeta:** es el destino elegido al crear el job, por ejemplo `videos`,
  `documentos` o `download`.
- **Job:** identifica el trabajo que descargó los archivos.
- **Mensaje:** relaciona el archivo con su mensaje de Telegram, incluso si otros
  mensajes tienen archivos con el mismo nombre.

Durante la transferencia, tdl escribe un temporal como `video.mp4.tmp`. Cuando
la descarga termina correctamente, lo renombra a `video.mp4` y el sistema
registra el archivo completo en SQLite.

**Reintentar pendientes** conserva el mismo ID de job y las mismas carpetas;
continúa con los archivos pendientes o fallidos. Los archivos antiguos conservan
su ubicación. Si está activo «omitir iguales» y otro job reutiliza un archivo
verificado del mismo canal y destino, ese archivo permanece en su ubicación
original y se registra la referencia sin crear otra copia.

### Carpetas de exportación

Los exports se guardan por chat:

```text
/opt/tld-web/data/exports/<chat_id>/export.json
```

Cada job genera su propio filtrado:

```text
/opt/tld-web/data/exports/<chat_id>/filtered-job-<job_id>.json
```

También se guarda `export-job-<job_id>.json`, que conserva la exportación usada
por ese trabajo. La carpeta `export-job-<job_id>/` contiene `latest.json` y los
fragmentos `<id_inicial>-<id_final>.json` que permiten reanudar la exportación.
`export.json` se reemplaza solo cuando todos los rangos están completos.

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

El script revisa cambios en el repo instalado (`/opt/tld-web/app`), sale sin detener servicios si no hay cambios de código ni de configuración, detiene web + worker cuando sí hay update, respalda SQLite incluyendo datos pendientes del WAL y pregunta si también quieres respaldar descargas. Genera `WEB_PASSWORD` si falta, añade los valores de descarga por lotes sin reemplazar valores personalizados, aplica `git pull --ff-only` o copia los archivos actuales e instala dependencias.

Antes de iniciar los servicios ejecuta la migración con el usuario y el archivo de entorno del servicio. Si falla la actualización o la migración, detiene los servicios y muestra el error; no reinicia automáticamente con una base incompatible. Al terminar comprueba que web y worker estén activos. La instalación también ejecuta la migración antes del primer inicio.

Los backups quedan en `/opt/tld-web/data/backups`: `update-<fecha>.sqlite3` para la base y, si lo solicitas, `downloads-<fecha>.tgz` para las descargas.

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
- `wipe no está instalado`: ejecuta `sudo apt install -y wipe` o vuelve a correr `sudo bash scripts/update.sh`.
- Redis no disponible al crear jobs: confirma `sudo systemctl status redis-server`.
- Sesión no detectada: ejecuta el login CLI como `telegramdl`.
- Jobs quedan pending: revisa Redis y `telegram-downloader-worker`.
- Errores de permisos: confirma dueño `telegramdl:telegramdl` en `/opt/tld-web`.
- Nginx devuelve 502: revisa `systemctl status telegram-downloader-web`.
