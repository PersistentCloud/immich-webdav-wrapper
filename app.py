import hashlib
import logging
import os
import requests
import threading
import time
from cheroot import wsgi
from dotenv import load_dotenv
from wsgidav.wsgidav_app import WsgiDAVApp
from wsgidav.dav_provider import DAVProvider, DAVCollection, DAVNonCollection
from wsgidav.util import join_uri
from wsgidav import util
from dateutil.parser import isoparse

_logger = util.get_module_logger(__name__)
_logger.setLevel(logging.INFO)

# Load environment variables from .env file
load_dotenv()


class SafeNameMixin:
    """Shared helper methods for sanitizing WebDAV-visible names."""

    @staticmethod
    def sanitize_name(name, fallback):
        """Replaces path separators and normalizes empty names."""
        if not name:
            name = fallback
        sanitized = str(name).replace("/", "_").replace("\\", "_").strip()
        return sanitized or fallback

    @classmethod
    def unique_safe_name(cls, original_name, fallback, existing_names, unique_suffix=None):
        """
        Returns a sanitized, unique name for WebDAV display.

        If the sanitized name already exists, it appends a stable suffix or
        an incrementing counter to ensure each exposed WebDAV name remains unique.
        """
        base_name = cls.sanitize_name(original_name, fallback)

        if base_name not in existing_names:
            existing_names.add(base_name)
            return base_name

        suffix = unique_suffix if unique_suffix is not None else "duplicate"
        candidate = f"{base_name} [{suffix}]"
        counter = 2

        while candidate in existing_names:
            candidate = f"{base_name} [{suffix}-{counter}]"
            counter += 1

        existing_names.add(candidate)
        return candidate


class ImmichProvider(DAVProvider):
    """
    Custom WebDAV provider that maps the Immich API to a virtual filesystem.
    Handles background refreshing to prevent WebDAV PROPFIND timeouts.
    """

    def __init__(self, immich_url, api_key, album_ids, refresh_rate_hours, filetype_ignore_list, flatten_structure):
        super().__init__()
        self._count_get_resource_inst = 0

        self.immich_url = immich_url.rstrip("/")
        self.api_key = api_key
        self.album_ids = album_ids

        # Published as a single snapshot so requests see a consistent view during refreshes
        self.snapshot = {
            "albums": [],
            "album_map": {},
        }

        self.refresh_rate_seconds = refresh_rate_hours * 3600
        self.filetype_ignore_list = filetype_ignore_list
        self.flatten_structure = flatten_structure

        self.refresh_thread = threading.Thread(target=self._auto_refresh, daemon=True)
        self.stop_event = threading.Event()

        # Initial asset load before starting the server
        self.refresh_assets()

        # Start the background thread to refresh assets periodically
        self.refresh_thread.start()

    def _auto_refresh(self):
        """Background thread loop to refresh assets at the specified interval."""
        while not self.stop_event.wait(self.refresh_rate_seconds):
            _logger.info("Refreshing assets...")
            self.refresh_assets()

    def _fetch_with_retries(self, url, max_retries=3):
        """Helper function to fetch JSON data from the Immich API with retries."""
        headers = {"x-api-key": self.api_key}

        for attempt in range(1, max_retries + 1):
            try:
                response = requests.get(url, headers=headers, timeout=30)
                response.raise_for_status()
                return response.json()
            except requests.RequestException as e:
                _logger.error(f"Error fetching {url} (attempt {attempt}/{max_retries}): {e}")
                time.sleep(2)

        return None

    def _get_all_album_ids(self):
        """Fetches all album IDs available to the API key if none are explicitly provided."""
        url = f"{self.immich_url}/api/albums"
        albums = self._fetch_with_retries(url)
        if albums:
            return [album.get("id") for album in albums if album.get("id")]
        return []

    def stop_refresh(self):
        """Gracefully stops the background refresh thread."""
        self.stop_event.set()
        if self.refresh_thread.is_alive():
            self.refresh_thread.join()

    def get_resource_inst(self, path, environ):
        """
        Called by WsgiDAV for every request to resolve a path to a resource.
        Passes the current published snapshot so each request resolves paths against a consistent view.
        """
        _logger.info("get_resource_inst('%s')" % path)
        self._count_get_resource_inst += 1

        snapshot = self.snapshot
        root = RootCollection(environ, self.flatten_structure, snapshot["album_map"])
        return root.resolve("", path)

    def refresh_assets(self):
        """
        Fetches the latest album and asset data from the Immich API,
        pre-computes WebDAV paths, and performs an atomic swap.
        """
        new_album_data = []
        new_album_map = {}

        if not self.album_ids:
            _logger.info("No album IDs provided. Fetching all albums.")
            self.album_ids = self._get_all_album_ids()

        used_album_names = set()

        for album_id in self.album_ids:
            url = f"{self.immich_url}/api/albums/{album_id}"
            album_data = self._fetch_with_retries(url)

            if album_data:
                album_data = dict(album_data)  # avoid mutating original

                # Pre-process assets once per refresh to avoid heavy CPU load on PROPFIND requests
                album_data["processed_assets"] = self._pre_process_assets(
                    album_data.get("assets", [])
                )

                safe_album_name = SafeNameMixin.unique_safe_name(
                    album_data.get("albumName"),
                    fallback="Untitled Album",
                    existing_names=used_album_names,
                    unique_suffix=album_data.get("id", "unknown"),
                )

                new_album_data.append(album_data)
                new_album_map[safe_album_name] = album_data

        # Publish the new snapshot in one step so new requests see a consistent updated state
        self.snapshot = {
            "albums": new_album_data,
            "album_map": new_album_map,
        }

        asset_count = sum(album.get("assetCount", 0) for album in new_album_data)
        _logger.info(f"Loaded {asset_count} assets from the API.")

    def _pre_process_assets(self, assets):
        """
        Categorizes and sanitizes asset names in advance.
        Returns a dictionary mapping safe WebDAV names to asset payloads.
        """
        processed = {
            "all": {},
            "images": {},
            "videos": {},
        }

        used_all = set()
        used_img = set()
        used_vid = set()

        for asset in assets:
            original = asset.get("originalFileName")
            if not original:
                continue

            ext = original.split(".")[-1].lower()
            if ext in self.filetype_ignore_list:
                continue

            asset_id = asset.get("id", "unknown")
            asset_type = asset.get("type")

            safe_all = SafeNameMixin.unique_safe_name(original, "Untitled Asset", used_all, asset_id)
            processed["all"][safe_all] = asset

            if asset_type == "IMAGE":
                safe_img = SafeNameMixin.unique_safe_name(original, "Untitled Asset", used_img, asset_id)
                processed["images"][safe_img] = asset

            elif asset_type == "VIDEO":
                safe_vid = SafeNameMixin.unique_safe_name(original, "Untitled Asset", used_vid, asset_id)
                processed["videos"][safe_vid] = asset

        return processed


class RootCollection(DAVCollection):
    """Resolves top-level requests ('/') and lists available Immich albums."""

    def __init__(self, environ, flatten_structure, album_map):
        super().__init__("/", environ)
        self.flatten_structure = flatten_structure
        self._album_map = album_map

    def get_member_names(self):
        """Returns the list of album names to display in the root directory."""
        return sorted(self._album_map.keys())

    def get_member(self, name):
        """Resolves an album name to its corresponding collection object."""
        album = self._album_map.get(name)
        if not album:
            return None

        return ImmichAlbumCollection(
            join_uri(self.path, name),
            self.environ,
            album,
            self.flatten_structure,
        )


class ImmichAlbumCollection(DAVCollection):
    """Represents a specific Immich album, exposing either a flat or categorized structure."""

    def __init__(self, path, environ, album, flatten_structure):
        super().__init__(path, environ)
        self.flatten_structure = flatten_structure
        self.visible_member_names = ("videos", "images")
        self.album = album
        self._all_assets = album.get("processed_assets", {}).get("all", {})

    def get_member_names(self):
        """Returns either a flat list of all assets or category folders ('videos', 'images')."""
        if self.flatten_structure:
            return sorted(self._all_assets.keys())
        return self.visible_member_names

    def get_member(self, name):
        """Resolves a specific asset or category folder within the album."""
        if self.flatten_structure:
            asset = self._all_assets.get(name)
            if not asset:
                return None
            return ImmichAsset(join_uri(self.path, name), self.environ, asset)

        if name in self.visible_member_names:
            return ImmichAssetCollection(join_uri(self.path, name), self.environ, self.album, name)

        return None


class ImmichAssetCollection(DAVCollection):
    """Represents a categorized sub-folder (e.g., 'videos' or 'images') within an album."""

    def __init__(self, path, environ, album, group_name):
        super().__init__(path, environ)
        self.asset_map = album.get("processed_assets", {}).get(group_name, {})

    def get_member_names(self):
        return sorted(self.asset_map.keys())

    def get_member(self, name):
        asset = self.asset_map.get(name)
        if not asset:
            return None
        return ImmichAsset(join_uri(self.path, name), self.environ, asset)


class ImmichAsset(DAVNonCollection):
    """Represents an individual photo or video file mapped from the Immich local filesystem."""

    def __init__(self, path, environ, asset):
        super().__init__(path, environ)
        self.asset = asset

    def get_content_length(self):
        """Returns the file size from disk when the mounted file is available."""
        try:
            return os.path.getsize(self.asset.get("originalPath"))
        except (FileNotFoundError, TypeError):
            _logger.error("Check originalPath")
            return None

    def get_content_type(self):
        return self.asset.get("originalMimeType")

    def get_creation_date(self):
        """Parses the creation date from the Immich API payload."""
        val = self.asset.get("fileCreatedAt")
        if not val:
            return None
        try:
            return int(isoparse(val).timestamp())
        except Exception:
            return None

    def get_display_name(self):
        return self.name

    def get_display_info(self):
        return {
            "type": "File",
            "etag": self.get_etag(),
            "size": self.get_content_length(),
        }

    def get_etag(self):
        """Generates a unique ETag for WebDAV caching."""
        return (
            f"{hashlib.md5(self.path.encode()).hexdigest()}-"
            f"{util.to_str(self.get_last_modified())}-"
            f"{self.get_content_length()}"
        )

    def support_etag(self):
        return True

    def get_last_modified(self):
        """Parses the last modified date from the Immich API payload."""
        val = self.asset.get("fileModifiedAt")
        if not val:
            return None
        try:
            return int(isoparse(val).timestamp())
        except Exception:
            return None

    def get_content(self):
        """Opens and streams the file directly from the local volume mount."""
        path = self.asset.get("originalPath")
        if not path:
            raise FileNotFoundError("Missing originalPath")
        return open(path, "rb")


def run_webdav_server():
    """Initializes configurations and starts the WsgiDAV server."""
    immich_url = os.getenv("IMMICH_URL")
    api_key = os.getenv("IMMICH_API_KEY")
    album_ids_env = os.getenv("ALBUM_IDS")
    album_ids = [id.strip() for id in album_ids_env.split(",") if id.strip()] if album_ids_env else []

    refresh_rate_hours = int(os.getenv("REFRESH_RATE_HOURS", 1))
    port = int(os.getenv("WEBDAV_PORT", 1700))

    excluded_file_types = [
        x.strip().lower()
        for x in os.getenv("EXCLUDED_FILE_TYPES", "").split(",")
        if x.strip()
    ]

    flatten_structure = os.getenv("FLATTEN_ASSET_STRUCTURE", "false").lower() == "true"

    if not immich_url or not api_key:
        raise ValueError("IMMICH_URL and IMMICH_API_KEY must be set.")

    provider = ImmichProvider(
        immich_url,
        api_key,
        album_ids,
        refresh_rate_hours,
        excluded_file_types,
        flatten_structure,
    )

    config = {
        "host": "0.0.0.0",
        "port": port,
        "provider_mapping": {"/": provider},
        "simple_dc": {"user_mapping": {"*": True}},
        "directory_browser": True,
        "verbose": 2,
    }

    app = WsgiDAVApp(config)

    server = wsgi.Server(
        bind_addr=(config["host"], port),
        wsgi_app=app,
    )

    try:
        _logger.info(f"Starting WebDAV server on port {port}...")
        server.start()
    except KeyboardInterrupt:
        _logger.info("Stopping...")
    finally:
        provider.stop_refresh()
        server.stop()


if __name__ == "__main__":
    run_webdav_server()