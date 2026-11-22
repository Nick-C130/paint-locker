FROM python:3.12-slim

LABEL org.opencontainers.image.title="Paint Locker" \
      org.opencontainers.image.description="Self-hosted miniature paint inventory, racks and mixing" \
      org.opencontainers.image.source="https://github.com/Nick-C130/paint-locker" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PAINTLOCKER_DATA_DIR=/data

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY catalogues ./catalogues

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN useradd --create-home --uid 1000 paintlocker \
    && mkdir -p /data && chown paintlocker:paintlocker /data
# The entrypoint fixes ownership of /data, then runs the app as PUID/PGID (default 1000).
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz').status == 200 else 1)"

# One worker on purpose: SQLite + a single nightly-backup task.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]
