FROM python:3.12-slim

LABEL org.opencontainers.image.title="Paint Locker" \
      org.opencontainers.image.description="Self-hosted miniature paint inventory, racks and mixing" \
      org.opencontainers.image.source="https://github.com/Nick-C130/paint-locker" \
      org.opencontainers.image.licenses="MIT"

ARG PAINTLOCKER_VERSION=dev
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PAINTLOCKER_DATA_DIR=/data \
    PAINTLOCKER_HOST=0.0.0.0 \
    PAINTLOCKER_PORT=8080 \
    PAINTLOCKER_VERSION=$PAINTLOCKER_VERSION \
    LOG_LEVEL=info

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY catalogues ./catalogues

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
# Strip Windows line endings (a checkout with core.autocrlf turns the shebang into "sh\r") and set the exec bit.
RUN sed -i 's/\r$//' /usr/local/bin/entrypoint.sh && chmod 755 /usr/local/bin/entrypoint.sh
RUN useradd --create-home --uid 1000 paintlocker \
    && mkdir -p /data && chown paintlocker:paintlocker /data
# The entrypoint fixes ownership of /data, then runs the app as PUID/PGID (default 1000).
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import os,sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PAINTLOCKER_PORT', '8080'), timeout=4).status == 200 else 1)"

# app.serve logs where it listens, then runs uvicorn (one worker on purpose: SQLite + one backup task).
CMD ["python", "-m", "app.serve"]
