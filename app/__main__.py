"""Start one Uvicorn worker with the application's settings."""

import uvicorn
from uvicorn.config import STARTUP_FAILURE

from app.config import Settings, get_settings


def build_uvicorn_config(settings: Settings) -> uvicorn.Config:
    return uvicorn.Config(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
        workers=1,
        lifespan="on",
        access_log=False,
        # The app needs the original peer to validate the forwarded chain.
        proxy_headers=False,
        forwarded_allow_ips=[str(network) for network in settings.trusted_proxies],
    )


def main() -> None:
    settings = get_settings()
    try:
        settings.validate_startup()
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from None
    server = uvicorn.Server(build_uvicorn_config(settings))
    server.run()
    if not server.started:
        raise SystemExit(STARTUP_FAILURE)


if __name__ == "__main__":
    main()
