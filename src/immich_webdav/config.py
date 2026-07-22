from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from dotenv import load_dotenv


_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


@dataclass(frozen=True, slots=True)
class Settings:
    immich_url: str
    immich_api_key: str
    album_ids: tuple[str, ...]
    refresh_rate_hours: float
    excluded_file_types: frozenset[str]
    flatten_asset_structure: bool
    webdav_host: str
    webdav_port: int
    request_timeout_seconds: float
    request_max_retries: int
    log_level: str

    @property
    def refresh_rate_seconds(self) -> float:
        return self.refresh_rate_hours * 3600

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()

        immich_url = _required_env("IMMICH_URL").rstrip("/")
        immich_api_key = _required_env("IMMICH_API_KEY")

        album_ids = tuple(
            value.strip()
            for value in os.getenv("ALBUM_IDS", "").split(",")
            if value.strip()
        )

        excluded_file_types = frozenset(
            value.strip().lower().lstrip(".")
            for value in os.getenv("EXCLUDED_FILE_TYPES", "").split(",")
            if value.strip()
        )

        refresh_rate_hours = _positive_float("REFRESH_RATE_HOURS", default=1.0)
        webdav_port = _integer("WEBDAV_PORT", default=1700, minimum=1, maximum=65535)
        request_timeout_seconds = _positive_float(
            "IMMICH_REQUEST_TIMEOUT_SECONDS", default=60.0
        )
        request_max_retries = _integer(
            "IMMICH_REQUEST_MAX_RETRIES", default=3, minimum=1, maximum=10
        )

        log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper()
        if log_level not in logging.getLevelNamesMapping():
            raise ValueError(f"Invalid LOG_LEVEL: {log_level!r}")

        return cls(
            immich_url=immich_url,
            immich_api_key=immich_api_key,
            album_ids=album_ids,
            refresh_rate_hours=refresh_rate_hours,
            excluded_file_types=excluded_file_types,
            flatten_asset_structure=_boolean("FLATTEN_ASSET_STRUCTURE", default=False),
            webdav_host=os.getenv("WEBDAV_HOST", "0.0.0.0").strip() or "0.0.0.0",
            webdav_port=webdav_port,
            request_timeout_seconds=request_timeout_seconds,
            request_max_retries=request_max_retries,
            log_level=log_level,
        )


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"{name} must be set")
    return value


def _boolean(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default

    value = raw.strip().lower()
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    raise ValueError(
        f"{name} must be one of: true, false, 1, 0, yes, no, on, off"
    )


def _positive_float(name: str, *, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default

    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc

    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def _integer(name: str, *, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default

    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc

    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value
