"""
Router
Deterministic, evidence-based role selection and execution mode determination.
"""

import json
from dataclasses import dataclass
from typing import Dict, List, Any

from .catalog import Catalog
from .detector import RepositoryDetector


@dataclass
class RouteResult:
    """Router output schema."""

    phases: List[str]
    roles: List[Dict[str, Any]]
    external_skills: List[str]
    execution_mode: str
    stack_status: str
    approval_gates: List[str]
    unloaded_modules: List[str]


class Router:
    """Score and select roles based on request and detected evidence."""

    def __init__(
        self,
        catalog: Catalog,
        detector: RepositoryDetector,
    ):
        """Initialize router.

        Args:
            catalog: The 100-role catalog.
            detector: The repository detector instance.
        """
        self.catalog = catalog
        self.detector = detector

    def route(self, request: str) -> RouteResult:
        """Route a request through the detection + scoring pipeline.

        Args:
            request: User's natural language request.

        Returns:
            RouteResult with selected phases, roles, and execution mode.
        """
        # Run detection
        detection = self.detector.detect()

        # Get all roles
        all_roles = self.catalog.get_all_roles()

        # Extract trigger keywords from request
        triggers = self._extract_triggers(request)

        # Score roles
        scored_roles = self._score_roles(all_roles, triggers, detection)

        # Select top 3 roles
        selected_roles = sorted(scored_roles, key=lambda x: x["score"], reverse=True)[:3]

        # Determine phases
        phases = self._determine_phases(selected_roles)

        # Determine execution mode
        execution_mode = self._determine_execution_mode(
            selected_roles,
            detection["status"],
        )

        # Determine approval gates
        approval_gates = self._determine_approval_gates(
            selected_roles,
            execution_mode,
            detection["status"],
        )

        # Determine loaded vs unloaded modules
        languages = detection.get("languages", [])
        lsp_candidates = detection.get("lsp_candidates", [])

        # Unloaded modules are languages without evidence (e.g. C/C++ clangd binary present but no .c file)
        unloaded_modules = []
        for lang in ["c", "cpp", "swift", "go", "rust", "kotlin", "java", "csharp", "lua"]:
            if lang not in languages:
                # Check if binary exists but not evidence
                lsp = next((l for l in lsp_candidates if lang in l), None)
                if lsp:
                    unloaded_modules.append(lang)

        # Get external skills from capability refs
        external_skills = self._extract_external_skills(selected_roles)

        return RouteResult(
            phases=phases,
            roles=selected_roles,
            external_skills=external_skills,
            execution_mode=execution_mode,
            stack_status=detection["status"],
            approval_gates=approval_gates,
            unloaded_modules=unloaded_modules,
        )

    def _extract_triggers(self, request: str) -> List[str]:
        """Extract trigger keywords from request.

        Args:
            request: User request.

        Returns:
            List of trigger keywords.
        """
        # Common trigger words
        trigger_words = [
            "requirements", "epic", "roadmap", "okr", "prioritize",
            "ux", "design", "ui", "test", "security", "architecture",
            "backend", "frontend", "api", "database", "deploy",
            "fix", "bug", "feature", "improve", "optimize",
            "audit", "review", "code", "review",
            "qa", "testing", "test",
        ]

        request_lower = request.lower()
        triggers = []

        for word in trigger_words:
            if word in request_lower:
                triggers.append(word)

        return triggers

    def _score_roles(self, roles: List[Dict], triggers: List[str], detection: Dict) -> List[Dict]:
        """Score roles based on triggers, lifecycle phases, and evidence.

        Args:
            roles: All roles from catalog.
            triggers: Trigger keywords from request.
            detection: Detection result.

        Returns:
            List of roles with score and reason.
        """
        scored_roles = []

        for role in roles:
            score = 0
            reasons = []

            # +4: Explicit trigger match
            triggers_match = any(
                t in role.get("triggers", [])
                for t in triggers
            )
            if triggers_match:
                score += 4
                reasons.append("Explicit trigger match")

            # +3: Lifecycle-phase match (intake/discover/define/design/plan)
            for phase in detection.get("test_commands", []):
                if phase in role.get("phases", []):
                    score += 3
                    reasons.append(f"Lifecycle-phase match: {phase}")

            # +3: Repository signal match
            for signal in detection.get("languages", []):
                if signal in role.get("domain", ""):
                    score += 3
                    reasons.append(f"Repository signal match: {signal}")

            # +2: Requested deliverable match
            deliverables = role.get("deliverables", [])
            request_lower = detection.get("request", "").lower()
            if any(d in request_lower for d in deliverables):
                score += 2
                reasons.append("Deliverable match")

            # +1: Mapped external capability installed
            capability_refs = role.get("capability_refs", [])
            # We don't check actual installation here — that's done by the user/doctor
            if capability_refs:
                score += 1
                reasons.append(f"Has external capability refs: {capability_refs[0]}")

            # -5: Stack-specific role conflicts with detected evidence
            # Example: backend-engineer when no backend code exists
            domain = role.get("domain", "")
            stack_status = detection.get("status", "unclassified")

            if stack_status == "unclassified" and domain in [
                "application",
                "data",
                "delivery",
            ]:
                score -= 5
                reasons.append("Repo unclassified but role requires stack")

            # -3: Role requires a stack but repo is unclassified
            if stack_status == "unclassified" and len(detection.get("languages", [])) == 0:
                score -= 3
                reasons.append("Repo is unclassified")

            # Record score and reasons
            role_with_score = {
                **role,
                "score": score,
                "reasons": reasons,
            }

            if score > 0:
                scored_roles.append(role_with_score)

        # Include roles that failed (score -3) if only one trigger
        if len(triggers) == 1:
            for role in roles:
                score = 0
                for trigger in triggers:
                    if trigger in role.get("triggers", []):
                        score -= 3
                        break

                if score == -3:
                    role_with_score = {
                        **role,
                        "score": score,
                        "reasons": ["Single trigger match (unclassified)"],
                    }
                    scored_roles.append(role_with_score)

        return scored_roles

    def _determine_phases(self, selected_roles: List[Dict]) -> List[str]:
        """Determine lifecycle phases from selected roles.

        Args:
            selected_roles: Selected role dictionaries.

        Returns:
            List of phases.
        """
        phases = set()

        for role in selected_roles:
            for phase in role.get("phases", []):
                phases.add(phase)

        # Sort phases in lifecycle order
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

        return sorted(phases, key=lambda x: lifecycle_order.index(x) if x in lifecycle_order else 999)

    def _determine_execution_mode(self, selected_roles: List[Dict], stack_status: str) -> str:
        """Determine execution mode.

        Args:
            selected_roles: Selected roles.
            stack_status: Repository classification status.

        Returns:
            Execution mode string.
        """
        # If repo is unclassified, force isolated execution
        if stack_status == "unclassified":
            return "isolated_scout"

        # Check if any role requires isolated execution
        for role in selected_roles:
            if role.get("id", "") in [
                "context-scout",
                "fresh-verifier",
            ]:
                return "isolated_scout"

        # Check for roles that require planning
        has_planning_roles = any(
            "plan" in role.get("phases", [])
            for role in selected_roles
        )

        if has_planning_roles:
            return "isolated_planner"

        return "direct"

    def _determine_approval_gates(
        self,
        selected_roles: List[Dict],
        execution_mode: str,
        stack_status: str,
    ) -> List[str]:
        """Determine approval gates.

        Args:
            selected_roles: Selected roles.
            execution_mode: Execution mode.
            stack_status: Repository status.

        Returns:
            List of approval gates.
        """
        gates = []

        # External mutation gate
        for role in selected_roles:
            if any(
                t in role.get("triggers", [])
                for t in ["deploy", "push", "release", "migration"]
            ):
                gates.append("external_mutation")

        # Stack selection gate
        if stack_status == "unclassified":
            gates.append("stack_selection")

        # Language activation gate (only if we have LSP candidates)
        lsp_candidates = self.detector.detect().get("lsp_candidates", [])
        if lsp_candidates:
            gates.append("language_activation")

        # Project approval gate (general safety)
        if execution_mode == "direct":
            gates.append("project_approval")

        return gates

    def _extract_external_skills(self, selected_roles: List[Dict]) -> List[str]:
        """Extract external skill references from selected roles.

        Args:
            selected_roles: Selected roles.

        Returns:
            List of external skill references.
        """
        skills = []

        for role in selected_roles:
            capability_refs = role.get("capability_refs", [])
            for ref in capability_refs:
                # Ref format: "engineering-skills:senior-backend"
                if ":" in ref:
                    skills.append(ref)

        return list(set(skills))

    def _resolve_fallback_pack(self, role: Dict) -> str:
        """Resolve fallback pack name.

        Args:
            role: Role dictionary.

        Returns:
            Fallback pack name or empty string.
        """
        fallback = role.get("fallback_pack", "")
        if fallback:
            return fallback
        return ""
