# Telegram Downloader Web

Interfaz web para descargar archivos de Telegram con `tdl`. Permite filtrar por
texto, hashtag, fecha y tipo de archivo, ver el progreso y reanudar descargas.

## Instalar

En Debian 12 o Ubuntu 24.04, desde el repositorio:

```bash
sudo bash scripts/install.sh
```

El script instala las dependencias, configura los servicios y muestra la
contraseña de acceso. Abre `http://IP_DEL_SERVIDOR:8000` y entra en **Setup**
para iniciar sesión en Telegram.

## Usar

1. Selecciona un canal o grupo.
2. Elige filtros y una subcarpeta de destino.
3. Crea el job y consulta su progreso o sus archivos.

Las descargas se procesan por lotes y los fallos se reintentan hasta tres veces.
**Reintentar pendientes** continúa el mismo job conservando los archivos
completos. **Nuevo parecido** crea una búsqueda nueva.

## Carpetas

Por defecto, los datos están en `/opt/tld-web/data/`:

```text
sessions/                   Sesión de Telegram
exports/                    Exportaciones y filtros
logs/                       Logs de los jobs
backups/                    Respaldos de actualización
telegram_downloader.sqlite3 Historial y estado de descargas
downloads/                  Archivos descargados
```

Las descargas siguen esta estructura:

```text
downloads/
└── 123456789/          Canal o grupo
    └── videos/         Subcarpeta elegida
        └── job-42/     Trabajo de descarga
            └── 1501/   Mensaje de Telegram
                └── video.mp4
```

Los archivos `.tmp` generados durante la transferencia son temporales.
Reintentar mantiene las mismas carpetas. Los archivos antiguos conservan su
ubicación; «omitir iguales» reutiliza los archivos completos ya verificados.

## Configurar

Edita `/etc/telegram-downloader/telegram-downloader.env`:

```ini
WEB_PASSWORD=tu-contraseña
DOWNLOADS_DIR=/opt/tld-web/data/downloads
DOWNLOAD_BATCH_SIZE=100
EXPORT_BATCH_SIZE=5000
DOWNLOAD_IDLE_TIMEOUT_SECONDS=600
COMMAND_TIMEOUT_SECONDS=7200
```

Los tiempos están en segundos. Después de modificar la configuración:

```bash
sudo systemctl restart telegram-downloader-web telegram-downloader-worker
```

## Actualizar

```bash
sudo bash scripts/update.sh
```

Respalda SQLite, actualiza el código, ejecuta la migración y reinicia los
servicios. Conserva la configuración personalizada y permite respaldar las
descargas. Si falla, detiene los servicios y muestra el error.

## Ver logs

```bash
sudo journalctl -u telegram-downloader-web -f
sudo journalctl -u telegram-downloader-worker -f
```

Si los jobs no avanzan, revisa Redis y el worker. Si falla la sesión de Telegram,
vuelve a **Setup**.

## Desarrollo local

Requiere Python 3.12+, `tdl` y Redis instalados.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Ejecuta cada proceso en una terminal:

```bash
redis-server
.venv/bin/python -m app.rq_worker
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Abre `http://localhost:8000`. Los datos se guardan en `./data`.

```bash
.venv/bin/python -m pytest
```

## Docker

```bash
docker compose up -d --build
```

Abre `http://localhost:8000`. Los datos persisten en `./data`.
