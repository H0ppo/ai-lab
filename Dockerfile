FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=5000 \
    HOST=0.0.0.0 \
    SETUP_FILE=/data/setup.json \
    METRICS_DB=/data/metrics.db

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Persisted setup/metrics live in a mounted volume.
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=4s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"5000\")}/healthz')" || exit 1

# Bind to 0.0.0.0 so the app is reachable on the host IP (not localhost only).
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "8", "--timeout", "180", "app:app"]
