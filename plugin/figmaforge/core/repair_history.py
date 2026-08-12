"""
Repair History Manifest (Part 8).

Tracks every iteration of the repair loop: source diffs, screenshots,
visual reports, and repair decisions.  Supports rollback to any previous
iteration by preserving the complete state at each step.

Design goals — consistent with FigmaForge conventions:

- Standard library only; no external dependencies.
- Append-only: iterations are never modified after recording.
- JSON-serializable for persistence.
- Supports rollback to any previous iteration index.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .diff_engine import DiffReport
from .patch_executor import ExecutionResult, MutationRecord
from .patch_planner import PatchPlan
from .repair_classifier import ClassificationReport


# ---------------------------------------------------------------------------
# Iteration record
# ---------------------------------------------------------------------------


@dataclass
class IterationRecord:
    """Complete record of one repair-loop iteration."""

    iteration: int = 0
    similarity_before: float = 0.0
    similarity_after: float = 0.0
    diff_report: Optional[Dict[str, Any]] = None
    classification: Optional[Dict[str, Any]] = None
    patch_plan: Optional[Dict[str, Any]] = None
    execution_result: Optional[Dict[str, Any]] = None
    screenshot_path: str = ""
    source_diff: Dict[str, Any] = field(default_factory=dict)
    repair_decisions: List[Dict[str, Any]] = field(default_factory=list)
    stopped: bool = False
    stop_reason: str = ""

    @property
    def improvement(self) -> float:
        return self.similarity_after - self.similarity_before

    def to_dict(self) -> Dict[str, Any]:
        return {
            "iteration": self.iteration,
            "similarity_before": self.similarity_before,
            "similarity_after": self.similarity_after,
            "improvement": self.improvement,
            "diff_report": self.diff_report,
            "classification": self.classification,
            "patch_plan": self.patch_plan,
            "execution_result": self.execution_result,
            "screenshot_path": self.screenshot_path,
            "source_diff": dict(self.source_diff),
            "repair_decisions": list(self.repair_decisions),
            "stopped": self.stopped,
            "stop_reason": self.stop_reason,
        }


# ---------------------------------------------------------------------------
# History manifest
# ---------------------------------------------------------------------------


@dataclass
class RepairHistory:
    """Append-only manifest of all repair-loop iterations.

    Preserves every iteration's diff report, classification, patch plan,
    execution result, and screenshot path.  Supports rollback to any
    previous iteration.
    """

    run_id: str = ""
    iterations: List[IterationRecord] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""
    final_score: float = 0.0
    status: str = "pending"  # "pending" | "running" | "completed" | "rolled_back" | "failed"

    @property
    def current_iteration(self) -> int:
        return len(self.iterations)

    @property
    def best_score(self) -> float:
        if not self.iterations:
            return 0.0
        return max(
            r.similarity_after for r in self.iterations
        )

    @property
    def total_improvement(self) -> float:
        if not self.iterations:
            return 0.0
        return self.iterations[-1].similarity_after - self.iterations[0].similarity_before

    def record_iteration(self, record: IterationRecord) -> None:
        """Append an iteration record.  Must be in iteration order."""
        if self.iterations and record.iteration <= self.iterations[-1].iteration:
            raise ValueError(
                f"Iteration {record.iteration} must be > "
                f"{self.iterations[-1].iteration}"
            )
        self.iterations.append(record)

    def get_iteration(self, index: int) -> Optional[IterationRecord]:
        """Get an iteration record by index (0-based)."""
        if 0 <= index < len(self.iterations):
            return self.iterations[index]
        return None

    def get_rollback_state(self, target_iteration: int) -> Optional[Dict[str, Any]]:
        """Get the source diff needed to roll back to a target iteration.

        Returns the source_diff from the target iteration, which represents
        the state of the source at that point.
        """
        record = self.get_iteration(target_iteration)
        if record is None:
            return None
        return {
            "target_iteration": target_iteration,
            "source_diff": record.source_diff,
            "similarity_at_target": record.similarity_after,
        }

    def mark_completed(self, final_score: float) -> None:
        """Mark the repair history as completed."""
        self.final_score = final_score
        self.status = "completed"

    def mark_failed(self, reason: str = "") -> None:
        """Mark the repair history as failed."""
        self.status = "failed"
        if self.iterations:
            self.iterations[-1].stopped = True
            self.iterations[-1].stop_reason = reason

    def mark_rolled_back(self, target_iteration: int) -> None:
        """Mark that a rollback was performed."""
        self.status = "rolled_back"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "iterations": [r.to_dict() for r in self.iterations],
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "final_score": self.final_score,
            "status": self.status,
            "current_iteration": self.current_iteration,
            "best_score": self.best_score,
            "total_improvement": self.total_improvement,
        }

    def to_json(self, indent: int = 2) -> str:
        """Serialize to deterministic JSON."""
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RepairHistory":
        """Deserialize from a dict."""
        history = cls(
            run_id=data.get("run_id", ""),
            started_at=data.get("started_at", ""),
            finished_at=data.get("finished_at", ""),
            final_score=data.get("final_score", 0.0),
            status=data.get("status", "pending"),
        )
        for iter_data in data.get("iterations", []):
            record = IterationRecord(
                iteration=iter_data.get("iteration", 0),
                similarity_before=iter_data.get("similarity_before", 0.0),
                similarity_after=iter_data.get("similarity_after", 0.0),
                diff_report=iter_data.get("diff_report"),
                classification=iter_data.get("classification"),
                patch_plan=iter_data.get("patch_plan"),
                execution_result=iter_data.get("execution_result"),
                screenshot_path=iter_data.get("screenshot_path", ""),
                source_diff=iter_data.get("source_diff", {}),
                repair_decisions=iter_data.get("repair_decisions", []),
                stopped=iter_data.get("stopped", False),
                stop_reason=iter_data.get("stop_reason", ""),
            )
            history.iterations.append(record)
        return history

    def save(self, path: Path) -> None:
        """Persist the history to a JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "RepairHistory":
        """Load a history from a JSON file."""
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_dict(data)
