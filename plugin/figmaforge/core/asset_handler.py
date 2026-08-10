"""
Asset-reference handler for Figma API responses.

Figma API returns references (URLs) rather than image bytes. This handler maps
those URLs to local storage references and manages the mapping.

It does NOT download images (that is an I/O operation outside this layer). It
only manages the *mapping* and *validation* of those asset references.
"""

import logging
from dataclasses import dataclass
from typing import Dict, Optional


logger = logging.getLogger("figmaforge.asset_handler")


@dataclass
class AssetMetadata:
    """Metadata about a Figma asset reference."""

    url: str
    downloaded: bool = False
    local_path: Optional[str] = None
    checksum: Optional[str] = None


class AssetHandler:
    """Manages maps of Figma image/URL references."""

    def __init__(self):
        self._assets: Dict[str, AssetMetadata] = {}

    def register(self, node_id: str, url: str) -> str:
        """Register a new asset URL."""
        if node_id not in self._assets:
            self._assets[node_id] = AssetMetadata(url=url)
        return node_id

    def get_url(self, node_id: str) -> Optional[str]:
        """Get the URL for an asset."""
        asset = self._assets.get(node_id)
        return asset.url if asset else None

    def mark_downloaded(self, node_id: str, local_path: str, checksum: str) -> None:
        """Mark an asset as downloaded."""
        if node_id in self._assets:
            self._assets[node_id].downloaded = True
            self._assets[node_id].local_path = local_path
            self._assets[node_id].checksum = checksum
        else:
            logger.warning(f"Attempted to mark unknown asset as downloaded: {node_id}")

    def list_pending(self) -> Dict[str, str]:
        """List all URLs not yet downloaded."""
        return {
            nid: a.url for nid, a in self._assets.items() if not a.downloaded
        }
