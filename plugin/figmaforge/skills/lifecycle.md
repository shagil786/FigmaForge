---
role: Skill
type: skill
scope: figmaforge
id: figmaforge:lifecycle
---

# Lifecycle

**Short description:** Create or advance an evidence-backed task run.

**Triggers:** [lifecycle, run, task, state, phase]

**Output:**
- Initialized run state with run_id, request, selected_roles
- Advanced state through lifecycle phases (intake → discover → define → design → plan → implement → verify → release → operate → learn)
- Evidence-driven transitions (not prose claims)
- Atomic state writes to `.figmaforge/runs/<run-id>/state.json`
- Append-only event log to `.figmaforge/runs/<run-id>/events.jsonl`

**Constraints:**
- Only creates/advances state when transitions are evidence-driven
- Requires explicit approval gates for external mutations
- Atomic state writes only (no partial updates)
- Must handle approval requests from user
- Never creates directories that should be gitignored
