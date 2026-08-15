"""
Asset-reference collector (Part 17).

Gathers the image/SVG asset references an IR document carries into
deterministic, node_id-sorted :class:`AssetRef` records:

- per-node ``IRAssetRef`` (``node.asset``) with its ``url`` / ``image_ref``;
- the document-level ``assets`` map (``node_id -> url`` from the Figma
  ``/v1/images`` endpoint), which also resolves nodes that only carry an
  ``image_ref``;
- image fills (``IRFill(kind="image")``) that have no resolved URL yet — the
  pipeline ``assets`` stage fills those via ``get_images`` when a token is
  present.

Pure, deterministic, and dependency-free: no I/O, no network, no credentials.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .ir_types import IRDocument, IRNode

ASSET_KIND_IMAGE = "image"
ASSET_KIND_SVG = "svg"


@dataclass
class AssetRef:
    """One asset a design references: where it lives (url) and how it is keyed."""

    node_id: str
    url: Optional[str] = None
    image_ref: Optional[str] = None
    kind: str = ASSET_KIND_IMAGE  # "image" | "svg"


def _kind_for(image_ref: Optional[str], url: Optional[str]) -> str:
    """SVG when the reference or URL says so; images otherwise."""
    if image_ref and str(image_ref).lower().startswith("svg"):
        return ASSET_KIND_SVG
    if url and str(url).lower().endswith(".svg"):
        return ASSET_KIND_SVG
    return ASSET_KIND_IMAGE


def _collect_node_ref(node: IRNode, document_assets) -> Optional[AssetRef]:
    """Build the AssetRef for a single IR node (None when it has no asset)."""
    asset = node.asset
    image_ref = asset.image_ref if asset else None

    fills = node.style.fills if node.style else []
    image_fills = [f for f in fills if f.kind == "image"]
    if image_fills and not image_ref:
        image_ref = image_fills[0].image_ref

    url = (asset.url if asset and asset.url else None) or document_assets.get(node.id)

    if url is None and image_ref is None and asset is None and not image_fills:
        return None

    return AssetRef(
        node_id=node.id,
        url=url,
        image_ref=image_ref,
        kind=_kind_for(image_ref, url),
    )


def collect_asset_refs(document: IRDocument) -> List[AssetRef]:
    """Collect every asset reference in the document, sorted by node id.

    Node-carried references come first (walk order); document-level ``assets``
    entries whose node is absent from the tree are appended; the result is
    sorted by ``node_id`` so the manifest is deterministic.
    """
    refs: List[AssetRef] = []
    seen: set = set()
    for node in document.all_nodes():
        ref = _collect_node_ref(node, document.assets)
        if ref is not None:
            refs.append(ref)
            seen.add(node.id)
    for node_id, url in document.assets.items():
        if node_id not in seen and url:
            refs.append(AssetRef(
                node_id=node_id,
                url=url,
                kind=_kind_for(None, url),
            ))
    return sorted(refs, key=lambda r: r.node_id)
