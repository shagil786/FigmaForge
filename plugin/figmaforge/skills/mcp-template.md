---
role: Skill
type: skill
scope: figmaforge
id: figmaforge:mcp-template
---

# MCP Template

**Short description:** Explain or render an inert MCP template to stdout.

**Triggers:** [mcp template, explain template, render template, MCP configuration]

**Output:**
- For templates in `templates/mcp/`:
  - Reads the template file
  - Renders it to stdout (plain markdown or JSON)
  - Includes no credentials, no functioning commands
  - Uses `example.invalid` for URLs
  - Uses symbolic environment names (never actual values)
- For templates in `.mcp.json`:
  - Explains how to merge a reviewed template manually
  - No command writes `.mcp.json` or invokes `claude mcp add`/`login`

**Constraints:**
- Always renders to stdout (no file writes)
- Never includes functioning commands unless explicitly an example
- Never includes credential-like values (tokens, API keys)
- Templates are strictly inert — no execution, no approvals granted
- Never reads or writes user's actual `.mcp.json`
