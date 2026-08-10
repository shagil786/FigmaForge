"""
Repository-component matcher.

Deterministically maps indexed Figma components onto the project's *existing*
component library. Matching is pure string/key logic — there is deliberately no
model-based or fuzzy matching, so every result is reproducible and explainable.

Priority (first that resolves wins):

1. **Explicit override** — a project component declares ``figma_keys`` matching
   the Figma component's key or node id.
2. **Name/alias** — the normalized Figma name equals a project component's
   normalized name or one of its aliases.

Outcomes:

- ``resolved`` — exactly one project component matched.
- ``ambiguous`` — two or more project components matched; reported explicitly,
  never guessed.
- ``missing`` — no project component matched; reported explicitly so it can be
  created deliberately rather than duplicated by accident.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .component_index import ComponentIndex, IndexedComponent
from .library_types import ProjectComponent, ProjectLibrary, normalize_name


@dataclass
class MatchResult:
    status: str  # "resolved" | "ambiguous" | "missing"
    figma_component: str  # node id
    figma_name: str
    matches: List[str]  # project component ids
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "figma_component": self.figma_component,
            "figma_name": self.figma_name,
            "status": self.status,
            "matches": list(self.matches),
            "reason": self.reason,
        }


class ComponentMatcher:
    """Map Figma components onto existing project components."""

    def __init__(self, library: ProjectLibrary):
        self._components = list(library.components)

    # ------------------------------------------------------------------ API
    def match_all(self, index: ComponentIndex) -> List[MatchResult]:
        """Match component-sets and standalone components (not set variants).

        Variants are resolved through their parent set; see
        ``index.variants_of``. This avoids matching every variant label as if it
        were a distinct component.
        """
        results: List[MatchResult] = []
        for indexed in index.all():
            if indexed.is_variant:
                continue
            results.append(self.match(indexed))
        return results

    def match(self, indexed: IndexedComponent) -> MatchResult:
        """Match a single indexed component."""
        # 1) explicit override
        explicit = self._match_explicit(indexed)
        if explicit:
            return MatchResult(
                status="resolved",
                figma_component=indexed.node_id,
                figma_name=indexed.name,
                matches=[explicit.id],
                reason="explicit figma_keys mapping",
            )

        # 2) name / alias
        candidates = self._match_by_name(indexed.name)
        if not candidates:
            return MatchResult(
                status="missing",
                figma_component=indexed.node_id,
                figma_name=indexed.name,
                matches=[],
                reason="no existing project component matches",
            )
        if len(candidates) == 1:
            return MatchResult(
                status="resolved",
                figma_component=indexed.node_id,
                figma_name=indexed.name,
                matches=[candidates[0].id],
                reason="normalized name/alias match",
            )
        return MatchResult(
            status="ambiguous",
            figma_component=indexed.node_id,
            figma_name=indexed.name,
            matches=[c.id for c in candidates],
            reason=f"{len(candidates)} project components match; refusing to guess",
        )

    # ------------------------------------------------------------ strategies
    def _match_explicit(self, indexed: IndexedComponent) -> Optional[ProjectComponent]:
        figma_keys = [k for k in (indexed.key, indexed.node_id) if k]
        for comp in self._components:
            if any(k in comp.figma_keys for k in figma_keys):
                return comp
        return None

    def _match_by_name(self, name: str) -> List[ProjectComponent]:
        normalized = normalize_name(name)
        if not normalized:
            return []
        candidates: List[ProjectComponent] = []
        for comp in self._components:
            if normalized in comp.normalized_names:
                candidates.append(comp)
        return candidates
