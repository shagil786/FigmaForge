"""
Resolution orchestrator: components + variants + instances + tokens.

``Resolver.resolve()`` runs the Part-4 pipeline over a Design IR and produces a
single :class:`ResolutionReport` that is JSON-serializable and schema-validated:

- component index (ComponentIndex) + variant extraction (VariantResolver)
- repository-component matching (ComponentMatcher) with resolved/ambiguous/missing
- instance-to-component resolution
- semantic token resolution (TokenResolver) with token references
- a clear account of unresolved mappings and unsupported token types

No code generation happens here — the report is the input a future generator
would consume.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .component_index import ComponentIndex
from .ir_types import IRDocument, IRNode, KIND_INSTANCE
from .library_types import ProjectLibrary, LibraryLoader
from .matcher import ComponentMatcher, MatchResult
from .token_resolver import TokenResolution, TokenResolver
from .variant_resolver import VariantResolver

DEFAULT_SCHEMA_PATH = Path(__file__).parent.parent / "schemas" / "resolution-report.schema.json"


@dataclass
class ResolutionReport:
    """The full result of resolving a Design IR against the project library."""

    schema_version: int = 1
    file_key: str = ""
    resolved: List[MatchResult] = field(default_factory=list)
    ambiguous: List[MatchResult] = field(default_factory=list)
    missing: List[MatchResult] = field(default_factory=list)
    instances: List[Dict[str, Any]] = field(default_factory=list)
    variants: List[Dict[str, Any]] = field(default_factory=list)
    tokens: Optional[TokenResolution] = None

    @property
    def counts(self) -> Dict[str, int]:
        return {
            "resolved": len(self.resolved),
            "ambiguous": len(self.ambiguous),
            "missing": len(self.missing),
            "instances_resolved": sum(1 for i in self.instances if i.get("status") == "resolved"),
            "instances_missing": sum(1 for i in self.instances if i.get("status") == "missing"),
            "semantic_tokens": len(self.tokens.semantic) if self.tokens else 0,
            "token_refs_resolved": sum(1 for r in (self.tokens.node_refs if self.tokens else []) if r.get("resolved")),
            "token_refs_unresolved": sum(1 for r in (self.tokens.node_refs if self.tokens else []) if not r.get("resolved")),
            "unsupported_tokens": len(self.tokens.unsupported) if self.tokens else 0,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "file_key": self.file_key,
            "counts": self.counts,
            "resolved": [r.to_dict() for r in self.resolved],
            "ambiguous": [r.to_dict() for r in self.ambiguous],
            "missing": [r.to_dict() for r in self.missing],
            "instances": list(self.instances),
            "variants": list(self.variants),
            "tokens": self.tokens.to_dict() if self.tokens else None,
        }


def report_to_json(report: ResolutionReport, indent: int = 2) -> str:
    """Serialize a resolution report deterministically (snapshot-stable)."""
    return json.dumps(report.to_dict(), indent=indent, sort_keys=True)


class Resolver:
    """Run component/variant/instance/token resolution over an IR document."""

    def __init__(self, document: IRDocument, library: Optional[ProjectLibrary] = None):
        self.document = document
        self.library = library if library is not None else LibraryLoader().load()

    # ------------------------------------------------------------------ API
    def resolve(self) -> ResolutionReport:
        index = ComponentIndex(self.document)

        # --- components + variants + instances
        matcher = ComponentMatcher(self.library)
        matches = matcher.match_all(index)
        report = ResolutionReport(
            file_key=self.document.file_key,
            resolved=[m for m in matches if m.status == "resolved"],
            ambiguous=[m for m in matches if m.status == "ambiguous"],
            missing=[m for m in matches if m.status == "missing"],
            instances=self._resolve_instances(index),
            variants=self._collect_variants(index),
        )

        # --- tokens
        token_resolution = TokenResolver(
            self.document,
            library_tokens=self.library.tokens,
        ).resolve()
        report.tokens = token_resolution
        return report

    # ------------------------------------------------------------- helpers
    def _resolve_instances(self, index: ComponentIndex) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for node in self.document.all_nodes():
            if node.kind != KIND_INSTANCE:
                continue
            resolved = index.resolve_instance(node)
            entry: Dict[str, Any] = {
                "node_id": node.id,
                "name": node.name,
                "component_id": node.instance.component_id if node.instance else None,
                "variant_properties": VariantResolver.instance_properties(node),
            }
            if resolved is not None:
                entry.update({
                    "status": "resolved",
                    # File-level components may have no node id; fall back to key.
                    "resolved_to": resolved.node_id or resolved.key,
                    "resolved_name": resolved.name,
                    "resolved_kind": resolved.kind,
                    "is_variant_of": resolved.variant_of,
                })
            else:
                entry.update({
                    "status": "missing",
                    "reason": "component referenced by this instance is not in the document",
                })
            out.append(entry)
        return out

    def _collect_variants(self, index: ComponentIndex) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for comp in index.component_sets():
            set_node = self._node_by_id(comp.node_id)
            variants = VariantResolver.variants(set_node) if set_node is not None else []
            out.append({
                "set_node_id": comp.node_id,
                "set_name": comp.name,
                "set_key": comp.key,
                "variant_count": len(variants),
                "default_variant": next((v.node_id for v in variants if v.default), None),
                "variants": [v.to_dict() for v in variants],
            })
        return out

    def _node_by_id(self, node_id: str) -> Optional[IRNode]:
        for node in self.document.all_nodes():
            if node.id == node_id:
                return node
        return None
