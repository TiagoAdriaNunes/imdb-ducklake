"""Source archive acquisition and manifest management."""

from imdb_ducklake.acquisition.downloader import Downloader
from imdb_ducklake.acquisition.manifest import Manifest, ManifestEntry

__all__ = ["Downloader", "Manifest", "ManifestEntry"]
