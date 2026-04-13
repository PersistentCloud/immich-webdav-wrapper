import hashlib
import logging
import os
import requests
import threading
import time
import json
from cheroot import wsgi
from datetime import datetime
from dateutil.parser import *
from dotenv import load_dotenv
from wsgidav.wsgidav_app import WsgiDAVApp
from wsgidav.dav_provider import DAVProvider, DAVCollection, DAVNonCollection
from wsgidav.util import join_uri
from wsgidav import util

_logger = util.get_module_logger(__name__)
_logger.setLevel(logging.INFO)

# Load environment variables from .env file
load_dotenv()

class ImmichProvider(DAVProvider):
    def __init__(self, immich_url, api_key, album_ids, refresh_rate_hours, filetype_ignore_list, flatten_structure):
        super().__init__()
        self.immich_url = immich_url
        self.api_key = api_key
        self.album_ids = album_ids
        self.all_album_data = []
        self.refresh_rate_seconds = refresh_rate_hours * 3600  # Convert hours to seconds
        self.filetype_ignore_list = filetype_ignore_list
        self.flatten_structure = flatten_structure
        self.refresh_thread = threading.Thread(target=self._auto_refresh)
        self.stop_event = threading.Event()

        # Initial asset load
        self.refresh_assets()

        # Start the background thread to refresh assets periodically
        self.refresh_thread.start()
        
    def _auto_refresh(self):
        """Background thread method to refresh assets periodically."""
        while not self.stop_event.wait(self.refresh_rate_seconds):
            _logger.info("Refreshing assets...")
            self.refresh_assets()

    def _fetch_with_retries(self, url, max_retries=3):
        """Helper function to fetch data with retries."""
        headers = {'x-api-key': self.api_key}
        for attempt in range(1, max_retries + 1):
            try:
                response = requests.get(url, headers=headers)
                response.raise_for_status()
                return response.json()
            except requests.RequestException as e:
                _logger.error(f"Error fetching {url} (attempt {attempt}/{max_retries}): {e}")
                time.sleep(2)
        return None  # Return None if all retries fail

    def _get_all_album_ids(self):
        url = f"{self.immich_url}/api/albums"
        albums = self._fetch_with_retries(url)
        if albums:
            return [album.get('id', 0) for album in albums]
        
    def stop_refresh(self):
        """Stop the background refresh thread."""
        self.stop_event.set()
        self.refresh_thread.join()
        
    def get_resource_inst(self, path, environ):
        _logger.info("get_resource_inst('%s')" % path)
        self._count_get_resource_inst += 1
        root = RootCollection(environ, self.flatten_structure)
        return root.resolve("", path)
    
    def refresh_assets(self):
        # Clear all_album_data to store only fresh data
        self.all_album_data = []
        if not self.album_ids:
            _logger.info("No album Ids provided. Fetching all albums.")
            self.album_ids = self._get_all_album_ids()
        for album_id in self.album_ids:
            url = f"{self.immich_url}/api/albums/{album_id}"
            album_data = self._fetch_with_retries(url)
            if album_data:
                self.all_album_data.append(album_data)
        asset_count = sum(album.get('assetCount', 0) for album in self.all_album_data)
        _logger.info(f"Loaded {asset_count} assets from the API.")

        
class RootCollection(DAVCollection):
    """Resolve top-level requests '/'."""

    def __init__(self, environ, flatten_structure):
        super().__init__("/", environ)
        self.flatten_structure = flatten_structure
        
    def _sanitize_name(self, name):
        """Replace illegal filesystem characters to prevent WebDAV crashes."""
        if not name:
            return "Untitled Album"
        # Replace forward slashes (and backslashes just to be safe) with underscores
        return name.replace("/", "_").replace("\\", "_")

    def _get_album(self, name):
        for album in self.provider.all_album_data:
            if self._sanitize_name(album.get("albumName")) == name:
                return album
        return None

    def get_member_names(self):
        # Present the sanitized names to the WebDAV client
        return [self._sanitize_name(entry.get('albumName')) for entry in self.provider.all_album_data]

    def get_member(self, name):
        # Match the WebDAV requested name (which is sanitized) against our sanitized album list
        album = next((entry for entry in self.provider.all_album_data 
                      if self._sanitize_name(entry.get('albumName')) == name), None)
        
        if not album:
            return None
            
        return ImmichAlbumCollection(
            join_uri(self.path, name), 
            self.environ,
            album,
            self.flatten_structure
        )

class ImmichAlbumCollection(DAVCollection):
    def __init__(self, path: str, environ: dict, album: dict, flatten_structure: bool):
        super().__init__(path, environ)
        self.flatten_structure = flatten_structure  # Whether to flatten the structure
        self.visibleMemberNames = ('videos', 'images')
        self.album = album
        
    def get_member_names(self):
        if self.flatten_structure:
            # If flatten_structure is True, return all asset names (both videos and images)
            return sorted(self._get_all_assets().keys())
        else:
            # Otherwise, return the subcollections (images, videos)
            return self.visibleMemberNames
    
    def get_member(self, name):
        if self.flatten_structure:
            # If flatten_structure is True, return the asset directly
            asset = self._get_all_assets().get(name)
            return ImmichAsset(join_uri(self.path, name), self.environ, asset)
        else:
            # If flatten_structure is False, return the subcollections (images, videos)
            if name in self.visibleMemberNames:
                return ImmichAssetCollection(join_uri(self.path, name), self.environ, self.album)
            return None

    def _get_all_assets(self):
        """Return a dictionary of all assets (both images and videos) for the album."""
        all_assets = {}
        for asset in self.album.get('assets', []):
            
            # Extract file extension and check against excluded_file_types
            file_extension = asset['originalFileName'].split(".")[-1].lower()
            
            if file_extension in self.provider.filetype_ignore_list:
                continue  # Skip this asset if its type is in the excluded list
            
            asset_name = asset['originalFileName']
            all_assets[asset_name] = asset
        return all_assets


class ImmichAssetCollection(DAVCollection):
    def __init__(self, path, environ, album):
        super().__init__(path, environ)
        self.album = album
        self.asset_map = self._sort_assets()
    
    def _sort_assets(self):
        imageAssets = {}
        videoAssets = {}
        
        for asset in self.album.get('assets', []):
            asset_type = asset.get('type')
            # Extract file extension and check against excluded_file_types
            file_extension = asset['originalFileName'].split(".")[-1].lower()
            
            if file_extension in self.provider.filetype_ignore_list:
                continue  # Skip this asset if its type is in the excluded list
            
            # Sort assets by type
            if asset_type == 'IMAGE':
                imageAssets[asset['originalFileName']] = asset
            elif asset_type == 'VIDEO':
                videoAssets[asset['originalFileName']] = asset
                
        return {
            "videos": videoAssets,
            "images": imageAssets,
        }

    def get_member_names(self):
        if self.name not in self.asset_map:
            raise ValueError(f"Unsupported member name: {self.name}")

        return sorted(self.asset_map[self.name].keys())

    def get_member(self, name):
        return ImmichAsset(join_uri(self.path, name), self.environ, self.asset_map[self.name].get(name))
        
        
class ImmichAsset(DAVNonCollection):
    def __init__(self, path, environ, asset):
        super().__init__(path, environ)
        self.asset = asset
        
    def get_content_length(self):
        try:
            return os.path.getsize(f"{self.asset.get('originalPath')}")
        except FileNotFoundError:
            _logger.error("Check if mountPath is correct")
            return None

    def get_content_type(self):
        return self.asset.get("originalMimeType")

    def get_creation_date(self):
        return int(isoparse(self.asset.get("fileCreatedAt")).timestamp())

    def get_display_name(self):
        return self.name
    
    def get_display_info(self):
        return {
            "type": "File",
            "etag": self.get_etag(),
            "size": self.get_content_length(),
        }

    def get_etag(self):
        return (
            f"{hashlib.md5(self.path.encode()).hexdigest()}-"
            f"{util.to_str(self.get_last_modified())}-"
            f"{self.get_content_length()}"
        )
        
    def support_etag(self):
        return True

    def get_last_modified(self):
        return int(isoparse(self.asset.get("fileModifiedAt")).timestamp())
        
    def get_content(self):
        return open(f"{self.asset.get('originalPath')}", "rb")
    
    
def run_webdav_server():
    # Load all environment variables at once
    immich_url = os.getenv("IMMICH_URL")
    api_key = os.getenv("IMMICH_API_KEY")
    album_ids_env = os.getenv("ALBUM_IDS")
    album_ids = [id.strip() for id in album_ids_env.split(",") if id.strip()] if album_ids_env else []
    refresh_rate_hours = int(os.getenv("REFRESH_RATE_HOURS", 1))  # Default to 1 hour
    port = int(os.getenv("WEBDAV_PORT", 1700))  # Allow port to be set via environment variable
    excluded_file_types = [id.strip().lower() for id in os.getenv("EXCLUDED_FILE_TYPES", "").split(",") if id.strip()]
    flatten_structure = os.getenv("FLATTEN_ASSET_STRUCTURE", "false").lower() == "true"  # Load flatten structure option

    # Validate required environment variables
    if not immich_url or not api_key:
        raise ValueError("IMMICH_URL and IMMICH_API_KEY must be set.")

    provider = ImmichProvider(immich_url, api_key, album_ids, refresh_rate_hours, excluded_file_types, flatten_structure)

    config = {
        "host": "0.0.0.0",
        "port": port,
        "provider_mapping": {"/": provider},
        "simple_dc": {
            "user_mapping": {
                "*": True  # Allows anonymous access
                # You can use {"*": {"admin": {"password": "admin"}}} to add specific user credentials.
            }
        },
        'directory_browser': True,
        'verbose': 2,   # _logger level (0: None, 1: Basic, 2: Verbose)
    }

    app = WsgiDAVApp(config)
    
    server_args = {
        "bind_addr": (config["host"], port),
        "wsgi_app": app,
    }
    server = wsgi.Server(**server_args)

    try:
        _logger.info(f"Starting WebDAV server on port {port}...")
        server.start()
    except KeyboardInterrupt:
        _logger.info("Received Ctrl-C: stopping...")
    finally:
        provider.stop_refresh()
        server.stop()


if __name__ == "__main__":
    run_webdav_server()
