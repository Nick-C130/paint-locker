# Security policy

Paint Locker is designed to run on a home server behind your own reverse proxy or VPN. It has an
optional single shared password (`PAINTLOCKER_PASSWORD`) and does not terminate TLS itself.
Please don't expose it directly to the internet without HTTPS and that password set.

## Reporting a vulnerability

Please report vulnerabilities privately with GitHub's
[private vulnerability reporting](https://github.com/Nick-C130/paint-locker/security/advisories/new)
rather than a public issue. Include steps to reproduce and the version (image tag or commit).

You should get a reply within a week. Fixes go out as a new image on `main`; the advisory is
published once a fix is available.

## Supported versions

Only the latest image (`ghcr.io/nick-c130/paint-locker:latest`) and the `main` branch get fixes.
