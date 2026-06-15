FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=production \
    DATA_DIR=/data \
    TDL_SESSIONS_DIR=/data/sessions \
    EXPORTS_DIR=/data/exports \
    DOWNLOADS_DIR=/data/downloads \
    LOGS_DIR=/data/logs \
    DATABASE_URL=sqlite:////data/telegram_downloader.sqlite3 \
    REDIS_URL=redis://redis:6379/0 \
    TDL_BINARY=tdl \
    TDL_NAMESPACE=default \
    COMMAND_TIMEOUT_SECONDS=7200

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash curl ca-certificates gzip tar unzip \
    && curl -sSL https://docs.iyear.me/tdl/install.sh | bash \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip wheel \
    && pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN mkdir -p /data/sessions /data/exports /data/downloads /data/logs

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
