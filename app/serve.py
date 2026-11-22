"""Container entry point: `python -m app.serve`. Logs where it listens, then runs uvicorn."""

from __future__ import annotations

import logging

import uvicorn

from . import config, logsetup


def main() -> None:
    log_config = logsetup.configure()
    log = logging.getLogger("paintlocker")
    log.info("Paint Locker %s starting: listening on http://%s:%d (inside the container), log level %s",
             config.VERSION, config.HOST, config.PORT, logsetup.level())
    if config.HOST in ("127.0.0.1", "localhost"):
        log.warning("PAINTLOCKER_HOST=%s: only reachable from inside the container. Use 0.0.0.0 with Docker.", config.HOST)
    uvicorn.run(
        "app.main:app",
        host=config.HOST,
        port=config.PORT,
        workers=1,  # one worker on purpose: SQLite + a single nightly-backup task
        proxy_headers=True,
        forwarded_allow_ips="*",
        log_config=log_config,
        log_level=logsetup.level(),
        access_log=False,
    )


if __name__ == "__main__":
    main()
