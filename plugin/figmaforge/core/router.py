"""
Router
Deterministic, evidence-based role selection and execution mode determination.
"""

import json
from dataclasses import dataclass
from typing import Dict, List, Any, Optional

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

    def route(
        self,
        request: str,
        installed_capabilities: Optional[List[str]] = None,
    ) -> RouteResult:
        """Route a request through the detection + scoring pipeline.

        Args:
            request: User's natural language request.
            installed_capabilities: External capability refs actually installed.

        Returns:
            RouteResult with selected phases, roles, and execution mode.
        """
        # Run detection once and cache the result for all downstream steps.
        detection = self.detector.detect()

        # Get all roles
        all_roles = self.catalog.get_all_roles()

        # Extract trigger keywords from request
        triggers = self._extract_triggers(request)

        # Score roles
        scored_roles = self._score_roles(
            all_roles, triggers, detection,
            installed_capabilities=installed_capabilities,
        )

        # Select top 3 roles (may be empty if no role scored > 0 and no fallback)
        selected_roles = sorted(scored_roles, key=lambda x: x["score"], reverse=True)[:3]

        # Determine phases
        phases = self._determine_phases(selected_roles)

        # Determine execution mode
        execution_mode = self._determine_execution_mode(
            selected_roles,
            detection["status"],
        )

        # Determine approval gates (pass cached detection — no re-detection)
        approval_gates = self._determine_approval_gates(
            selected_roles,
            execution_mode,
            detection["status"],
            detection,
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

    # Trigger words mapped to the lifecycle phases they imply.
    _TRIGGER_WORDS = [
        "requirements", "epic", "roadmap", "okr", "prioritize",
        "ux", "design", "ui", "test", "security", "architecture",
        "backend", "frontend", "api", "database", "deploy",
        "fix", "bug", "feature", "improve", "optimize",
        "audit", "review", "code", "qa", "testing",
    ]

    # Map trigger keywords → lifecycle phase(s) for phase-match scoring.
    _TRIGGER_TO_PHASES: Dict[str, List[str]] = {
        "requirements": ["define"],
        "epic": ["define"],
        "roadmap": ["define"],
        "okr": ["define"],
        "prioritize": ["define"],
        "ux": ["design"],
        "design": ["design"],
        "ui": ["design"],
        "test": ["verify"],
        "testing": ["verify"],
        "qa": ["verify"],
        "security": ["verify"],
        "audit": ["verify"],
        "review": ["verify"],
        "architecture": ["design", "plan"],
        "backend": ["implement"],
        "frontend": ["implement"],
        "api": ["implement"],
        "database": ["implement"],
        "code": ["implement"],
        "feature": ["implement"],
        "fix": ["implement"],
        "bug": ["implement"],
        "improve": ["implement"],
        "optimize": ["implement"],
        "deploy": ["release"],
    }

    # Map detected languages → domains they are relevant to.
    _LANGUAGE_TO_DOMAIN: Dict[str, List[str]] = {
        "javascript": ["experience", "application"],
        "typescript": ["experience", "application"],
        "python": ["application", "data"],
        "go": ["application", "data"],
        "rust": ["application"],
        "java": ["application", "data"],
        "kotlin": ["application", "experience"],
        "csharp": ["application"],
        "php": ["application"],
        "ruby": ["application"],
        "swift": ["experience"],
        "elixir": ["application", "data"],
        "sql": ["data"],
        "r": ["data"],
    }

    def _extract_triggers(self, request: str) -> List[str]:
        """Extract trigger keywords from request.

        Args:
            request: User request.

        Returns:
            Deduplicated list of trigger keywords found in the request.
        """
        request_lower = request.lower()
        return [w for w in self._TRIGGER_WORDS if w in request_lower]

    def _score_roles(
        self,
        roles: List[Dict],
        triggers: List[str],
        detection: Dict,
        installed_capabilities: Optional[List[str]] = None,
    ) -> List[Dict]:
        """Score roles based on triggers, lifecycle phases, and evidence.

        Args:
            roles: All roles from catalog.
            triggers: Trigger keywords from request.
            detection: Detection result.
            installed_capabilities: External capability refs actually installed.

        Returns:
            List of roles with score and reason.
        """
        installed_caps = set(installed_capabilities or [])

        # Derive lifecycle phases implied by the request's trigger words.
        request_phases: List[str] = []
        for trigger in triggers:
            request_phases.extend(self._TRIGGER_TO_PHASES.get(trigger, []))
        request_phases = list(dict.fromkeys(request_phases))  # deduplicate, keep order

        # Derive domains relevant to the detected language stack.
        languages = detection.get("languages", [])
        relevant_domains: List[str] = []
        for lang in languages:
            relevant_domains.extend(self._LANGUAGE_TO_DOMAIN.get(lang, []))
        relevant_domains = list(dict.fromkeys(relevant_domains))

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

            # +3: Lifecycle-phase match — request triggers imply phases,
            # check whether the role operates in any of those phases.
            role_phases = role.get("phases", [])
            phase_overlap = [p for p in request_phases if p in role_phases]
            if phase_overlap:
                score += 3
                reasons.append(f"Lifecycle-phase match: {phase_overlap[0]}")

            # +3: Repository signal match — detected languages map to domains;
            # check whether the role's domain is relevant for the stack.
            domain = role.get("domain", "")
            if domain in relevant_domains:
                score += 3
                reasons.append(f"Repository signal match: {domain}")

            # +2: Requested deliverable match
            deliverables = role.get("deliverables", [])
            request_lower = detection.get("request", "").lower()
            if any(d in request_lower for d in deliverables):
                score += 2
                reasons.append("Deliverable match")

            # +1: Mapped external capability actually installed
            capability_refs = role.get("capability_refs", [])
            installed_refs = [r for r in capability_refs if r in installed_caps]
            if installed_refs:
                score += 1
                reasons.append(f"Installed capability ref: {installed_refs[0]}")

            # Penalty: stack-specific role conflicts with detected evidence.
            # -5 when unclassified AND role requires a concrete stack domain;
            # -3 when unclassified AND no languages detected (generic penalty).
            # These are mutually exclusive — the -5 subsumes the -3.
            stack_status = detection.get("status", "unclassified")

            if stack_status == "unclassified":
                if domain in ("application", "data", "delivery"):
                    score -= 5
                    reasons.append("Repo unclassified but role requires stack")
                elif not languages:
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

        # Fallback: when only a single trigger was found and no role scored
        # positively, include roles that at least recognise the trigger so the
        # caller gets a non-empty result.
        if len(triggers) == 1 and not scored_roles:
            for role in roles:
                if triggers[0] in role.get("triggers", []):
                    role_with_score = {
                        **role,
                        "score": 0,
                        "reasons": ["Single trigger fallback"],
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
        detection: Dict,
    ) -> List[str]:
        """Determine approval gates.

        Args:
            selected_roles: Selected roles.
            execution_mode: Execution mode.
            stack_status: Repository status.
            detection: Cached detection result (no re-detection).

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

        # Language activation gate (use cached detection, not a fresh call)
        lsp_candidates = detection.get("lsp_candidates", [])
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
