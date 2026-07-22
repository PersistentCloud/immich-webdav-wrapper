# Immich WebDAV Wrapper

A read-only WebDAV server for browsing photos and videos stored in [Immich](https://github.com/immich-app/immich).

The application exposes Immich albums as WebDAV directories and periodically refreshes its catalog so changes in Immich become available without restarting the server.

## Features

- Exposes Immich albums through WebDAV.
- Supports selected albums or all albums accessible to the API key.
- Supports Immich v3 album asset retrieval with pagination.
- Periodically refreshes albums and assets in the background.
- Retains the last successful catalog if a refresh fails.
- Optionally separates assets into `images` and `videos` directories.
- Optionally exposes all assets directly inside each album.
- Supports excluding selected file extensions.
- Handles duplicate and WebDAV-unsafe filenames.
- Provides file size, MIME type, timestamps, and ETag metadata.
- Streams original media files from the mounted Immich library.
- Runs locally or as a Docker container.

## Important storage requirement

The application reads media directly from the asset's `originalPath` returned by Immich.

The Immich media library must therefore be available to the WebDAV process at the exact same filesystem path.

For example, if Immich returns:

```text
/usr/src/app/upload/library/admin/2025/photo.jpg
```

the WebDAV container must also be able to read that path:

```yaml
volumes:
  - /path/to/your/immich/library:/usr/src/app/upload:ro
```

Mounting the library under a different container path will cause file reads and metadata lookups to fail.

## Requirements

- Python 3.11 or newer
- An accessible Immich instance
- An Immich API key with permission to read albums and assets
- Read access to the Immich media library when using direct filesystem streaming

Python dependencies are declared in `pyproject.toml`. A separate `requirements.txt` file is not required.

## Installation

Clone the repository:

```bash
git clone https://github.com/your-user/immich-webdav-wrapper.git
cd immich-webdav-wrapper
```

Create a virtual environment:

```bash
python -m venv .venv
```

Activate it on Linux or macOS:

```bash
source .venv/bin/activate
```

Activate it on Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Install the application:

```bash
python -m pip install .
```

For development:

```bash
python -m pip install -e ".[dev]"
```

## Configuration

Configuration is read from environment variables. A local `.env` file is also supported.

Copy the example configuration:

```bash
cp .env.example .env
```

Example `.env` file:

```dotenv
IMMICH_URL=https://your-immich-instance.com
IMMICH_API_KEY=your_api_key_here

ALBUM_IDS=
REFRESH_RATE_HOURS=1
EXCLUDED_FILE_TYPES=
FLATTEN_ASSET_STRUCTURE=false

WEBDAV_HOST=0.0.0.0
WEBDAV_PORT=1700

IMMICH_REQUEST_TIMEOUT_SECONDS=60
IMMICH_REQUEST_MAX_RETRIES=3
LOG_LEVEL=INFO
```

### Environment variables

| Variable                         | Required | Default   | Description                                                                |
|----------------------------------|---------:|-----------|----------------------------------------------------------------------------|
| `IMMICH_URL`                     |      Yes | —         | Base URL of the Immich server.                                             |
| `IMMICH_API_KEY`                 |      Yes | —         | API key used to access Immich.                                             |
| `ALBUM_IDS`                      |       No | Empty     | Comma-separated album IDs. When empty, all accessible albums are loaded.   |
| `REFRESH_RATE_HOURS`             |       No | `1`       | Number of hours between catalog refreshes.                                 |
| `EXCLUDED_FILE_TYPES`            |       No | Empty     | Comma-separated file extensions to exclude, without leading dots.          |
| `FLATTEN_ASSET_STRUCTURE`        |       No | `false`   | Places assets directly inside each album instead of grouping them by type. |
| `WEBDAV_HOST`                    |       No | `0.0.0.0` | Address on which the WebDAV server listens.                                |
| `WEBDAV_PORT`                    |       No | `1700`    | Port on which the WebDAV server listens.                                   |
| `IMMICH_REQUEST_TIMEOUT_SECONDS` |       No | `60`      | HTTP read timeout for Immich API requests.                                 |
| `IMMICH_REQUEST_MAX_RETRIES`     |       No | `3`       | Maximum number of attempts for retryable Immich API requests.              |
| `LOG_LEVEL`                      |       No | `INFO`    | Application log level, such as `DEBUG`, `INFO`, or `WARNING`.              |

### Selecting albums

To expose specific albums:

```dotenv
ALBUM_IDS=album-id-1,album-id-2
```

To expose every album accessible to the API key:

```dotenv
ALBUM_IDS=
```

When all albums are enabled, the application requests the album list again during each refresh. Newly created albums can therefore appear without restarting the server.

### Excluding file types

Extensions are case-insensitive and should not include a leading dot:

```dotenv
EXCLUDED_FILE_TYPES=png,gif,mov
```

### Directory structure

With:

```dotenv
FLATTEN_ASSET_STRUCTURE=false
```

albums are exposed as:

```text
/
└── Album Name/
    ├── images/
    │   ├── photo.jpg
    │   └── scan.png
    └── videos/
        └── recording.mp4
```

With:

```dotenv
FLATTEN_ASSET_STRUCTURE=true
```

albums are exposed as:

```text
/
└── Album Name/
    ├── photo.jpg
    ├── scan.png
    └── recording.mp4
```

## Running the server

After installation, run:

```bash
immich-webdav
```

Alternatively:

```bash
python -m immich_webdav
```

The server is available by default at:

```text
http://localhost:1700/
```

## Docker

### Build the image

Using the build-only Compose file:

```bash
docker compose -f docker-compose.build.yaml build
```

The resulting local image is:

```text
persistentcloud/immich-webdav-wrapper:local
```

The provided build script can build, tag, and push versioned images:

```bash
./build.sh
```

To use another registry:

```bash
./build.sh ghcr.io
```

### Runtime Compose example

```yaml
services:
  immich-webdav-wrapper:
    image: persistentcloud/immich-webdav-wrapper:latest
    container_name: immich_webdav_wrapper

    environment:
      IMMICH_URL: http://immich_server:2283
      IMMICH_API_KEY: ${IMMICH_API_KEY}
      ALBUM_IDS: ${ALBUM_IDS:-}
      REFRESH_RATE_HOURS: ${REFRESH_RATE_HOURS:-1}
      EXCLUDED_FILE_TYPES: ${EXCLUDED_FILE_TYPES:-}
      FLATTEN_ASSET_STRUCTURE: ${FLATTEN_ASSET_STRUCTURE:-false}
      WEBDAV_PORT: 1700

    ports:
      - "1700:1700"

    volumes:
      - /path/to/your/immich/library:/usr/src/app/upload:ro

    networks:
      - immich

    restart: unless-stopped

networks:
  immich:
    external: true
    name: immich_default
```

Start the container:

```bash
docker compose up -d
```

Follow the logs:

```bash
docker compose logs -f immich-webdav-wrapper
```

## Connecting a WebDAV client

Use the configured host and port:

```text
http://server-address:1700/
```

The server is read-only. Uploading, deleting, renaming, or moving assets through WebDAV is not supported.

## Project structure

```text
src/
└── immich_webdav/
    ├── __init__.py
    ├── __main__.py
    ├── config.py
    ├── immich_client.py
    ├── server.py
    └── webdav/
        ├── __init__.py
        ├── provider.py
        └── resources.py
```

### `config.py`

Reads and validates environment configuration.

### `immich_client.py`

Handles communication with Immich, including:

- Album retrieval
- Paginated asset search
- Request retries
- Timeout handling
- HTTP error reporting
- Immich correlation IDs
- Response validation

### `webdav/provider.py`

Builds and periodically refreshes the in-memory WebDAV catalog.

A catalog is published only after a complete refresh succeeds. If a scheduled refresh fails, the previous successful catalog remains available.

### `webdav/resources.py`

Contains the WebDAV resource classes:

- `RootCollection`
- `ImmichAlbumCollection`
- `ImmichAssetCollection`
- `ImmichAsset`

### `server.py`

Creates the Immich client, WebDAV provider, WsgiDAV application, and Cheroot server.

### `__main__.py`

Provides the `immich-webdav` command-line entry point.

## Development

Install development dependencies:

```bash
python -m pip install -e ".[dev]"
```

Run tests:

```bash
pytest
```

Run Ruff:

```bash
ruff check .
```

Run mypy:

```bash
mypy src
```

Compile-check the source tree:

```bash
python -m compileall src
```

## Logging and troubleshooting

### Albums appear but contain no files

Verify that:

- The API key can read and search assets.
- The selected albums contain assets.
- The Immich URL is reachable from the application.
- The application logs do not contain API validation or authorization errors.

### Files are listed but cannot be opened

The most likely cause is an incorrect filesystem mount.

Inspect the `originalPath` returned by Immich and ensure the same path exists inside the WebDAV container.

### New albums do not appear

When `ALBUM_IDS` is empty, new albums appear after the next scheduled refresh.

When `ALBUM_IDS` contains explicit IDs, only those albums are exposed.

### Duplicate filenames

When two assets have the same filename, the application adds a stable suffix based on the asset ID:

```text
photo.jpg
photo.jpg [asset-id]
```

### Refresh failures

Retryable network failures and server errors are retried automatically.

If a background refresh ultimately fails, the error is logged and the previous catalog remains active.

### Container exits during startup

The initial catalog load runs synchronously. Invalid credentials, an unreachable Immich server, or an incompatible API response can therefore stop the application during startup instead of exposing an empty WebDAV share.

Inspect the container logs:

```bash
docker logs immich_webdav_wrapper
```

## Security

The WebDAV service should be treated as access to the underlying media library.

- Mount the media library as read-only.
- Do not expose the WebDAV port directly to the public internet.
- Place the service behind authentication or a trusted reverse proxy when remote access is required.
- Keep the Immich API key secret.
- Do not commit `.env` files to version control.

## Stopping the server

For a local process, press `Ctrl+C`.

For Docker Compose:

```bash
docker compose down
```

The server stops the background refresh thread and closes the Immich HTTP client during shutdown.

## License

This project is licensed under the MIT License. See [LICENSE](./LICENSE) for details.
