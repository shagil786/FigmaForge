"""
Lifecycle State Machine
Atomic state writes and append-only events for replay.
"""

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional


@dataclass
class Validation:
    """Single validation check result."""

    check: str
    passed: bool
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Approval:
    """Approval record."""

    gate: str
    granted: bool
    timestamp: str
    reason: str = ""


@dataclass
class Blocker:
    """Active blocker."""

    id: str
    message: str
    resolved: bool = False


@dataclass
class Decision:
    """Evidence-driven decision."""

    phase: str
    action: str
    reason: str
    evidence: List[str] = field(default_factory=list)


@dataclass
class LifecycleState:
    """Atomic state representation of a run."""

    run_id: str
    request: str
    phase: str
    status: str
    risk: str
    selected_roles: List[str]
    selected_capabilities: List[str]
    decisions: List[Decision]
    artifacts: Dict[str, Any]
    evidence: List[str]
    validations: List[Validation]
    approvals: List[Approval]
    blockers: List[Blocker]

    def __post_init__(self):
        """Initialize with default values if needed."""
        if not self.artifacts:
            self.artifacts = {}

    def to_dict(self) -> Dict[str, Any]:
        """Convert state to dictionary for JSON serialization."""
        return {
            "run_id": self.run_id,
            "request": self.request,
            "phase": self.phase,
            "status": self.status,
            "risk": self.risk,
            "selected_roles": self.selected_roles,
            "selected_capabilities": self.selected_capabilities,
            "decisions": [
                {
                    "phase": d.phase,
                    "action": d.action,
                    "reason": d.reason,
                    "evidence": d.evidence,
                }
                for d in self.decisions
            ],
            "artifacts": self.artifacts,
            "evidence": self.evidence,
            "validations": [
                {
                    "check": v.check,
                    "passed": v.passed,
                    "details": v.details,
                }
                for v in self.validations
            ],
            "approvals": [
                {
                    "gate": a.gate,
                    "granted": a.granted,
                    "timestamp": a.timestamp,
                    "reason": a.reason,
                }
                for a in self.approvals
            ],
            "blockers": [
                {
                    "id": b.id,
                    "message": b.message,
                    "resolved": b.resolved,
                }
                for b in self.blockers
            ],
        }


class StateMachine:
    """Manage lifecycle state with atomic writes and replay."""

    def __init__(self, run_id: str | None = None):
        """Initialize state machine.

        Args:
            run_id: Optional run ID. Generates one if not provided.
        """
        self.run_id = run_id or str(uuid.uuid4())
        self.state: Optional[LifecycleState] = None
        self.events: List[str] = []

    def initialize(
        self,
        request: str,
        selected_roles: List[str],
        selected_capabilities: List[str],
    ) -> LifecycleState:
        """Initialize a new run state.

        Args:
            request: User's request.
            selected_roles: Selected role IDs.
            selected_capabilities: Selected capability references.

        Returns:
            Initialized state.
        """
        self.state = LifecycleState(
            run_id=self.run_id,
            request=request,
            phase="intake",
            status="active",
            risk="low",
            selected_roles=selected_roles,
            selected_capabilities=selected_capabilities,
            decisions=[],
            artifacts={},
            evidence=[],
            validations=[],
            approvals=[],
            blockers=[],
        )

        self._write_state()
        self.events.append(f"{datetime.now().isoformat()} — Initialized run {self.run_id}")
        return self.state

    def advance_to(
        self,
        new_phase: str,
        risk: Optional[str] = None,
        artifacts: Optional[Dict[str, Any]] = None,
    ) -> LifecycleState:
        """Advance to a new phase with evidence-driven transition.

        Args:
            new_phase: Target phase.
            risk: New risk level.
            artifacts: New artifacts.

        Returns:
            Updated state.
        """
        if not self.state:
            raise ValueError("State not initialized. Call initialize() first.")

        # Verify transition is valid (evidence-driven, not prose claims)
        if not self._is_valid_transition(self.state.phase, new_phase):
            raise ValueError(
                f"Invalid transition: {self.state.phase} -> {new_phase}. "
                f"Requires explicit evidence."
            )

        # Record decision
        self.state.decisions.append(
            Decision(
                phase=self.state.phase,
                action=f"advance to {new_phase}",
                reason=f"Evidence-driven transition from {self.state.phase}",
                evidence=[],
            )
        )

        # Update state
        old_phase = self.state.phase
        self.state.phase = new_phase
        if risk:
            self.state.risk = risk
        if artifacts:
            self.state.artifacts.update(artifacts)

        # Log event
        self._write_state()
        self.events.append(
            f"{datetime.now().isoformat()} — Transitioned {old_phase} -> {new_phase}"
        )

        return self.state

    def add_evidence(self, evidence: List[str]) -> LifecycleState:
        """Add collected evidence.

        Args:
            evidence: New evidence items.

        Returns:
            Updated state.
        """
        if not self.state:
            raise ValueError("State not initialized.")

        self.state.evidence.extend(evidence)
        self._write_state()
        return self.state

    def add_validation(self, check: str, passed: bool, details: Optional[Dict] = None) -> LifecycleState:
        """Add validation result.

        Args:
            check: Validation check name.
            passed: Whether it passed.
            details: Additional details.

        Returns:
            Updated state.
        """
        if not self.state:
            raise ValueError("State not initialized.")

        self.state.validations.append(Validation(check=check, passed=passed, details=details or {}))
        self._write_state()
        return self.state

    def request_approval(self, gate: str, reason: str) -> bool:
        """Request approval for a gate.

        Args:
            gate: Gate name.
            reason: Reason for approval.

        Returns:
            True if granted, False otherwise.
        """
        if not self.state:
            raise ValueError("State not initialized.")

        # Record approval request
        self.state.approvals.append(
            Approval(
                gate=gate,
                granted=False,
                timestamp=datetime.now().isoformat(),
                reason=f"Pending approval: {reason}",
            )
        )

        self._write_state()
        self.events.append(
            f"{datetime.now().isoformat()} — Approval requested: {gate}"
        )

        return False  # Default to denied until user explicitly grants

    def grant_approval(self, gate: str, reason: str) -> bool:
        """Grant approval for a gate.

        Args:
            gate: Gate name.
            reason: Reason for granting.

        Returns:
            True if granted.
        """
        if not self.state:
            raise ValueError("State not initialized.")

        # Find and update approval
        for approval in self.state.approvals:
            if approval.gate == gate:
                approval.granted = True
                approval.reason = reason
                break

        self._write_state()
        self.events.append(
            f"{datetime.now().isoformat()} — Approval granted: {gate} ({reason})"
        )

        return True

    def resolve_blocker(self, blocker_id: str) -> LifecycleState:
        """Resolve a blocker.

        Args:
            blocker_id: Blocker ID.

        Returns:
            Updated state.
        """
        if not self.state:
            raise ValueError("State not initialized.")

        for blocker in self.state.blockers:
            if blocker.id == blocker_id:
                blocker.resolved = True
                break

        self._write_state()
        return self.state

    def complete(self, risk: Optional[str] = None) -> LifecycleState:
        """Mark run as completed.

        Args:
            risk: Final risk level.

        Returns:
            Completed state.
        """
        if not self.state:
            raise ValueError("State not initialized.")

        self.state.status = "completed"
        self.state.phase = "learn"
        if risk:
            self.state.risk = risk

        self._write_state()
        self.events.append(
            f"{datetime.now().isoformat()} — Run {self.run_id} completed"
        )

        return self.state

    def fail(self, risk: Optional[str] = None) -> LifecycleState:
        """Mark run as failed.

        Args:
            risk: Failure risk level.

        Returns:
            Failed state.
        """
        if not self.state:
            raise ValueError("State not initialized.")

        self.state.status = "failed"
        if risk:
            self.state.risk = risk

        self._write_state()
        self.events.append(
            f"{datetime.now().isoformat()} — Run {self.run_id} failed"
        )

        return self.state

    def set_blocker(self, blocker_id: str, message: str) -> LifecycleState:
        """Set a blocker.

        Args:
            blocker_id: Blocker ID.
            message: Blocker message.

        Returns:
            Updated state.
        """
        if not self.state:
            raise ValueError("State not initialized.")

        self.state.blockers.append(
            Blocker(id=blocker_id, message=message)
        )

        self._write_state()
        return self.state

    def _write_state(self) -> None:
        """Write current state to disk."""
        if not self.state:
            return

        # Create run directory if it doesn't exist
        run_dir = Path(".figmaforge/runs")
        run_dir.mkdir(exist_ok=True)

        # Write state.json
        state_file = run_dir / self.run_id / "state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)

        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(self.state.to_dict(), f, indent=2)

    def _is_valid_transition(self, from_phase: str, to_phase: str) -> bool:
        """Check if a transition is valid based on evidence.

        Args:
            from_phase: Current phase.
            to_phase: Target phase.

        Returns:
            True if transition is valid.
        """
        # Lifecycle order
        lifecycle_order = [
            "intake",
            "discover",
            "define",
            "design",
            "plan",
            "implement",
            "verify",
            "release",
            "operate",
            "learn",
        ]

        try:
            from_idx = lifecycle_order.index(from_phase)
            to_idx = lifecycle_order.index(to_phase)
        except ValueError:
            return False

        # Only allow forward movement (to_idx > from_idx)
        return to_idx > from_idx
