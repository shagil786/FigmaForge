---
role: Skill
type: skill
scope: figmaforge
id: figmaforge:lsp-template
---

# LSP Template

**Short description:** Recommend an official LSP plugin or render a custom template.

**Triggers:** [lsp, language server, autocomplete, code intelligence, linter]

**Output:**
- For detected languages:
  - Recommends official Claude Code LSP plugins from the plugin matrix
  - Lists required binary names for each language
  - Suggests local scope installation first
  - Always requires explicit user action (never auto-installs)
- For unsupported languages:
  - Renders a custom `.lsp.json` template from `templates/lsp/custom-server.example.json`
  - Includes placeholder values only (no functioning commands)
  - Provides guidance on how to configure the language server manually

**Constraints:**
- Never auto-installs LSP plugins or connects language servers
- Installation always requires explicit user action
- Uses local scope first (`~/.claude/lsp/` or `.claude/lsp/`)
- Never creates active `.lsp.json` in repo root or plugin root
- Custom templates are strictly inert (no functioning commands)
