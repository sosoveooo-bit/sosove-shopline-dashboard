FROM python:3.12-slim

LABEL org.opencontainers.image.source="https://github.com/sosoveooo-bit/sosove-shopline-dashboard"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PORT=8000 \
    DASHBOARD_SNAPSHOT_DIR=/app/runtime/snapshots

WORKDIR /app

COPY requirements.txt .
COPY shopline_monitor/requirements.txt shopline_monitor/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 dashboard \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin dashboard \
    && mkdir -p /app/runtime/snapshots /app/secrets \
    && chown -R 10001:10001 /app/runtime

COPY app.py .
COPY shopline_monitor/ shopline_monitor/

# COPY preserves NAS checkout modes (for example files 0600, directories 0700).
# Normalize public application code only; credentials and runtime data are not here.
RUN chmod 0755 /app \
    && chmod 0644 /app/app.py \
    && find /app/shopline_monitor -type d -exec chmod 0755 {} + \
    && find /app/shopline_monitor -type f -exec chmod 0644 {} +

USER 10001:10001

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.build_opener(urllib.request.ProxyHandler({})).open('http://127.0.0.1:8000/api/health', timeout=3).read()"

CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
