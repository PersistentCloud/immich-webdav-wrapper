from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, Mapping, TypeAlias

from wsgidav.dav_provider import DAVProvider

from immich_webdav.immich_client import ImmichClient, ImmichClientError
from immich_webdav.webdav.resources import RootCollection


logger = logging.getLogger(__name__)

JsonObject: TypeAlias = dict[str, Any]
AssetMap: TypeAlias = dict[str, JsonObject]
AssetGroup: TypeAlias = Literal["all", "images", "videos"]
ProcessedAssets: TypeAlias = dict[AssetGroup, AssetMap]


@dataclass(frozen=True, slots=True)
class CatalogSnapshot:
    albums: tuple[JsonObject, ...]
    album_map: Mapping[str, JsonObject]
    asset_count: int


class ImmichProvider(DAVProvider):
    """Read-only DAV provider that publishes an atomically refreshed catalog."""

    def __init__(
        self,
        client: ImmichClient,
        *,
        album_ids: tuple[str, ...],
        refresh_rate_seconds: float,
        excluded_file_types: frozenset[str],
        flatten_structure: bool,
    ) -> None:
        super().__init__()
        self._client = client
        self._album_ids = album_ids
        self._refresh_rate_seconds = refresh_rate_seconds
        self._excluded_file_types = excluded_file_types
        self._flatten_structure = flatten_structure
        self._stop_event = threading.Event()

        # The first load is intentionally synchronous. Invalid credentials or an
        # incompatible API should fail container startup instead of serving an
        # unexplained empty WebDAV share.
        self._snapshot = self._build_snapshot()
        self._log_snapshot(self._snapshot, prefix="Initial catalog loaded")

        self._refresh_thread = threading.Thread(
            target=self._auto_refresh,
            name="immich-catalog-refresh",
            daemon=True,
        )
        self._refresh_thread.start()

    def is_readonly(self) -> bool:
        return True

    def get_resource_inst(self, path: str, environ: dict[str, Any]):
        snapshot = self._snapshot
        root = RootCollection(
            environ,
            self._flatten_structure,
            snapshot.album_map,
        )
        return root.resolve("", path)

    def refresh(self) -> bool:
        try:
            new_snapshot = self._build_snapshot()
        except ImmichClientError:
            logger.exception(
                "Catalog refresh failed; retaining the previous snapshot"
            )
            return False

        self._snapshot = new_snapshot
        self._log_snapshot(new_snapshot, prefix="Catalog refreshed")
        return True

    def stop_refresh(self) -> None:
        self._stop_event.set()
        if self._refresh_thread.is_alive():
            self._refresh_thread.join()

    def _auto_refresh(self) -> None:
        while not self._stop_event.wait(self._refresh_rate_seconds):
            try:
                self.refresh()
            except Exception:  # noqa: BLE001 - background-thread safety boundary
                logger.exception(
                    "Unexpected error in catalog refresh thread; "
                    "retaining the previous snapshot"
                )

    def _build_snapshot(self) -> CatalogSnapshot:
        albums = self._load_albums()
        album_records: list[JsonObject] = []
        album_map: dict[str, JsonObject] = {}
        used_album_names: set[str] = set()
        asset_count = 0

        for album in albums:
            album_id = album.get("id")
            if not isinstance(album_id, str) or not album_id:
                logger.warning("Skipping album without a valid id: %r", album)
                continue

            assets = self._client.get_album_assets(album_id)
            processed_assets = self._preprocess_assets(assets)

            album_record = dict(album)
            album_record["processed_assets"] = processed_assets

            safe_album_name = _unique_safe_name(
                album.get("albumName"),
                fallback="Untitled Album",
                existing_names=used_album_names,
                unique_suffix=album_id,
            )

            album_records.append(album_record)
            album_map[safe_album_name] = album_record
            asset_count += len(processed_assets["all"])

        return CatalogSnapshot(
            albums=tuple(album_records),
            album_map=MappingProxyType(album_map),
            asset_count=asset_count,
        )

    def _load_albums(self) -> list[JsonObject]:
        if self._album_ids:
            return [self._client.get_album(album_id) for album_id in self._album_ids]
        return self._client.list_albums()

    def _preprocess_assets(self, assets: list[JsonObject]) -> ProcessedAssets:
        processed: ProcessedAssets = {
            "all": {},
            "images": {},
            "videos": {},
        }

        used_names: dict[AssetGroup, set[str]] = {
            "all": set(),
            "images": set(),
            "videos": set(),
        }

        asset_type_groups: dict[str, AssetGroup] = {
            "IMAGE": "images",
            "VIDEO": "videos",
        }

        for asset in assets:
            original_name = asset.get("originalFileName")
            if not isinstance(original_name, str) or not original_name:
                continue

            extension = _file_extension(original_name)
            if extension in self._excluded_file_types:
                continue

            asset_id = asset.get("id")
            unique_suffix = (
                asset_id
                if isinstance(asset_id, str) and asset_id
                else "unknown"
            )

            all_name = _unique_safe_name(
                original_name,
                fallback="Untitled Asset",
                existing_names=used_names["all"],
                unique_suffix=unique_suffix,
            )
            processed["all"][all_name] = asset

            asset_type = asset.get("type")
            if not isinstance(asset_type, str):
                continue

            group_name = asset_type_groups.get(asset_type)
            if group_name is None:
                continue

            category_name = _unique_safe_name(
                original_name,
                fallback="Untitled Asset",
                existing_names=used_names[group_name],
                unique_suffix=unique_suffix,
            )
            processed[group_name][category_name] = asset

        return processed

    @staticmethod
    def _log_snapshot(snapshot: CatalogSnapshot, *, prefix: str) -> None:
        logger.info(
            "%s: %d albums, %d assets",
            prefix,
            len(snapshot.albums),
            snapshot.asset_count,
        )


def _sanitize_name(value: Any, fallback: str) -> str:
    name = str(value).strip() if value is not None else ""
    name = name.replace("/", "_").replace("\\", "_")
    return name or fallback


def _unique_safe_name(
    value: Any,
    *,
    fallback: str,
    existing_names: set[str],
    unique_suffix: str,
) -> str:
    base_name = _sanitize_name(value, fallback)
    if base_name not in existing_names:
        existing_names.add(base_name)
        return base_name

    candidate = f"{base_name} [{unique_suffix}]"
    counter = 2
    while candidate in existing_names:
        candidate = f"{base_name} [{unique_suffix}-{counter}]"
        counter += 1

    existing_names.add(candidate)
    return candidate


def _file_extension(filename: str) -> str:
    _, separator, extension = filename.rpartition(".")
    return extension.lower() if separator else ""
