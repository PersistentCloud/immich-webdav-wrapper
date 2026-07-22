from __future__ import annotations

import hashlib
import logging
import os
from typing import Any, BinaryIO, Mapping

from dateutil.parser import isoparse
from wsgidav.dav_provider import DAVCollection, DAVNonCollection
from wsgidav.util import join_uri


logger = logging.getLogger(__name__)

Asset = Mapping[str, Any]
Album = Mapping[str, Any]
AlbumMap = Mapping[str, Album]


class RootCollection(DAVCollection):
    """Top-level collection containing the configured Immich albums."""

    def __init__(
        self,
        environ: dict[str, Any],
        flatten_structure: bool,
        album_map: AlbumMap,
    ) -> None:
        super().__init__("/", environ)
        self._flatten_structure = flatten_structure
        self._album_map = album_map

    def get_member_names(self) -> list[str]:
        return sorted(self._album_map)

    def get_member(self, name: str) -> ImmichAlbumCollection | None:
        album = self._album_map.get(name)
        if album is None:
            return None

        return ImmichAlbumCollection(
            join_uri(self.path, name),
            self.environ,
            album,
            self._flatten_structure,
        )


class ImmichAlbumCollection(DAVCollection):
    """Collection representing one Immich album."""

    _CATEGORY_NAMES = ("videos", "images")

    def __init__(
        self,
        path: str,
        environ: dict[str, Any],
        album: Album,
        flatten_structure: bool,
    ) -> None:
        super().__init__(path, environ)
        self._album = album
        self._flatten_structure = flatten_structure
        self._all_assets = self._processed_assets("all")

    def get_member_names(self) -> list[str] | tuple[str, str]:
        if self._flatten_structure:
            return sorted(self._all_assets)
        return self._CATEGORY_NAMES

    def get_member(self, name: str) -> DAVCollection | DAVNonCollection | None:
        if self._flatten_structure:
            asset = self._all_assets.get(name)
            if asset is None:
                return None
            return ImmichAsset(join_uri(self.path, name), self.environ, asset)

        if name not in self._CATEGORY_NAMES:
            return None

        return ImmichAssetCollection(
            join_uri(self.path, name),
            self.environ,
            self._processed_assets(name),
        )

    def _processed_assets(self, group_name: str) -> Mapping[str, Asset]:
        processed = self._album.get("processed_assets", {})
        if not isinstance(processed, Mapping):
            return {}

        assets = processed.get(group_name, {})
        return assets if isinstance(assets, Mapping) else {}


class ImmichAssetCollection(DAVCollection):
    """Collection containing assets of one category, such as images or videos."""

    def __init__(
        self,
        path: str,
        environ: dict[str, Any],
        asset_map: Mapping[str, Asset],
    ) -> None:
        super().__init__(path, environ)
        self._asset_map = asset_map

    def get_member_names(self) -> list[str]:
        return sorted(self._asset_map)

    def get_member(self, name: str) -> ImmichAsset | None:
        asset = self._asset_map.get(name)
        if asset is None:
            return None
        return ImmichAsset(join_uri(self.path, name), self.environ, asset)


class ImmichAsset(DAVNonCollection):
    """Read-only WebDAV file backed by an Immich originalPath."""

    def __init__(
        self,
        path: str,
        environ: dict[str, Any],
        asset: Asset,
    ) -> None:
        super().__init__(path, environ)
        self._asset = asset

    def get_content_length(self) -> int | None:
        original_path = self._asset.get("originalPath")
        if not isinstance(original_path, str) or not original_path:
            return None

        try:
            return os.path.getsize(original_path)
        except OSError:
            logger.warning("Cannot read asset path: %s", original_path)
            return None

    def get_content_type(self) -> str:
        mime_type = self._asset.get("originalMimeType")
        return mime_type if isinstance(mime_type, str) else "application/octet-stream"

    def get_creation_date(self) -> int | None:
        return _parse_timestamp(self._asset.get("fileCreatedAt"))

    def get_display_name(self) -> str:
        return self.name

    def get_display_info(self) -> dict[str, Any]:
        return {
            "type": "File",
            "etag": self.get_etag(),
            "size": self.get_content_length(),
        }

    def get_etag(self) -> str:
        path_hash = hashlib.md5(
            self.path.encode("utf-8"), usedforsecurity=False
        ).hexdigest()
        return f"{path_hash}-{self.get_last_modified()}-{self.get_content_length()}"

    def support_etag(self) -> bool:
        return True

    def get_last_modified(self) -> int | None:
        return _parse_timestamp(self._asset.get("fileModifiedAt"))

    def get_content(self) -> BinaryIO:
        original_path = self._asset.get("originalPath")
        if not isinstance(original_path, str) or not original_path:
            raise FileNotFoundError("Asset is missing originalPath")
        return open(original_path, "rb")


def _parse_timestamp(value: Any) -> int | None:
    if not isinstance(value, str) or not value:
        return None

    try:
        return int(isoparse(value).timestamp())
    except (TypeError, ValueError, OverflowError):
        return None
