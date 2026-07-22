from __future__ import annotations

from typing import Any

import pytest

from immich_webdav.immich_client import ImmichRequestError
from immich_webdav.webdav.provider import (
    ImmichProvider,
    _file_extension,
    _sanitize_name,
    _unique_safe_name,
)


JsonObject = dict[str, Any]


class FakeImmichClient:
    def __init__(
        self,
        *,
        albums: list[JsonObject],
        assets_by_album: dict[str, list[JsonObject]],
    ) -> None:
        self.albums = albums
        self.assets_by_album = assets_by_album
        self.list_albums_calls = 0
        self.get_album_calls: list[str] = []
        self.get_album_assets_calls: list[str] = []
        self.fail_asset_requests = False

    def list_albums(self) -> list[JsonObject]:
        self.list_albums_calls += 1
        return [dict(album) for album in self.albums]

    def get_album(self, album_id: str) -> JsonObject:
        self.get_album_calls.append(album_id)
        for album in self.albums:
            if album.get("id") == album_id:
                return dict(album)
        raise ImmichRequestError(f"Unknown album: {album_id}")

    def get_album_assets(self, album_id: str) -> list[JsonObject]:
        self.get_album_assets_calls.append(album_id)
        if self.fail_asset_requests:
            raise ImmichRequestError("Temporary Immich failure")
        return [
            dict(asset)
            for asset in self.assets_by_album.get(album_id, [])
        ]


def create_provider(
    client: FakeImmichClient,
    *,
    album_ids: tuple[str, ...] = (),
    excluded_file_types: frozenset[str] = frozenset(),
    flatten_structure: bool = False,
) -> ImmichProvider:
    return ImmichProvider(
        client,  # type: ignore[arg-type]
        album_ids=album_ids,
        refresh_rate_seconds=3600,
        excluded_file_types=excluded_file_types,
        flatten_structure=flatten_structure,
    )


def test_initial_snapshot_sanitizes_names_and_groups_assets() -> None:
    client = FakeImmichClient(
        albums=[
            {
                "id": "album-1",
                "albumName": "Family/2025",
            }
        ],
        assets_by_album={
            "album-1": [
                {
                    "id": "image-1",
                    "originalFileName": "photo.jpg",
                    "type": "IMAGE",
                },
                {
                    "id": "image-2",
                    "originalFileName": "photo.jpg",
                    "type": "IMAGE",
                },
                {
                    "id": "video-1",
                    "originalFileName": "clip.mp4",
                    "type": "VIDEO",
                },
                {
                    "id": "excluded-1",
                    "originalFileName": "preview.PNG",
                    "type": "IMAGE",
                },
                {
                    "id": "invalid-1",
                    "type": "IMAGE",
                },
            ]
        },
    )

    provider = create_provider(
        client,
        excluded_file_types=frozenset({"png"}),
    )

    try:
        snapshot = provider._snapshot

        assert snapshot.asset_count == 3
        assert list(snapshot.album_map) == ["Family_2025"]

        album = snapshot.album_map["Family_2025"]
        processed = album["processed_assets"]

        assert list(processed["all"]) == [
            "photo.jpg",
            "photo.jpg [image-2]",
            "clip.mp4",
        ]
        assert list(processed["images"]) == [
            "photo.jpg",
            "photo.jpg [image-2]",
        ]
        assert list(processed["videos"]) == ["clip.mp4"]
    finally:
        provider.stop_refresh()


def test_explicit_album_ids_do_not_list_all_albums() -> None:
    client = FakeImmichClient(
        albums=[
            {"id": "album-1", "albumName": "One"},
            {"id": "album-2", "albumName": "Two"},
        ],
        assets_by_album={
            "album-1": [],
            "album-2": [],
        },
    )

    provider = create_provider(
        client,
        album_ids=("album-2",),
    )

    try:
        assert client.list_albums_calls == 0
        assert client.get_album_calls == ["album-2"]
        assert list(provider._snapshot.album_map) == ["Two"]
    finally:
        provider.stop_refresh()


def test_refresh_failure_retains_previous_snapshot() -> None:
    client = FakeImmichClient(
        albums=[{"id": "album-1", "albumName": "One"}],
        assets_by_album={
            "album-1": [
                {
                    "id": "asset-1",
                    "originalFileName": "photo.jpg",
                    "type": "IMAGE",
                }
            ]
        },
    )

    provider = create_provider(client)

    try:
        previous_snapshot = provider._snapshot
        client.fail_asset_requests = True

        assert provider.refresh() is False
        assert provider._snapshot is previous_snapshot
    finally:
        provider.stop_refresh()


def test_refresh_publishes_new_snapshot_after_success() -> None:
    client = FakeImmichClient(
        albums=[{"id": "album-1", "albumName": "One"}],
        assets_by_album={"album-1": []},
    )

    provider = create_provider(client)

    try:
        previous_snapshot = provider._snapshot
        client.assets_by_album["album-1"] = [
            {
                "id": "asset-1",
                "originalFileName": "new.jpg",
                "type": "IMAGE",
            }
        ]

        assert provider.refresh() is True
        assert provider._snapshot is not previous_snapshot
        assert provider._snapshot.asset_count == 1
    finally:
        provider.stop_refresh()


@pytest.mark.parametrize(
    ("value", "fallback", "expected"),
    [
        ("Album", "Fallback", "Album"),
        (" Family/2025 ", "Fallback", "Family_2025"),
        (r"Family\2025", "Fallback", "Family_2025"),
        ("", "Fallback", "Fallback"),
        (None, "Fallback", "Fallback"),
    ],
)
def test_sanitize_name(
    value: Any,
    fallback: str,
    expected: str,
) -> None:
    assert _sanitize_name(value, fallback) == expected


def test_unique_safe_name_adds_suffixes() -> None:
    existing_names: set[str] = set()

    first = _unique_safe_name(
        "photo.jpg",
        fallback="Untitled",
        existing_names=existing_names,
        unique_suffix="asset-1",
    )
    second = _unique_safe_name(
        "photo.jpg",
        fallback="Untitled",
        existing_names=existing_names,
        unique_suffix="asset-2",
    )
    third = _unique_safe_name(
        "photo.jpg",
        fallback="Untitled",
        existing_names=existing_names,
        unique_suffix="asset-2",
    )

    assert first == "photo.jpg"
    assert second == "photo.jpg [asset-2]"
    assert third == "photo.jpg [asset-2-2]"


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("photo.JPG", "jpg"),
        ("archive.tar.gz", "gz"),
        ("README", ""),
        (".hidden", "hidden"),
    ],
)
def test_file_extension(filename: str, expected: str) -> None:
    assert _file_extension(filename) == expected
