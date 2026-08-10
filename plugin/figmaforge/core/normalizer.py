"""
Normalization module.

Converts raw Figma API dictionaries into the internal typed model from
``figma_types``. This layer is pure, deterministic, and dependency-free; it
never performs I/O, never contacts the network, and never inspects credentials.

The typed constructors (``FigmaFile.from_dict``, ``Node.from_dict``, ...) live
in ``figma_types``. This module exists to keep a single, documented entry
point for ingestion workflows and to host higher-level summary extraction
(e.g. splitting a file into pages/frames/text/assets) that is not part of the
low-level model itself.
"""

from typing import Any, Callable, Dict, List

from .figma_types import (
    FigmaFile,
    FigmaNodeResponse,
    Node,
    NODE_TYPE_COMPONENT,
    NODE_TYPE_COMPONENT_SET,
    NODE_TYPE_FRAME,
    NODE_TYPE_GROUP,
    NODE_TYPE_INSTANCE,
    NODE_TYPE_PAGE,
    NODE_TYPE_TEXT,
)

# Callable used to build a FigmaFile from a raw response. Kept as a slot so
# tests can substitute a different parser without touching the client.
FileBuilder = Callable[[str, Dict[str, Any]], FigmaFile]


class Normalizer:
    """Normalize raw Figma API responses into internal typed objects."""

    def __init__(self, file_builder: FileBuilder = FigmaFile.from_dict):
        self._file_builder = file_builder

    # -------------------------------------------------------------------- API
    def normalize_file(self, file_key: str, raw: Dict[str, Any]) -> FigmaFile:
        """Normalize a raw full-file response into a :class:`FigmaFile`."""
        if not isinstance(raw, dict):
            raise ValueError("Figma file response must be a JSON object.")
        return self._file_builder(file_key, raw)

    def normalize_nodes(self, file_key: str, raw: Dict[str, Any]) -> FigmaNodeResponse:
        """Normalize a raw ``/v1/files/{key}/nodes`` response."""
        if not isinstance(raw, dict):
            raise ValueError("Figma nodes response must be a JSON object.")
        return FigmaNodeResponse.from_dict(file_key, raw)

    # ------------------------------------------------------------ summaries
    def collect_nodes(self, node: Node) -> List[Node]:
        """Collect every node in a tree, including the root."""
        return list(node.walk())

    def pages(self, file_data: FigmaFile) -> List[Node]:
        return [n for n in self.all_nodes(file_data) if n.type == NODE_TYPE_PAGE]

    def frames(self, file_data: FigmaFile) -> List[Node]:
        return [
            n for n in self.all_nodes(file_data)
            if n.type in (NODE_TYPE_FRAME, NODE_TYPE_GROUP)
        ]

    def components(self, file_data: FigmaFile) -> List[Node]:
        return [n for n in self.all_nodes(file_data) if n.type == NODE_TYPE_COMPONENT]

    def component_sets(self, file_data: FigmaFile) -> List[Node]:
        return [n for n in self.all_nodes(file_data) if n.type == NODE_TYPE_COMPONENT_SET]

    def instances(self, file_data: FigmaFile) -> List[Node]:
        return [n for n in self.all_nodes(file_data) if n.type == NODE_TYPE_INSTANCE]

    def text_nodes(self, file_data: FigmaFile) -> List[Node]:
        return [n for n in self.all_nodes(file_data) if n.type == NODE_TYPE_TEXT]

    def all_nodes(self, file_data: FigmaFile) -> List[Node]:
        if file_data.document is None:
            return []
        return self.collect_nodes(file_data.document)

    def summary(self, file_data: FigmaFile) -> Dict[str, Any]:
        """Return a concise, typed-aware summary of a normalized file."""
        return {
            "file_key": file_data.file_key,
            "name": file_data.name,
            "pages": len(self.pages(file_data)),
            "frames": len(self.frames(file_data)),
            "components": len(self.components(file_data)),
            "component_sets": len(self.component_sets(file_data)),
            "instances": len(self.instances(file_data)),
            "text_nodes": len(self.text_nodes(file_data)),
            "styles": len(file_data.styles),
            "variables": len(file_data.variables),
        }
