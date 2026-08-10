---
role: Skill
type: skill
scope: figmaforge
id: figmaforge:route
---

# Route

**Short description:** Detect context and select phases, roles, existing skills, and execution mode.

**Triggers:** [detect, route, adapt, detect context, select roles]

**Output:** A route result with:
- Selected lifecycle phases
- Up to 3 selected roles with scores and reasons
- External skill references
- Execution mode (direct | isolated_scout | isolated_planner | fresh_verifier)
- Stack status (unclassified | classified)
- Approval gates that need user approval
- Unloaded modules (languages detected as present but no evidence)

**Constraints:**
- Uses detector.py to inspect repository
- Uses catalog/roles.json to query 100 roles
- Scores roles based on triggers, lifecycle phases, and repository signals
- Never installs plugins or connects MCP servers
- Deterministic and bounded before Claude interprets the result
