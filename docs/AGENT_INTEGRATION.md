# FigmaForge — Agent Integration Guide

FigmaForge is a **tool**, not a product. It extracts structured design data from
Figma files (or screenshots) and generates production-quality code. Any AI agent
that can run shell commands can use it.

```
Figma File / Screenshot
        ↓
    FigmaForge (extract structured design data)
        ↓
    AI Agent (interprets intent, generates code)
        ↓
    FigmaForge (compares output vs original design)
        ↓
    Agent iterates until similarity score passes
```

## Quick Start

### One command (recommended)

```bash
# Full pipeline: spec + generate + compare in one shot
figmaforge agent-loop --file design.json --backend react_tailwind --baseline design.png
```

This returns a single JSON object with `spec`, `generated`, and `feedback`.

### Step by step

```bash
# 1. Extract the semantic design spec (what the page IS)
figmaforge spec --file design.json

# 2. Generate code for a target framework
figmaforge generate --file design.json --backend react_tailwind

# 3. Compare generated output against the original design
figmaforge compare --baseline design.png --generated out/html_css/
```

The `spec` output is structured JSON that any LLM can understand — sections,
layout intent, design tokens, content. No prompt engineering needed.

## Available Commands

| Command | What it does | Agent use case |
|---------|-------------|----------------|
| `spec` | Semantic design spec (agent-readable JSON) | Primary input — gives agent the design context |
| `ingest` | Normalize a raw Figma file JSON | Step before spec if working from raw Figma API output |
| `normalize` | Build the design IR | Deeper access to the full IR tree |
| `audit` | Check source completeness | Pre-flight: are there missing assets/fonts? |
| `resolve` | Match against project library | Component reuse detection |
| `layout` | Infer layout plan (flex/grid/absolute) | Understand spacing, alignment, sizing |
| `assets` | Download + content-address images | Asset pipeline for generated code |
| `generate` | Generate code for a backend | Produce HTML/React/Vue/Svelte/SwiftUI/Flutter |
| `repair` | Visual repair loop | Auto-fix toward a baseline screenshot |
| `compare` | Pixel-diff two PNGs + actionable feedback JSON | Measure similarity, find mismatch regions |
| `agent-loop` | Full pipeline: spec → generate → compare | One command for the complete agent workflow |
| `image_ingest` | Analyze any screenshot → design IR | No Figma file needed — works from a PNG |

## Agent Patterns

### Pattern 1: Figma-to-Code (recommended)

The agent receives a Figma file and produces production code.

```bash
# Step 1: Get the design spec
figmaforge spec --file figma-export.json

# Step 2: Agent reads the spec and generates code
# (agent uses the spec as context in its generation)

# Step 3: Compare and iterate
figmaforge compare --baseline original.png --generated output/
```

### Pattern 2: Screenshot-to-Code (no Figma needed)

The agent receives a screenshot and recreates it.

```bash
# Step 1: Analyze the screenshot
figmaforge image_ingest --image screenshot.png --out ir.json

# Step 2: Generate the spec from the IR
figmaforge spec --file ir.json

# Step 3: Agent generates code from the spec
# Step 4: Compare
figmaforge compare --baseline screenshot.png --generated output/
```

### Pattern 3: URL-to-Code

The agent scrapes a website and recreates it.

```bash
# Agent scrapes the site, produces a design description
# Agent generates code from the description
# Agent compares against a screenshot of the original
figmaforge compare --baseline site-screenshot.png --generated output/
```

## Integration with Specific Agents

### Freebuff / OpenCode

Create a skill file at `~/.config/opencode/skills/figmaforge/SKILL.md`:

```markdown
---
name: figmaforge
description: >
  Use this skill when the user wants to generate code from Figma designs,
  screenshots, or mockups. FigmaForge extracts structured design data
  and produces pixel-accurate code for React, Vue, Svelte, HTML/CSS,
  SwiftUI, and Flutter.
---

# FigmaForge Design-to-Code Tool

## When to use
- User provides a Figma file or URL
- User provides a screenshot/mockup and wants code
- User wants to recreate an existing website

## How to use

### Get the design spec
\`\`\`bash
figmaforge spec --file <figma-export.json>
\`\`\`

The spec output is structured JSON with:
- `page.name` — the design name
- `design_tokens.colors` — color palette
- `design_tokens.typography` — font styles
- `sections[]` — semantic sections with layout and content

### Generate code
\`\`\`bash
figmaforge generate --file <design.json> --backend <target>
\`\`\`

Backends: `html_css`, `react_tailwind`, `vue`, `svelte`, `swiftui`, `flutter`

### Compare output
\`\`\`bash
figmaforge compare --baseline <design.png> --generated <output-dir>/
\`\`\`

Returns a similarity score (0.0–1.0). SSIM > 0.95 is pixel-accurate.

### Full pipeline (single command)
\`\`\`bash
figmaforge run --file <design.json> --target <target> --baseline <design.png>
\`\`\`
```

### Claude Code / Cursor / Copilot

FigmaForge works as a shell tool. Add to your project's `AGENTS.md`:

```markdown
# Design-to-Code with FigmaForge

When generating code from designs:

1. Run `figmaforge spec --file <figma.json>` to get the design context
2. Use the spec JSON as input for code generation
3. Run `figmaforge compare --baseline <design.png> --generated <output>/` to verify

Available backends: html_css, react_tailwind, vue, svelte, swiftui, flutter
```

### MCP Integration (Claude Desktop, Freebuff, OpenCode, any MCP client)

FigmaForge ships with a built-in MCP server (`mcp_server.py`) that exposes
6 tools over JSON-RPC stdio. No external dependencies required.

#### Setup

Add to your MCP client config:

```json
{
  "mcpServers": {
    "figmaforge": {
      "command": "/opt/homebrew/bin/python3.14",
      "args": ["/path/to/FigmaForge/plugin/figmaforge/mcp_server.py"]
    }
  }
}
```

#### Exposed tools

| Tool | What it does |
|------|-------------|
| `figmaforge_spec` | Extract semantic design spec from a Figma file JSON |
| `figmaforge_generate` | Generate code for a target framework |
| `figmaforge_compare` | Pixel-diff two PNGs with actionable feedback |
| `figmaforge_agent_loop` | Full pipeline: spec → generate → compare in one call |
| `figmaforge_image_ingest` | Analyze any screenshot → design IR via vision model |
| `figmaforge_audit` | Pre-flight completeness check on raw Figma payload |

#### Example: agent calls spec via MCP

```json
{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
  "name": "figmaforge_spec",
  "arguments": {"file": "design.json"}
}}
```

Response:
```json
{"jsonrpc": "2.0", "id": 1, "result": {
  "content": [{"type": "text", "text": "{\"sections\": [...], \"design_tokens\": {...}}"}]
}}
```

## Design Spec Output Format

The `spec` command produces this JSON structure:

```json
{
  "page": {
    "name": "Landing Page"
  },
  "design_tokens": {
    "colors": [
      {"value": "#10b981", "count": 5},
      {"value": "#ffffff", "count": 4}
    ],
    "typography": [
      {"fontFamily": "Inter", "fontSize": 14, "fontWeight": 400},
      {"fontFamily": "Inter", "fontSize": 64, "fontWeight": 700}
    ]
  },
  "sections": [
    {
      "id": "header",
      "name": "Header",
      "type": "navigation",
      "layout": "flex-row",
      "background": "#0a1628",
      "gap": 24,
      "content": [
        {"type": "heading", "text": "Logo", "fontSize": 18, "fontWeight": 700},
        {"type": "nav-link", "text": "Features"},
        {"type": "nav-link", "text": "Pricing"},
        {"type": "button", "text": "Get Started", "background": "#10b981"}
      ]
    },
    {
      "id": "hero",
      "name": "Hero",
      "type": "hero",
      "layout": "flex-column",
      "gap": 16,
      "content": [
        {"type": "heading", "text": "Build Faster", "fontSize": 64, "fontWeight": 700},
        {"type": "subheading", "text": "Ship production apps in minutes"},
        {"type": "button", "text": "Start Free", "background": "#10b981"}
      ]
    }
  ]
}
```

### Section Types

| Type | Pattern | Description |
|------|---------|-------------|
| `navigation` | header, nav, navbar, menu | Top navigation bar |
| `hero` | hero, banner, jumbotron | Hero section with main CTA |
| `features` | feature, service, capability | Feature grid or list |
| `cta` | cta, call-to-action, sign-up | Call-to-action block |
| `testimonials` | testimonial, review, quote | Social proof section |
| `pricing` | pricing, plan, tier | Pricing table |
| `footer` | footer, bottom, legal | Page footer |
| `content` | (default) | Generic content block |

### Layout Types

| Layout | Meaning |
|--------|---------|
| `flex-row` | Horizontal flex (nav, feature cards in a row) |
| `flex-column` | Vertical flex (hero, stacked sections) |
| `grid` | CSS grid layout |
| `stack` | Absolute/overlapping (ZStack in SwiftUI) |

## Available Backends

| Backend | Output | Best for |
|---------|--------|----------|
| `html_css` | Standalone `.html` | Quick preview, email templates |
| `react_tailwind` | `.tsx` + Tailwind | React/Next.js projects |
| `vue` | `.vue` SFC | Vue/Nuxt projects |
| `svelte` | `.svelte` component | SvelteKit projects |
| `swiftui` | `.swift` view | iOS/macOS apps |
| `flutter` | `.dart` widget | Cross-platform mobile |

## Environment Variables

| Variable | Purpose | Required |
|----------|---------|----------|
| `FIGMA_TOKEN` | Figma personal access token | For live Figma API access |
| `NVIDIA_API_KEY` | NVIDIA API key for image analysis | For `image_ingest` |
| `ANTHROPIC_API_KEY` | Anthropic API key (alternative) | For `image_ingest` |
| `OPENAI_API_KEY` | OpenAI API key (alternative) | For `image_ingest` |
| `PYTHON_BIN` | Path to Python 3.14+ binary | Required on this machine |

## Troubleshooting

### "No vision model configured"
Set one of: `NVIDIA_API_KEY`, `ANTHROPIC_API_KEY`, or `OPENAI_API_KEY`.

### "Figma API token is not configured"
Set `FIGMA_TOKEN` environment variable or run `figmaforge auth login`.

### Low similarity score
The repair loop can auto-fix generated code:
```bash
figmaforge repair --ir ir.json --layout layout.json --baseline baseline.png
```

### "source came from MCP adapter; verify recursive completeness"
The Figma data was fetched via MCP and may be incomplete. Re-ingest with
the full Figma API for complete output.
