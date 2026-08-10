---
role: Skill
type: skill
scope: figmaforge
id: figmaforge:doctor
---

# Doctor

**Short description:** Inspect plugin structure, context cost, dependencies, and dormant integrations.

**Triggers:** [doctor, check, validate, inspect, audit, plugin health]

**Output:** A health report that:
- Verifies plugin structure (files, directories, metadata)
- Reads installed plugins inventory (read-only)
- Resolves optional capability references from catalog
- Identifies missing capabilities without errors
- Reports projected context cost (always-on token estimates)
- Warns on duplication between user plugins and FigmaForge
- Suggests (never auto-performs) project-local disabling of unrelated user plugins

**Constraints:**
- Read-only operations only
- Never installs dependencies or modifies user configuration
- Must handle missing optional external skills gracefully (do not fail routing)
- Reports context cost in tokens
- Must NOT vendor/copy/wrap user's installed skills
