"""
Visual Repair Loop (Part 8).

The automatic visual repair loop that iterates between:

1. Render → Diff → Classify → Plan → Execute → Re-render

until stopping conditions are met.  The loop modifies *source code and
design tokens*, never screenshots or reference images.

Stopping conditions (any one terminates the loop):

- **Threshold satisfied**: similarity score >= configured threshold.
- **No safe repair**: the planner produced zero patches.
- **Insufficient progress**: improvement < configured minimum.
- **Max iterations**: iteration count reached the configured limit.

Safety rules:

- Never hide differences, blur screenshots, or alter reference images.
- Never make arbitrary broad rewrites.
- Prefer regeneration over manual editing.
- Support human approval before writing to the real repository.
- Support rollback to any previous iteration.

Design goals — consistent with FigmaForge conventions:

- Standard library only; no external dependencies.
- No agent frameworks (ADK, LangGraph, CrewAI).
- Deterministic and reproducible.
"""

from __future__ import annotations

import copy
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol

from .diff_engine import DiffEngine, DiffReport, RasterOptions
from .generator_types import GeneratorManifest, VStyle
from .ir_types import IRDocument
from .layout_types import LayoutPlan
from .library_types import ProjectLibrary
from .patch_executor import ExecutionResult, PatchExecutor
from .patch_planner import PatchPlan, PatchPlanner
from .repair_classifier import ClassificationReport, RepairClassifier
from .repair_history import IterationRecord, RepairHistory

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class RepairConfig:
    """Configuration for the repair loop."""

    # Stopping conditions
    similarity_threshold: float = 0.95     # stop when score >= this
    max_iterations: int = 10               # hard iteration limit
    min_progress: float = 0.005            # minimum improvement per iteration
    min_patches_per_iteration: int = 1     # stop if fewer patches generated

    # Safety
    require_approval: bool = False         # pause for human approval
    auto_rollback_on_regression: bool = True  # roll back if score drops
    max_rollback_iterations: int = 3       # max iterations to look back

    # Output
    output_dir: Optional[Path] = None      # where to write iteration artifacts

    # Raster (pixel) diffing — Part 12. The baseline PNG is SUPPLEMENTARY:
    # when None, diffing is structural-only (Part 7 behavior).
    baseline_png: Optional[str] = None     # path to the Figma baseline PNG
    color_threshold: int = 16              # max per-channel delta ignored
    noise_floor: float = 0.01              # diffRatio <= floor → pixels = 1.0
    min_region_area: int = 8               # contiguous diff regions >= 8px
    pixel_weight: float = 0.15             # capped weight in overall score

    def __post_init__(self) -> None:
        # Fail fast on invalid raster knobs at config time, before the loop
        # starts (Part 12): RasterOptions performs the actual validation.
        try:
            RasterOptions(
                color_threshold=self.color_threshold,
                noise_floor=self.noise_floor,
                min_region_area=self.min_region_area,
                pixel_weight=self.pixel_weight,
            )
        except ValueError as exc:
            raise ValueError(f"invalid raster knob: {exc}") from exc

    def to_dict(self) -> Dict[str, Any]:
        return {
            "similarity_threshold": self.similarity_threshold,
            "max_iterations": self.max_iterations,
            "min_progress": self.min_progress,
            "min_patches_per_iteration": self.min_patches_per_iteration,
            "require_approval": self.require_approval,
            "auto_rollback_on_regression": self.auto_rollback_on_regression,
            "max_rollback_iterations": self.max_rollback_iterations,
            "baseline_png": self.baseline_png,
            "color_threshold": self.color_threshold,
            "noise_floor": self.noise_floor,
            "min_region_area": self.min_region_area,
            "pixel_weight": self.pixel_weight,
        }


# ---------------------------------------------------------------------------
# Stop reasons
# ---------------------------------------------------------------------------

STOP_THRESHOLD = "threshold_satisfied"
STOP_NO_REPAIR = "no_safe_repair"
STOP_NO_PROGRESS = "insufficient_progress"
STOP_MAX_ITERATIONS = "max_iterations_reached"
STOP_APPROVAL_DENIED = "approval_denied"
STOP_REGRESSION = "regression_detected"


# ---------------------------------------------------------------------------
# Render protocol (dependency injection for the render step)
# ---------------------------------------------------------------------------


class RenderCallable(Protocol):
    """Protocol for the render step.

    Takes the current state (plan, styles, document) and returns a
    (render_meta dict, screenshot_path str) tuple.
    """

    def __call__(
        self,
        plan: LayoutPlan,
        styles: Dict[str, VStyle],
        document: IRDocument,
        iteration: int,
    ) -> tuple: ...


class ApprovalCallable(Protocol):
    """Protocol for the human-approval step.

    Takes the patch plan and returns True if approved, False if denied.
    """

    def __call__(self, plan: PatchPlan, iteration: int) -> bool: ...


# ---------------------------------------------------------------------------
# Default render (deterministic mock for testing)
# ---------------------------------------------------------------------------


def _default_render(
    plan: LayoutPlan,
    styles: Dict[str, VStyle],
    document: IRDocument,
    iteration: int,
) -> tuple:
    """Default render that returns empty metadata.

    In production this would invoke Playwright or another browser renderer.
    For testing, it returns metadata derived from the layout plan.
    """
    render_meta: Dict[str, Any] = {}
    for node in plan.nodes():
        if node.box:
            render_meta[node.node_id] = {
                "x": node.box.x,
                "y": node.box.y,
                "width": node.box.width,
                "height": node.box.height,
            }
    return render_meta, ""


# ---------------------------------------------------------------------------
# Repair loop result
# ---------------------------------------------------------------------------


@dataclass
class RepairResult:
    """The final result of a repair-loop run."""

    success: bool = False
    final_score: float = 0.0
    iterations_run: int = 0
    stop_reason: str = ""
    history: Optional[RepairHistory] = None

    @property
    def unresolved_differences(self) -> int:
        """Number of mismatches remaining in the final diff report."""
        if self.history is None or not self.history.iterations:
            return 0
        last = self.history.iterations[-1]
        if last.diff_report is None:
            return 0
        return len(last.diff_report.get("mismatches", []))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "final_score": self.final_score,
            "iterations_run": self.iterations_run,
            "stop_reason": self.stop_reason,
            "unresolved_differences": self.unresolved_differences,
            "history": self.history.to_dict() if self.history else None,
        }


# ---------------------------------------------------------------------------
# Repair loop
# ---------------------------------------------------------------------------


class RepairLoop:
    """The automatic visual repair loop controller.

    Orchestrates the iterate-between-render-diff-classify-plan-execute cycle
    until a stopping condition is met.
    """

    def __init__(
        self,
        config: Optional[RepairConfig] = None,
        render_fn: Optional[RenderCallable] = None,
        approval_fn: Optional[ApprovalCallable] = None,
    ):
        self._config = config or RepairConfig()
        self._render_fn = render_fn or _default_render
        self._approval_fn = approval_fn

    def _raster_options(self) -> RasterOptions:
        """Build raster diff knobs from the config (Part 12)."""
        return RasterOptions(
            color_threshold=self._config.color_threshold,
            noise_floor=self._config.noise_floor,
            min_region_area=self._config.min_region_area,
            pixel_weight=self._config.pixel_weight,
        )

    def run(
        self,
        plan: LayoutPlan,
        document: IRDocument,
        library: Optional[ProjectLibrary] = None,
        styles: Optional[Dict[str, VStyle]] = None,
        manifest: Optional[GeneratorManifest] = None,
        run_id: str = "",
    ) -> RepairResult:
        """Execute the repair loop.

        Args:
            plan: The layout plan to repair.
            document: The design IR document (source of truth).
            library: The project library (tokens, components).
            styles: Current VStyle dictionaries keyed by node_id.
            manifest: The generator manifest (node_id → filepath).
            run_id: Unique identifier for this repair run.

        Returns:
            A RepairResult with the final score, iteration count, and
            complete history.
        """
        styles = styles or {}
        history = RepairHistory(
            run_id=run_id or f"repair-{int(time.time())}",
            status="running",
        )

        diff_engine = DiffEngine()
        prev_score = 0.0

        for iteration in range(self._config.max_iterations):
            # Step 1: Render
            render_meta, screenshot_path = self._render_fn(
                plan, styles, document, iteration,
            )

            # Step 2: Diff
            diff_report = diff_engine.diff(
                plan,
                render_meta,
                render_screenshot=screenshot_path or None,
                baseline_png=self._config.baseline_png,
                raster_options=self._raster_options(),
            )
            score = diff_report.similarity_score

            # Step 3: Classify
            classifier = RepairClassifier(
                plan=plan, document=document, manifest=manifest,
            )
            classification = classifier.classify(diff_report)

            # Step 4: Plan
            planner = PatchPlanner(
                plan=plan, document=document, library=library,
            )
            patch_plan = planner.plan(classification, iteration=iteration)

            # Step 5: Record iteration
            record = IterationRecord(
                iteration=iteration,
                similarity_before=prev_score,
                similarity_after=score,
                diff_report=diff_report.to_dict(),
                classification=classification.to_dict(),
                patch_plan=patch_plan.to_dict(),
                screenshot_path=screenshot_path,
            )

            # Step 6: Check stopping conditions (before applying)
            stop_reason = self._check_stopping(
                score, prev_score, patch_plan, iteration,
            )
            if stop_reason is not None:
                record.stopped = True
                record.stop_reason = stop_reason
                record.similarity_after = score
                history.record_iteration(record)
                history.mark_completed(score)
                return RepairResult(
                    success=(stop_reason == STOP_THRESHOLD),
                    final_score=score,
                    iterations_run=iteration,
                    stop_reason=stop_reason,
                    history=history,
                )

            # Step 7: Approval gate
            if self._config.require_approval and self._approval_fn is not None:
                if not self._approval_fn(patch_plan, iteration):
                    record.stopped = True
                    record.stop_reason = STOP_APPROVAL_DENIED
                    record.similarity_after = score
                    history.record_iteration(record)
                    history.mark_completed(score)
                    return RepairResult(
                        success=False,
                        final_score=score,
                        iterations_run=iteration,
                        stop_reason=STOP_APPROVAL_DENIED,
                        history=history,
                    )

            # Step 8: Execute patches
            executor = PatchExecutor(
                plan=plan,
                document=document,
                library=library,
                styles=styles,
            )
            exec_result = executor.execute(patch_plan)

            # Record execution details
            record.execution_result = exec_result.to_dict()
            record.repair_decisions = [
                {"patch_id": p.patch_id, "target_type": p.target_type,
                 "target_key": p.target_key, "reason": p.reason}
                for p in patch_plan.patches
            ]
            record.source_diff = {
                "applied_patches": exec_result.success_count,
                "rejected_patches": exec_result.failure_count,
            }

            # Step 9: Re-render to get the new score
            # NOTE (Part 12): assumes render_fn returns screenshots consistently
            # across iterations, so the raster regime matches the first diff.
            new_render_meta, new_screenshot = self._render_fn(
                plan, styles, document, iteration + 1,
            )
            new_diff = diff_engine.diff(
                plan,
                new_render_meta,
                render_screenshot=new_screenshot or None,
                baseline_png=self._config.baseline_png,
                raster_options=self._raster_options(),
            )
            new_score = new_diff.similarity_score

            # Step 10: Check for regression
            if (self._config.auto_rollback_on_regression
                    and new_score < score - 0.01):
                executor.rollback()
                record.stopped = True
                record.stop_reason = STOP_REGRESSION
                record.similarity_after = score
                history.record_iteration(record)
                history.mark_completed(score)
                return RepairResult(
                    success=False,
                    final_score=score,
                    iterations_run=iteration,
                    stop_reason=STOP_REGRESSION,
                    history=history,
                )

            record.similarity_after = new_score
            record.screenshot_path = new_screenshot
            history.record_iteration(record)
            prev_score = new_score

        # Max iterations reached
        history.mark_completed(prev_score)
        return RepairResult(
            success=False,
            final_score=prev_score,
            iterations_run=self._config.max_iterations,
            stop_reason=STOP_MAX_ITERATIONS,
            history=history,
        )

    # ------------------------------------------------------------------
    # Stopping conditions
    # ------------------------------------------------------------------

    def _check_stopping(
        self,
        score: float,
        prev_score: float,
        patch_plan: PatchPlan,
        iteration: int,
    ) -> Optional[str]:
        """Check if any stopping condition is met.  Returns reason or None."""
        # 1. Threshold satisfied
        if score >= self._config.similarity_threshold:
            return STOP_THRESHOLD

        # 2. No safe repair available
        if patch_plan.is_empty:
            return STOP_NO_REPAIR

        # 3. Insufficient progress (after second iteration — need at least
        #    two data points to measure progress)
        if iteration > 1:
            progress = score - prev_score
            if progress < self._config.min_progress:
                return STOP_NO_PROGRESS

        return None

    # ------------------------------------------------------------------
    # Rollback support
    # ------------------------------------------------------------------

    def rollback_to(
        self,
        history: RepairHistory,
        target_iteration: int,
        executor: PatchExecutor,
    ) -> bool:
        """Roll back to a previous iteration.

        Returns True if rollback succeeded, False if the target iteration
        doesn't exist.
        """
        state = history.get_rollback_state(target_iteration)
        if state is None:
            return False

        executor.rollback()
        history.mark_rolled_back(target_iteration)
        return True
