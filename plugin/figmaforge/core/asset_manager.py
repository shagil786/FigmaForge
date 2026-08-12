"""
Asset Manager (Part 7).

Handles ingestion, validation, and content-addressed storage of assets.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .asset_types import AssetManifest, AssetMetadata

class AssetManager:
    """Stores assets by SHA256 content hash."""

    def __init__(self, storage_dir: Path):
        self.storage_dir = storage_dir
        self.manifest_path = storage_dir / "manifest.json"
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.manifest = self._load_manifest()

    def _load_manifest(self) -> AssetManifest:
        if self.manifest_path.exists():
            with open(self.manifest_path, "r") as f:
                data = json.load(f)
                return AssetManifest(assets={
                    h: AssetMetadata(**m) for h, m in data["assets"].items()
                })
        return AssetManifest()

    def _save_manifest(self) -> None:
        with open(self.manifest_path, "w") as f:
            json.dump(self.manifest.to_dict(), f, indent=2)

    def ingest(self, raw_data: bytes, original_url: str, kind: str, extension: str) -> str:
        """Hash, store, and add to manifest."""
        content_hash = hashlib.sha256(raw_data).hexdigest()

        # Validate SVG
        if kind == "svg":
            self._validate_svg(raw_data)

        # Store in content-addressed structure (2-level prefixing)
        dest = self.storage_dir / content_hash[:2] / content_hash
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw_data)

        # Manifest
        self.manifest.assets[content_hash] = AssetMetadata(
            original_url=original_url,
            content_hash=content_hash,
            kind=kind,
            extension=extension,
        )
        self._save_manifest()
        return content_hash

    def _validate_svg(self, data: bytes) -> None:
        """Basic SVG security: detect embedded scripts."""
        text = data.decode("utf-8", errors="ignore").lower()
        if "<script" in text or "javascript:" in text:
            raise ValueError("Unsafe SVG content detected")
