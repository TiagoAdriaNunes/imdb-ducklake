"""Source archive acquisition and manifest management."""

from imdb_ducklake.acquisition.downloader import (
    Downloader,
    VerifiedArtifact,
    load_verified_artifacts,
)
from imdb_ducklake.acquisition.manifest import Manifest, ManifestEntry

__all__ = [
    "Downloader",
    "Manifest",
    "ManifestEntry",
    "VerifiedArtifact",
    "load_verified_artifacts",
]
