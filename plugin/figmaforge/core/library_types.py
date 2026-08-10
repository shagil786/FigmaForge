"""
Project library: the repository's *existing* components and design tokens.

The resolution layer maps Figma components and variables onto this library and
always **prefers** these existing definitions over creating new ones — a Figma
component is never duplicated when a matching project component already exists.

The library is loaded from a deterministic JSON manifest under
``plugin/figmaforge/library/`` (``components.json`` + ``tokens.json``). This is
the project's source of truth for "what already exists" — pure data, no
inference, no agent frameworks.

Design goals, consistent with FigmaForge conventions:

- Standard library only.
- Loaders are defensive (bad entries degrade to explicit errors, never silent
  drops), matching ``figma_types`` conventions.
- ``normalize_name`` provides the deterministic key used by every resolver so
  matching is exact and reproducible.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .figma_errors import FigmaResponseError

DEFAULT_LIBRARY_DIR = Path(__file__).parent.parent / "library"

# Words removed during component-name normalization (deterministic, documented).
# "set" is intentionally NOT a filler so component-sets ("Button Set") normalize
# distinctly from their base component ("Button").
FILLER_WORDS = frozenset({
    "component", "default", "the", "and", "a", "an", "of",
})

# Any run of non-alphanumeric characters acts as a single separator, so
# ``"icon-slot"`` and ``"Icon Slot"`` normalize identically.
_SEPARATOR_RE = re.compile(r"[^a-z0-9]+")


def normalize_name(name: str) -> str:
    """Deterministic normalization used for matching.

    Lowercases, treats every run of non-alphanumeric characters as a single
    separator, collapses whitespace, and removes filler words. Examples:
    ``"Button Set"`` -> ``"button set"``, ``"icon-slot"`` / ``"Icon Slot"`` ->
    ``"icon slot"``.
    """
    if not name:
        return ""
    words = _SEPARATOR_RE.sub(" ", name.lower()).split()
    cleaned = [w for w in words if w and w not in FILLER_WORDS]
    return " ".join(cleaned)


def slugify(name: str) -> str:
    """Deterministic kebab-case slug used for semantic token keys.

    Unlike ``normalize_name``, filler words are kept. Examples:
    ``"Space / 4"`` -> ``"space-4"``, ``"Color / Primary"`` -> ``"color-primary"``.
    """
    if not name:
        return ""
    return _SEPARATOR_RE.sub("-", name.lower()).strip("-")


@dataclass
class ProjectComponent:
    """An existing component in the repository's library."""

    id: str
    name: str
    kind: str = "component"  # "component" | "component_set"
    aliases: List[str] = field(default_factory=list)
    figma_keys: List[str] = field(default_factory=list)  # explicit overrides
    source: Optional[str] = None
    props: List[str] = field(default_factory=list)

    @property
    def normalized_names(self) -> List[str]:
        """All deterministic match keys: normalized name + normalized aliases."""
        names = [normalize_name(self.name)]
        names.extend(normalize_name(a) for a in self.aliases)
        return [n for n in names if n]

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProjectComponent":
        if not isinstance(data, dict):
            raise FigmaResponseError("Project component must be a JSON object.")
        comp_id = str(data.get("id", "") or "")
        if not comp_id:
            raise FigmaResponseError("Project component requires an 'id'.")
        return cls(
            id=comp_id,
            name=str(data.get("name", comp_id) or comp_id),
            kind=str(data.get("kind", "component") or "component"),
            aliases=[str(a) for a in (data.get("aliases", []) or []) if isinstance(a, str)],
            figma_keys=[str(k) for k in (data.get("figma_keys", []) or []) if isinstance(k, str)],
            source=data.get("source"),
            props=[str(p) for p in (data.get("props", []) or []) if isinstance(p, str)],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "aliases": list(self.aliases),
            "figma_keys": list(self.figma_keys),
            "source": self.source,
            "props": list(self.props),
        }


@dataclass
class ProjectToken:
    """An existing design token in the repository's library."""

    name: str
    type: str  # color | typography | spacing | radius | shadow | opacity | breakpoint
    value: Any = None
    source: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProjectToken":
        if not isinstance(data, dict):
            raise FigmaResponseError("Project token must be a JSON object.")
        name = str(data.get("name", "") or "")
        if not name:
            raise FigmaResponseError("Project token requires a 'name'.")
        return cls(
            name=name,
            type=str(data.get("type", "") or ""),
            value=data.get("value"),
            source=data.get("source"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "type": self.type, "value": self.value, "source": self.source}


@dataclass
class ProjectLibrary:
    """The repository's existing components and tokens."""

    components: List[ProjectComponent] = field(default_factory=list)
    tokens: List[ProjectToken] = field(default_factory=list)

    def component_by_id(self, comp_id: str) -> Optional[ProjectComponent]:
        return next((c for c in self.components if c.id == comp_id), None)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProjectLibrary":
        if not isinstance(data, dict):
            raise FigmaResponseError("Project library must be a JSON object.")
        return cls(
            components=[
                ProjectComponent.from_dict(c)
                for c in (data.get("components", []) or [])
                if isinstance(c, dict)
            ],
            tokens=[
                ProjectToken.from_dict(t)
                for t in (data.get("tokens", []) or [])
                if isinstance(t, dict)
            ],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "components": [c.to_dict() for c in self.components],
            "tokens": [t.to_dict() for t in self.tokens],
        }


class LibraryLoader:
    """Load the project library manifest from disk."""

    def __init__(self, library_dir: Optional[Path] = None):
        self.library_dir = Path(library_dir or DEFAULT_LIBRARY_DIR)

    def load(self) -> ProjectLibrary:
        components = self._load_components()
        tokens = self._load_tokens()
        return ProjectLibrary(components=components, tokens=tokens)

    def load_default(self) -> ProjectLibrary:
        """Alias matching the ir_fixtures convention: default library."""
        return self.load()

    def _load_components(self) -> List[ProjectComponent]:
        data = self._read_json("components")
        return [ProjectComponent.from_dict(c) for c in (data.get("components", []) or []) if isinstance(c, dict)]

    def _load_tokens(self) -> List[ProjectToken]:
        data = self._read_json("tokens")
        return [ProjectToken.from_dict(t) for t in (data.get("tokens", []) or []) if isinstance(t, dict)]

    def _read_json(self, name: str) -> Dict[str, Any]:
        path = self.library_dir / f"{name}.json"
        if not path.exists():
            raise FigmaResponseError(f"Library manifest not found: {name!r} ({path})")
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except json.JSONDecodeError as exc:
            raise FigmaResponseError(f"Library manifest {name!r} is not valid JSON: {exc}")
        if not isinstance(data, dict):
            raise FigmaResponseError(f"Library manifest {name!r} must be a JSON object.")
        return data
