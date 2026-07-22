from __future__ import annotations

from pathlib import Path
from typing import Any

from immich_webdav.webdav.resources import (
    ImmichAlbumCollection,
    ImmichAsset,
    ImmichAssetCollection,
    RootCollection,
    _parse_timestamp,
)


def make_album() -> dict[str, Any]:
    image = {
        "id": "image-1",
        "originalFileName": "photo.jpg",
        "type": "IMAGE",
    }
    video = {
        "id": "video-1",
        "originalFileName": "clip.mp4",
        "type": "VIDEO",
    }
    return {
        "id": "album-1",
        "albumName": "Album",
        "processed_assets": {
            "all": {
                "photo.jpg": image,
                "clip.mp4": video,
            },
            "images": {
                "photo.jpg": image,
            },
            "videos": {
                "clip.mp4": video,
            },
        },
    }


def test_root_collection_sorts_album_names() -> None:
    root = RootCollection(
        {},
        False,
        {
            "Zoo": make_album(),
            "Album": make_album(),
        },
    )

    assert root.get_member_names() == ["Album", "Zoo"]
    assert isinstance(root.get_member("Album"), ImmichAlbumCollection)
    assert root.get_member("Missing") is None


def test_album_collection_lists_categories_when_not_flat() -> None:
    collection = ImmichAlbumCollection(
        "/Album",
        {},
        make_album(),
        False,
    )

    assert collection.get_member_names() == ("videos", "images")
    assert isinstance(collection.get_member("images"), ImmichAssetCollection)
    assert isinstance(collection.get_member("videos"), ImmichAssetCollection)
    assert collection.get_member("photo.jpg") is None


def test_album_collection_lists_assets_when_flat() -> None:
    collection = ImmichAlbumCollection(
        "/Album",
        {},
        make_album(),
        True,
    )

    assert collection.get_member_names() == ["clip.mp4", "photo.jpg"]
    assert isinstance(collection.get_member("photo.jpg"), ImmichAsset)
    assert collection.get_member("images") is None


def test_asset_collection_sorts_and_resolves_assets() -> None:
    collection = ImmichAssetCollection(
        "/Album/images",
        {},
        {
            "z.jpg": {"id": "z"},
            "a.jpg": {"id": "a"},
        },
    )

    assert collection.get_member_names() == ["a.jpg", "z.jpg"]
    assert isinstance(collection.get_member("a.jpg"), ImmichAsset)
    assert collection.get_member("missing.jpg") is None


def test_asset_reads_file_and_metadata(tmp_path: Path) -> None:
    media_path = tmp_path / "photo.jpg"
    media_path.write_bytes(b"test-image-content")

    asset = ImmichAsset(
        "/Album/photo.jpg",
        {},
        {
            "originalPath": str(media_path),
            "originalMimeType": "image/jpeg",
            "fileCreatedAt": "2025-01-02T03:04:05Z",
            "fileModifiedAt": "2025-01-03T04:05:06Z",
        },
    )

    assert asset.get_content_length() == len(b"test-image-content")
    assert asset.get_content_type() == "image/jpeg"
    assert asset.get_creation_date() is not None
    assert asset.get_last_modified() is not None
    assert asset.support_etag() is True
    assert asset.get_etag()

    with asset.get_content() as stream:
        assert stream.read() == b"test-image-content"


def test_asset_uses_default_mime_type() -> None:
    asset = ImmichAsset(
        "/Album/file.bin",
        {},
        {},
    )

    assert asset.get_content_type() == "application/octet-stream"
    assert asset.get_content_length() is None


def test_asset_without_original_path_cannot_be_opened() -> None:
    asset = ImmichAsset(
        "/Album/missing.jpg",
        {},
        {},
    )

    try:
        asset.get_content()
    except FileNotFoundError as exc:
        assert "missing originalPath" in str(exc)
    else:
        raise AssertionError("Expected FileNotFoundError")


def test_parse_timestamp_rejects_invalid_values() -> None:
    assert _parse_timestamp(None) is None
    assert _parse_timestamp("") is None
    assert _parse_timestamp("not-a-date") is None
    assert _parse_timestamp("2025-01-02T03:04:05Z") is not None
