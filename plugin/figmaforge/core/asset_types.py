"""
Asset Pipeline Types (Part 7).

Defines the structure for validated, content-addressed assets.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Any


@dataclass
class AssetManifest:
    """Tracks ingested assets by content hash."""

    # Store: hash -> metadata
    assets: Dict[str, AssetMetadata] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "assets": {h: a.to_dict() for h, a in self.assets.items()}
        }


@dataclass
class AssetMetadata:
    """Provenance and type info for an asset."""

    original_url: str
    content_hash: str
    kind: str  # image, svg, font
    extension: str
    license: Optional[str] = None
    source: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "original_url": self.original_url,
            "content_hash": self.content_hash,
            "kind": self.kind,
            "extension": self.extension,
            "license": self.license,
            "source": self.source,
        }
