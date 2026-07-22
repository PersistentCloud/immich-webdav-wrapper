from __future__ import annotations

import logging

from cheroot import wsgi
from wsgidav.wsgidav_app import WsgiDAVApp

from immich_webdav.config import Settings
from immich_webdav.immich_client import ImmichClient
from immich_webdav.webdav.provider import ImmichProvider


logger = logging.getLogger(__name__)


def _wsgidav_verbosity(log_level: str) -> int:
    return {
        "CRITICAL": 0,
        "FATAL": 0,
        "ERROR": 1,
        "WARNING": 2,
        "WARN": 2,
        "INFO": 3,
        "DEBUG": 4,
    }.get(log_level, 3)


def run_webdav_server(settings: Settings | None = None) -> None:
    settings = settings or Settings.from_env()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    client = ImmichClient(
        settings.immich_url,
        settings.immich_api_key,
        timeout_seconds=settings.request_timeout_seconds,
        max_retries=settings.request_max_retries,
    )

    try:
        provider = ImmichProvider(
            client,
            album_ids=settings.album_ids,
            refresh_rate_seconds=settings.refresh_rate_seconds,
            excluded_file_types=settings.excluded_file_types,
            flatten_structure=settings.flatten_asset_structure,
        )
    except Exception:
        client.close()
        raise

    config = {
        "host": settings.webdav_host,
        "port": settings.webdav_port,
        "provider_mapping": {"/": provider},
        "simple_dc": {"user_mapping": {"*": True}},
        "dir_browser": {"enable": True},
        "property_manager": None,
        "lock_storage": None,
        "verbose": _wsgidav_verbosity(settings.log_level),
    }

    app = WsgiDAVApp(config)
    server = wsgi.Server(
        bind_addr=(settings.webdav_host, settings.webdav_port),
        wsgi_app=app,
    )

    try:
        logger.info(
            "Starting WebDAV server on %s:%d",
            settings.webdav_host,
            settings.webdav_port,
        )
        server.start()
    except KeyboardInterrupt:
        logger.info("Stopping WebDAV server")
    finally:
        provider.stop_refresh()
        server.stop()
        client.close()
