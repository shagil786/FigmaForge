---
kind: external_dependency
name: Claude Code CLI — host platform for the FigmaForge plugin
slug: claude-code
category: external_dependency
category_hints:
    - vendor_identity
    - client_constraint
scope:
    - '**'
source_files:
    - .mcp.json
    - plugin/figmaforge/.claude-plugin/plugin.json
    - plugin/figmaforge/hooks/hooks.json
---

### Identity
Anthropic's Claude Code CLI, which loads FigmaForge as a plugin via `--plugin-dir ./plugin/figmaforge` and validates it with `claude plugin validate --strict`.

### Role in this repo
FigmaForge is explicitly NOT a standalone application; it is a Claude Code plugin providing skills, agents, hooks, detector/router, lifecycle state machine, and the Figma-to-code pipeline. The CLI hosts session lifecycle, tool use gates, and MCP/LSP integration.

### Integration points
- Plugin manifest: `plugin/figmaforge/.claude-plugin/plugin.json`
- Hook registration: `plugin/figmaforge/hooks/hooks.json` (SessionStart, PreToolUse, PostToolUse)
- Skills/agents defined under `plugin/figmaforge/skills/` and `plugin/figmaforge/agents/`
- Root `.mcp.json` declares a project-scoped stdio MCP server named `pinchtab`.

### Stable constraints
- No MCP server approved/connected automatically by the plugin.
- No LSP plugin activated solely because its binary exists.
- Root `.mcp.json` must retain same semantics; templates under `templates/mcp/` are inert examples only.
- Safety invariants include no automatic stack inference from repository name and no plaintext credential exposure.