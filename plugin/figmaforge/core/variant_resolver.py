"""
Variant and variant-property extraction.

Figma encodes variants in two places:

- INSTANCE nodes carry ``componentProperties`` (e.g. ``{"Size": "Large"}``) and
  ``componentPropertyDefinitions`` in their raw payload — this is the
  authoritative source for "which variant is this instance?".
- COMPONENT_SET children (COMPONENT nodes) carry their variant combination in
  their name. We parse ``Prop=Value`` segments when present and fall back to a
  single ``variant`` label otherwise. The set's ``defaultVariant`` id marks the
  default variant.

All extraction is deterministic and never guesses: unparseable names surface as
a ``variant`` label rather than being dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .ir_types import IRNode, KIND_COMPONENT


@dataclass
class Variant:
    """A single variant of a component-set."""

    node_id: str
    name: str  # original Figma name, e.g. "Primary / Large"
    properties: Dict[str, str]  # parsed variant properties
    default: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "name": self.name,
            "properties": dict(self.properties),
            "default": self.default,
        }


class VariantResolver:
    """Extract variant properties from instances and component-sets."""

    @staticmethod
    def instance_properties(instance: IRNode) -> Dict[str, str]:
        """Return the ``componentProperties`` of an INSTANCE node (if any)."""
        if instance.kind != "instance":
            return {}
        raw = instance.raw if isinstance(instance.raw, dict) else {}
        props = raw.get("componentProperties")
        if not isinstance(props, dict):
            return {}
        return {str(k): str(v) for k, v in props.items() if v is not None}

    @staticmethod
    def instance_property_definitions(instance: IRNode) -> Dict[str, Any]:
        """Return the ``componentPropertyDefinitions`` of an INSTANCE (if any)."""
        raw = instance.raw if isinstance(instance.raw, dict) else {}
        defs = raw.get("componentPropertyDefinitions")
        return dict(defs) if isinstance(defs, dict) else {}

    @staticmethod
    def variants(component_set: IRNode) -> List[Variant]:
        """Extract variants from the children of a COMPONENT_SET node."""
        out: List[Variant] = []
        raw = component_set.raw if isinstance(component_set.raw, dict) else {}
        default_id = raw.get("defaultVariant")
        for child in component_set.children:
            if child.kind != KIND_COMPONENT:
                continue
            out.append(Variant(
                node_id=child.id,
                name=child.name,
                properties=VariantResolver._parse_variant_name(child.name),
                default=bool(default_id and child.id == default_id),
            ))
        return out

    # ------------------------------------------------------------- parsing
    @staticmethod
    def _parse_variant_name(name: str) -> Dict[str, str]:
        """Parse ``"Prop=Value, Prop2=Value2"`` segments out of a variant name.

        Falls back to a single ``variant`` label when no ``K=V`` segments are
        present, so nothing is silently dropped.
        """
        if not name:
            return {}
        properties: Dict[str, str] = {}
        for part in name.split(","):
            part = part.strip()
            if "=" in part:
                key, value = part.split("=", 1)
                properties[key.strip()] = value.strip()
        if not properties:
            properties["variant"] = name.strip()
        return properties
