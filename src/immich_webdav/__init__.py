from importlib.metadata import PackageNotFoundError, version


try:
    __version__ = version("immich-webdav-wrapper")
except PackageNotFoundError:
    __version__ = "0.0.0.dev0"


__all__ = ["__version__"]
