from __future__ import annotations

import logging
import time
from typing import Any

import requests


logger = logging.getLogger(__name__)

JsonObject = dict[str, Any]


class ImmichClientError(RuntimeError):
    """Base exception for all Immich client errors."""


class ImmichRequestError(ImmichClientError):
    """Raised when an HTTP request to Immich fails."""


class ImmichResponseError(ImmichClientError):
    """Raised when Immich returns an invalid or unexpected response."""


class ImmichClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout_seconds: float = 60.0,
        max_retries: int = 3,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = (5.0, timeout_seconds)
        self._max_retries = max_retries
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "x-api-key": api_key,
            }
        )

    def close(self) -> None:
        self._session.close()

    def list_albums(self) -> list[JsonObject]:
        payload = self._request_json("GET", "/api/albums")

        if not isinstance(payload, list):
            raise ImmichResponseError(
                "GET /api/albums returned a non-list response"
            )

        return [album for album in payload if isinstance(album, dict)]


    def get_album(self, album_id: str) -> JsonObject:
        payload = self._request_json("GET", f"/api/albums/{album_id}")

        if not isinstance(payload, dict):
            raise ImmichResponseError(
                f"GET /api/albums/{album_id} returned a non-object response"
            )

        return payload

    def get_album_assets(
            self,
            album_id: str,
            *,
            page_size: int = 1000,
    ) -> list[JsonObject]:
        if not 1 <= page_size <= 1000:
            raise ValueError("page_size must be between 1 and 1000")

        assets: list[JsonObject] = []
        page = 1

        while True:
            payload = self._request_json(
                "POST",
                "/api/search/metadata",
                json_body={
                    "albumIds": [album_id],
                    "page": page,
                    "size": page_size,
                },
            )

            result = self._extract_asset_search_result(payload)

            items = result.get("items", [])
            if not isinstance(items, list):
                raise ImmichResponseError(
                    "Immich search response has invalid assets.items"
                )

            assets.extend(item for item in items if isinstance(item, dict))

            next_page = result.get("nextPage")

            if next_page is None or next_page == "":
                break

            if isinstance(next_page, bool) or not isinstance(next_page, (str, int)):
                raise ImmichResponseError(
                    f"Immich search returned an invalid nextPage: {next_page!r}"
                )

            try:
                page = int(next_page)
            except ValueError as exc:
                raise ImmichResponseError(
                    f"Immich search returned an invalid nextPage: {next_page!r}"
                ) from exc

            if page < 1:
                raise ImmichResponseError(
                    f"Immich search returned an invalid nextPage: {page}"
                )

        return assets

    @staticmethod
    def _extract_asset_search_result(payload: Any) -> JsonObject:
        if not isinstance(payload, dict):
            raise ImmichResponseError(
                "Immich search returned a non-object response"
            )

        result = payload.get("assets", payload)

        if not isinstance(result, dict):
            raise ImmichResponseError(
                "Immich search response has invalid assets data"
            )

        return result

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        json_body: JsonObject | None = None,
    ) -> Any:
        url = f"{self._base_url}{path}"

        for attempt in range(1, self._max_retries + 1):
            try:
                response = self._session.request(
                    method,
                    url,
                    json=json_body,
                    timeout=self._timeout,
                )
            except requests.RequestException as exc:
                if attempt >= self._max_retries:
                    raise ImmichRequestError(
                        f"Immich request failed after {attempt} attempts: "
                        f"{method} {url}"
                    ) from exc

                logger.warning(
                    "Immich request error for %s %s (attempt %d/%d): %s",
                    method,
                    url,
                    attempt,
                    self._max_retries,
                    exc,
                )
                time.sleep(min(2 ** (attempt - 1), 8))
                continue

            if response.ok:
                try:
                    return response.json()
                except requests.JSONDecodeError as exc:
                    raise ImmichResponseError(
                        f"Immich returned invalid JSON for {method} {url}"
                    ) from exc

            detail = self._response_detail(response)
            correlation_id = response.headers.get("X-Correlation-ID")
            message = (
                f"Immich returned HTTP {response.status_code} for {method} {url}; "
                f"correlation_id={correlation_id!r}; response={detail!r}"
            )

            retryable = response.status_code in {408, 429} or response.status_code >= 500
            if not retryable or attempt >= self._max_retries:
                raise ImmichResponseError(message)

            logger.warning(
                "%s (attempt %d/%d)",
                message,
                attempt,
                self._max_retries,
            )
            time.sleep(min(2 ** (attempt - 1), 8))

        raise AssertionError("unreachable")

    @staticmethod
    def _response_detail(response: requests.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return response.text[:500]
