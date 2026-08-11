"""
Layout analyzer orchestrator (Part 5).

``LayoutAnalyzer.analyze(document, library, viewport)`` runs the layout engine,
constraint model, and breakpoint model over a Design IR and produces a single
:class:`LayoutPlan` that is JSON-serializable and schema-validated.

This module owns the *cross-cutting* outputs the engine does not:

- per-node and aggregate **confidence** (evidence-based, deterministic),
- document-level **diagnostics** (including unsupported behavior such as native
  scroll, which is reported, not invented),
- **counts** across the whole tree,
- a flattened **constraint report**.

Nothing here emits code — the plan is the seam a future generator consumes.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .breakpoint_model import BreakpointModel, signature
from .ir_types import IRDocument
from .layout_engine import LayoutEngine
from .layout_types import (
    CONFIDENCE_HIGH,
    CONFIDENCE_MEDIUM,
    ConstraintReport,
    Diagnostic,
    DISPLAY_ABSOLUTE,
    DISPLAY_FLEX,
    DISPLAY_GRID,
    DISPLAY_NONE,
    LayoutPlan,
    SEVERITY_INFO,
    SIZING_FILL,
    SIZING_FIXED,
    SIZING_HUG,
    SIZING_PERCENT,
)
from .library_types import LibraryLoader

# assumption -> confidence penalty (evidence-based, documented)
_ASSUMPTION_PENALTY = {
    "text_width_heuristic": 0.30,
    "absolute_without_anchors": 0.20,
    "fill_or_percent_in_hug_container": 0.30,
    "hug_no_content": 0.30,
    "grid_hug_approximated": 0.15,
    "percent_needs_parent": 0.30,
    "fill_needs_parent": 0.30,
    "scale_anchor_approximated_as_min": 0.10,
}


def _flatten(root) -> List[Any]:
    out = []
    for node in root.walk():
        out.append(node)
    return out


class LayoutAnalyzer:
    """Analyze a Design IR's layout into a :class:`LayoutPlan`."""

    def __init__(
        self,
        engine: Optional[LayoutEngine] = None,
        breakpoint_model: Optional[BreakpointModel] = None,
    ):
        self._engine = engine or LayoutEngine()
        self._breakpoint_model = breakpoint_model

    # ------------------------------------------------------------------ API
    def analyze(
        self,
        document: IRDocument,
        library=None,
        viewport: Optional[float] = None,
        breakpoint_tokens=None,
    ) -> LayoutPlan:
        lib = library if library is not None else LibraryLoader().load()
        breakpoint_model = self._breakpoint_model or BreakpointModel(
            breakpoint_tokens if breakpoint_tokens is not None else lib.tokens)

        base_width = self._base_width(document)
        target = viewport if viewport is not None else base_width

        screens = self._engine.screens(document, viewport=target, base_width=base_width)

        # --- breakpoints: measure real engine output at every ladder width
        bp_plan = breakpoint_model.infer(
            screens,
            self._collect_signatures(document, base_width, breakpoint_model),
        )
        width_to_alias = {int(w["width"]): w["breakpoint"] for w in bp_plan.breakpoints}
        for change in bp_plan.changes:
            change.breakpoint = width_to_alias.get(int(change.width), "")
        self._attach_breakpoints(screens, bp_plan.changes)

        # --- counts / confidence / diagnostics / constraints
        counts = self._counts(screens)
        confidence = self._confidence(screens)
        diagnostics = self._document_diagnostics(screens)
        constraint_report = self._flatten_constraints(screens)

        return LayoutPlan(
            schema_version=1,
            file_key=document.file_key,
            viewport=float(target),
            base_width=float(base_width),
            source=(document.source.to_dict() if document.source else None),
            screens=screens,
            breakpoints=bp_plan,
            constraints=constraint_report,
            counts=counts,
            confidence=confidence,
            diagnostics=diagnostics,
        )

    # ------------------------------------------------------------ breakpoints
    def _collect_signatures(
        self,
        document: IRDocument,
        base_width: float,
        breakpoint_model: BreakpointModel,
    ) -> Dict[str, List[Any]]:
        widths = sorted(set([base_width] + breakpoint_model.widths()))
        by_node: Dict[str, List[Any]] = {}
        for width in widths:
            run = self._engine.screens(document, viewport=width, base_width=base_width)
            for screen in run:
                for plan in _flatten(screen):
                    by_node.setdefault(plan.node_id, []).append((width, signature(plan)))
        return by_node

    @staticmethod
    def _attach_breakpoints(screens, changes) -> None:
        for change in changes:
            for screen in screens:
                node = screen.descendant(change.node_id) if change.node_id else None
                if node is not None:
                    node.breakpoints.append(change)
                    break

    # ------------------------------------------------------------- counting
    def _counts(self, screens) -> Dict[str, int]:
        counts: Dict[str, int] = {
            "nodes": 0,
            "flex": 0, "grid": 0, "absolute": 0, "static": 0,
            SIZING_FIXED: 0, SIZING_FILL: 0, SIZING_HUG: 0, SIZING_PERCENT: 0,
            "text_nodes": 0, "wrapped_text": 0, "clipped_content": 0,
            "contradictions": 0, "underdetermined": 0, "bounds_mismatch": 0,
        }
        for screen in screens:
            for plan in _flatten(screen):
                counts["nodes"] += 1
                by_display = {DISPLAY_FLEX: "flex", DISPLAY_GRID: "grid",
                              DISPLAY_ABSOLUTE: "absolute"}.get(plan.display)
                counts[by_display or "static"] += 1
                if plan.sizing and plan.sizing.horizontal:
                    counts[plan.sizing.horizontal.mode] = counts.get(plan.sizing.horizontal.mode, 0) + 1
                if plan.kind == "text":
                    counts["text_nodes"] += 1
                if plan.text and plan.text.wrapped:
                    counts["wrapped_text"] += 1
                if plan.overflow and plan.overflow.clipped_content:
                    counts["clipped_content"] += 1
                for diag in plan.diagnostics:
                    if diag.code == "contradiction":
                        counts["contradictions"] += 1
                    elif diag.code == "underdetermined":
                        counts["underdetermined"] += 1
                    elif diag.code == "bounds_mismatch":
                        counts["bounds_mismatch"] += 1
        return counts

    # ---------------------------------------------------------- confidence
    def _confidence(self, screens) -> Dict[str, Any]:
        scores = [self._score_confidence(plan) for screen in screens
                  for plan in _flatten(screen)]
        if not scores:
            return {"min": 1.0, "mean": 1.0, "high": 0, "medium": 0, "low": 0}
        high = sum(1 for s in scores if s > CONFIDENCE_HIGH)
        medium = sum(1 for s in scores if CONFIDENCE_MEDIUM <= s <= CONFIDENCE_HIGH)
        low = sum(1 for s in scores if s < CONFIDENCE_MEDIUM)
        return {
            "min": round(min(scores), 4),
            "mean": round(sum(scores) / len(scores), 4),
            "high": high,
            "medium": medium,
            "low": low,
        }

    def _score_confidence(self, plan) -> float:
        """Evidence-based per-node confidence in 0..1 (deterministic)."""
        if any(d.code == "contradiction" for d in plan.diagnostics):
            plan.confidence = 0.0
            return 0.0
        score = 1.0
        for assumption in plan.assumptions:
            score -= _ASSUMPTION_PENALTY.get(assumption, 0.0)
        if any(d.code == "bounds_mismatch" for d in plan.diagnostics):
            score -= 0.10
        score = max(0.0, min(1.0, score))
        plan.confidence = round(score, 4)
        return plan.confidence

    # ---------------------------------------------------------- diagnostics
    def _document_diagnostics(self, screens) -> List[Diagnostic]:
        diagnostics: List[Diagnostic] = []
        # Native scroll is not modeled by Figma IR; report rather than invent.
        diagnostics.append(Diagnostic(
            severity=SEVERITY_INFO,
            code="unsupported",
            message=(
                "native overflow:scroll is not represented in the Design IR; "
                "overflow is modeled as visible/clip and scroll is never inferred"
            ),
        ))
        # Surface the biggest confidence hits as a document-level view.
        for screen in screens:
            for plan in _flatten(screen):
                if plan.confidence < 0.6:
                    diagnostics.append(Diagnostic(
                        severity=SEVERITY_INFO,
                        code="low_confidence",
                        message=(
                            f"node {plan.name!r} ({plan.node_id}) has confidence "
                            f"{plan.confidence:.2f} due to {plan.assumptions or 'unverified bounds'}"
                        ),
                        node_id=plan.node_id,
                    ))
        return diagnostics

    # ---------------------------------------------------------- constraints
    @staticmethod
    def _flatten_constraints(screens) -> ConstraintReport:
        total = grounding = derived = 0
        contradictions, underdetermined, unsupported = [], [], []
        for screen in screens:
            for plan in _flatten(screen):
                report = plan.constraints
                if report is None:
                    continue
                total += report.total
                grounding += report.grounding
                derived += report.derived
                contradictions.extend(report.contradictions)
                underdetermined.extend(report.underdetermined)
                unsupported.extend(report.unsupported)
        return ConstraintReport(
            total=total, grounding=grounding, derived=derived,
            contradictions=contradictions, underdetermined=underdetermined,
            unsupported=unsupported,
        )

    # ------------------------------------------------------------- helpers
    @staticmethod
    def _base_width(document: IRDocument) -> float:
        """The design's native width = the widest top-level frame width."""
        widths: List[float] = []
        for page in document.pages:
            for frame in page.children:
                dims = frame.dimensions
                if dims and dims.width:
                    widths.append(dims.width)
        if not widths:
            return 1440.0  # documented default when no frame width exists
        return max(widths)