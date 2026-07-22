from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from immich_webdav.immich_client import (
    ImmichClient,
    ImmichResponseError,
)


@pytest.fixture
def client() -> Iterator[ImmichClient]:
    instance = ImmichClient(
        "https://immich.example",
        "test-api-key",
        max_retries=2,
    )
    yield instance
    instance.close()


def test_list_albums_returns_only_json_objects(
    client: ImmichClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_request_json(
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        assert method == "GET"
        assert path == "/api/albums"
        assert json_body is None
        return [
            {"id": "album-1", "albumName": "One"},
            "invalid",
            None,
            {"id": "album-2", "albumName": "Two"},
        ]

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    assert client.list_albums() == [
        {"id": "album-1", "albumName": "One"},
        {"id": "album-2", "albumName": "Two"},
    ]


def test_list_albums_rejects_non_list_response(
    client: ImmichClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        client,
        "_request_json",
        lambda *args, **kwargs: {"id": "not-a-list"},
    )

    with pytest.raises(
        ImmichResponseError,
        match="returned a non-list response",
    ):
        client.list_albums()


def test_get_album_assets_reads_all_pages(
    client: ImmichClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    pages = iter(
        [
            {
                "assets": {
                    "items": [
                        {"id": "asset-1"},
                        {"id": "asset-2"},
                    ],
                    "nextPage": "2",
                }
            },
            {
                "assets": {
                    "items": [{"id": "asset-3"}],
                    "nextPage": None,
                }
            },
        ]
    )

    def fake_request_json(
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        assert method == "POST"
        assert path == "/api/search/metadata"
        assert json_body is not None
        calls.append(json_body)
        return next(pages)

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    assets = client.get_album_assets("album-1", page_size=100)

    assert assets == [
        {"id": "asset-1"},
        {"id": "asset-2"},
        {"id": "asset-3"},
    ]
    assert calls == [
        {
            "albumIds": ["album-1"],
            "page": 1,
            "size": 100,
        },
        {
            "albumIds": ["album-1"],
            "page": 2,
            "size": 100,
        },
    ]


@pytest.mark.parametrize(
    "next_page",
    [
        True,
        False,
        [],
        {},
        "not-a-number",
        0,
        -1,
    ],
)
def test_get_album_assets_rejects_invalid_next_page(
    client: ImmichClient,
    monkeypatch: pytest.MonkeyPatch,
    next_page: Any,
) -> None:
    monkeypatch.setattr(
        client,
        "_request_json",
        lambda *args, **kwargs: {
            "assets": {
                "items": [],
                "nextPage": next_page,
            }
        },
    )

    with pytest.raises(
        ImmichResponseError,
        match="invalid nextPage",
    ):
        client.get_album_assets("album-1")


@pytest.mark.parametrize("page_size", [0, 1001])
def test_get_album_assets_validates_page_size(
    client: ImmichClient,
    page_size: int,
) -> None:
    with pytest.raises(
        ValueError,
        match="page_size must be between 1 and 1000",
    ):
        client.get_album_assets("album-1", page_size=page_size)


def test_extract_asset_search_result_accepts_wrapped_result() -> None:
    payload = {
        "assets": {
            "items": [{"id": "asset-1"}],
            "nextPage": None,
        }
    }

    result = ImmichClient._extract_asset_search_result(payload)

    assert result == payload["assets"]


def test_extract_asset_search_result_rejects_invalid_result() -> None:
    with pytest.raises(
        ImmichResponseError,
        match="invalid assets data",
    ):
        ImmichClient._extract_asset_search_result({"assets": []})
