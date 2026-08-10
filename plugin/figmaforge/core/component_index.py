"""
Component index and instance resolution.

Builds an index of every COMPONENT and COMPONENT_SET in a normalized Design IR
so that:

- components are addressable by node id and by file-level key,
- instances resolve to the component they instantiate,
- component-set children are tracked as variants of their parent set.

Everything here is pure, deterministic, and operates only on the IR produced
by Part 3 (no network, no guessing).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .ir_types import (
    IRComponent,
    IRDocument,
    IRLink,
    IRNode,
    IRSource,
    KIND_COMPONENT,
    KIND_COMPONENT_SET,
)


@dataclass
class IndexedComponent:
    """A component or component-set as indexed from the IR."""

    node_id: str
    name: str
    kind: str  # "component" | "component_set"
    key: Optional[str] = None  # file-level component key
    description: str = ""
    source: Optional[IRSource] = None
    documentation_links: List[IRLink] = field(default_factory=list)
    variant_of: Optional[str] = None  # parent component-set node id (if variant)
    default: bool = False  # is the default variant of its set

    @property
    def is_component_set(self) -> bool:
        return self.kind == KIND_COMPONENT_SET

    @property
    def is_variant(self) -> bool:
        return self.variant_of is not None


class ComponentIndex:
    """Index of all components / component-sets in a document."""

    def __init__(self, document: IRDocument):
        self._components: Dict[str, IndexedComponent] = {}  # by node id
        self._by_file_key: Dict[str, IndexedComponent] = {}  # by component key
        self._variants: Dict[str, List[IndexedComponent]] = {}  # set node id -> variants
        self._build(document)

    # ------------------------------------------------------------------ API
    def all(self) -> List[IndexedComponent]:
        return list(self._components.values())

    def components(self) -> List[IndexedComponent]:
        return [c for c in self.all() if c.kind == KIND_COMPONENT]

    def component_sets(self) -> List[IndexedComponent]:
        return [c for c in self.all() if c.kind == KIND_COMPONENT_SET]

    def get_by_node_id(self, node_id: str) -> Optional[IndexedComponent]:
        return self._components.get(node_id)

    def get_by_key(self, key: str) -> Optional[IndexedComponent]:
        return self._by_file_key.get(key)

    def variants_of(self, set_node_id: str) -> List[IndexedComponent]:
        return list(self._variants.get(set_node_id, []))

    def resolve_instance(self, instance: IRNode) -> Optional[IndexedComponent]:
        """Resolve an INSTANCE node to the component it instantiates.

        Resolution order (deterministic, first match wins):
        1. ``componentId`` as a node id
        2. ``componentId`` as a file-level component key
        3. ``mainComponent.id`` as a node id / key
        Returns ``None`` when the instance references something not in the
        document (reported as an unresolved mapping, never guessed).
        """
        candidates: List[Optional[str]] = []
        inst = instance.instance
        if inst is not None:
            candidates.extend([inst.component_id, inst.main_component_id, inst.component_key])
        for cand in candidates:
            if not cand:
                continue
            hit = self._components.get(cand) or self._by_file_key.get(cand)
            if hit is not None:
                return hit
        return None

    # ------------------------------------------------------------- building
    def _build(self, document: IRDocument) -> None:
        # 1) file-level component maps (authoritative for keys + descriptions)
        for key, comp in document.components.items():
            self._index_file_level(key, comp)
        for key, comp in document.component_sets.items():
            self._index_file_level(key, comp)

        # 2) node-level components (authoritative for node ids + source paths)
        for node in document.all_nodes():
            if node.kind in (KIND_COMPONENT, KIND_COMPONENT_SET):
                self._index_node(node)

        # 3) track set membership (variants) + default variant
        for node in document.all_nodes():
            if node.kind != KIND_COMPONENT_SET:
                continue
            default_id = node.raw.get("defaultVariant") if isinstance(node.raw, dict) else None
            for child in node.children:
                entry = self._components.get(child.id)
                if entry is None:
                    continue
                entry.variant_of = node.id
                entry.default = bool(default_id and child.id == default_id)
                self._variants.setdefault(node.id, []).append(entry)

    def _index_file_level(self, key: str, comp: IRComponent) -> None:
        entry = IndexedComponent(
            node_id=comp.node_id or "",
            name=comp.name,
            kind=comp.kind,
            key=key,
            description=comp.description,
            documentation_links=list(comp.documentation_links),
        )
        if entry.node_id:
            self._components[entry.node_id] = entry
        self._by_file_key[key] = entry

    def _index_node(self, node: IRNode) -> None:
        comp = node.component
        key = comp.key if comp else None
        existing = self._components.get(node.id) or (self._by_file_key.get(key) if key else None)
        if existing is not None:
            # enrich file-level entry with node source metadata
            existing.source = node.source
            return
        entry = IndexedComponent(
            node_id=node.id,
            name=node.name,
            kind=node.kind,
            key=key,
            description=comp.description if comp else "",
            source=node.source,
        )
        self._components[node.id] = entry
        if key:
            self._by_file_key[key] = entry
