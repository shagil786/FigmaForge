---
role: Skill
type: skill
scope: figmaforge
id: figmaforge:demo
---

# Demo

**Short description:** Run or explain the bounded demo.

**Triggers:** [demo, demonstration, smoke test, validation]

**Output:**
- **Bounded E2E demo (offline, no external dependencies):**
  1. Validate plugin structure with `claude plugin validate`
  2. Run detection against current repo (FigmaForge) — assert status=unclassified, no language modules/LSP activated
  3. Route task "Design a secure, testable CLI feature and define acceptance criteria" — assert universal phases, requirements/architecture/security roles, no concrete stack, no activation
  4. Create lifecycle state in temp directory
  5. Advance intake → discover → define with fixture evidence
  6. Exercise hook fixtures (safe no-op, approval request, no-toolchain post-edit no-op)
  7. Verify MCP/LSP templates inert
  8. Delete demo temp directory
  9. Recheck root PinchTab status and original config hash

- **Live smoke test (optional):**
  - Run `claude --plugin-dir ./plugin/figmaforge -p '/figmaforge:route Design a secure CLI feature'`
  - Verify output structure matches schema

**Constraints:**
- Demo is bounded and offline — no external API calls, no deployment, no network
- Always cleans up demo artifacts (temp directory, test runs)
- Never modifies user's working tree during demo
- Output must be repeatable and deterministic
