#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

LOCAL_REPOSITORY="persistentcloud/immich-webdav-wrapper"
LOCAL_IMAGE="${LOCAL_REPOSITORY}:local"

REGISTRY="${1:-}"

if [[ -n "${REGISTRY}" ]]; then
    REGISTRY="${REGISTRY%/}/"
fi

TARGET_REPOSITORY="${REGISTRY}${LOCAL_REPOSITORY}"

if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
else
    echo "Error: Python 3.11 or newer is required to read pyproject.toml."
    exit 1
fi

if ! "${PYTHON_BIN}" -c "import tomllib" >/dev/null 2>&1; then
    echo "Error: ${PYTHON_BIN} does not provide tomllib. Python 3.11+ is required."
    exit 1
fi

VERSION="$(
    "${PYTHON_BIN}" - <<'PY'
from pathlib import Path
import tomllib

with Path("pyproject.toml").open("rb") as pyproject_file:
    pyproject = tomllib.load(pyproject_file)

print(pyproject["project"]["version"])
PY
)"

if [[ -z "${VERSION}" ]]; then
    echo "Error: no project version found in pyproject.toml."
    exit 1
fi

# Docker tags allow letters, digits, underscores, periods and hyphens.
if [[ ! "${VERSION}" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ ]]; then
    echo "Error: '${VERSION}' is not a valid Docker tag."
    exit 1
fi

echo "Registry: ${REGISTRY:-Docker Hub}"
echo "Repository: ${TARGET_REPOSITORY}"
echo "Version: ${VERSION}"

echo "Building ${LOCAL_IMAGE}..."
docker compose \
    --file docker-compose.build.yaml \
    build \
    --pull \
    immich-webdav-wrapper

# Verify that Compose produced the expected image.
docker image inspect "${LOCAL_IMAGE}" >/dev/null

echo "Creating release tags..."
docker image tag "${LOCAL_IMAGE}" "${TARGET_REPOSITORY}:${VERSION}"
docker image tag "${LOCAL_IMAGE}" "${TARGET_REPOSITORY}:latest"

echo "Pushing ${TARGET_REPOSITORY}:${VERSION}..."
docker push "${TARGET_REPOSITORY}:${VERSION}"

echo "Pushing ${TARGET_REPOSITORY}:latest..."
docker push "${TARGET_REPOSITORY}:latest"

echo "Published:"
echo "  ${TARGET_REPOSITORY}:${VERSION}"
echo "  ${TARGET_REPOSITORY}:latest"