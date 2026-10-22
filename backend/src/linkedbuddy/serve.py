"""`python -m linkedbuddy.serve [--host H] [--port P] [--reload]`: run the API with uvicorn.

Refuses a non-loopback host unless ALLOW_PUBLIC_BIND=true and APP_ENV=prod (SECURITY.md 2.1): dev
serves /docs and /openapi.json, a non-Secure cookie and no HSTS. Starting uvicorn directly skips
this check, so scripts/dev.sh uses this module.
"""

import argparse
import ipaddress

import uvicorn

from linkedbuddy.config import get_settings

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


class PublicBindRefused(RuntimeError):
    pass


def is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.removeprefix("[").removesuffix("]")).is_loopback
    except ValueError:  # a hostname other than localhost may resolve anywhere
        return False


def check_bind(host: str, *, allow_public_bind: bool, app_env: str) -> None:
    """Raise PublicBindRefused for a non-loopback host unless public binding is allowed in prod."""
    if is_loopback(host) or (allow_public_bind and app_env == "prod"):
        return
    raise PublicBindRefused(
        f"refusing to bind to {host!r}: set ALLOW_PUBLIC_BIND=true and APP_ENV=prod (and put "
        "HTTPS in front) to serve beyond this machine"
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m linkedbuddy.serve")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--reload", action="store_true", help="restart on code changes (dev)")
    args = parser.parse_args(argv)
    settings = get_settings()
    try:
        check_bind(
            args.host, allow_public_bind=settings.allow_public_bind, app_env=settings.app_env
        )
    except PublicBindRefused as exc:
        parser.exit(2, f"{parser.prog}: {exc}\n")
    uvicorn.run("linkedbuddy.main:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
