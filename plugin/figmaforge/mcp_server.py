#!/usr/bin/env python3
"""
FigmaForge MCP Server — exposes FigmaForge pipeline commands as MCP tools.

Any MCP-compatible agent (Claude Desktop, Freebuff, OpenCode, Cursor) can
call FigmaForge directly as a tool without shell access.

Usage:
    python mcp_server.py                    # stdio transport (default)
    python mcp_server.py --port 8765        # Streamable HTTP transport

Protocol: JSON-RPC 2.0 over stdio (MCP 2025-06-18)
Dependencies: stdlib only (no external packages required)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Protocol constants
# ---------------------------------------------------------------------------

PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "figmaforge"
SERVER_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "figmaforge_spec",
        "description": (
            "Extract a semantic design spec from a Figma file JSON. "
            "Returns structured JSON with page name, sections (type, layout, content), "
            "and design tokens (colors, typography). Any LLM can understand this output."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "Path to a Figma file JSON (raw ingest output or normalized IR)",
                },
                "out": {
                    "type": "string",
                    "description": "Optional path to also write the spec JSON to disk",
                },
            },
            "required": ["file"],
        },
    },
    {
        "name": "figmaforge_generate",
        "description": (
            "Generate production code from a Figma file for a target framework. "
            "Backends: html_css, react_tailwind, vue, svelte, swiftui, flutter. "
            "Returns a manifest with generated files, fidelity losses, and metadata."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "Path to a Figma file JSON",
                },
                "backend": {
                    "type": "string",
                    "enum": ["html_css", "react_tailwind", "vue", "svelte", "swiftui", "flutter"],
                    "description": "Target framework backend",
                },
                "viewport": {
                    "type": "number",
                    "description": "Target viewport width (default: 1440)",
                },
                "out_dir": {
                    "type": "string",
                    "description": "Output directory (default: generated)",
                },
            },
            "required": ["file", "backend"],
        },
    },
    {
        "name": "figmaforge_compare",
        "description": (
            "Compare two PNG images pixel-by-pixel and return actionable feedback. "
            "Returns similarity score (0.0-1.0), verdict (identical/changed), "
            "mismatch regions with bounding boxes, and pixel statistics."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "baseline": {
                    "type": "string",
                    "description": "Path to the baseline PNG (the target look)",
                },
                "generated": {
                    "type": "string",
                    "description": "Path to the generated PNG to compare",
                },
                "out": {
                    "type": "string",
                    "description": "Optional path to also write the result JSON",
                },
            },
            "required": ["baseline", "generated"],
        },
    },
    {
        "name": "figmaforge_agent_loop",
        "description": (
            "Run the full agent pipeline in one command: extract design spec, "
            "generate code, and compare against a baseline. Returns a single "
            "JSON object with spec, generated code, and visual feedback."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "Path to a Figma file JSON or design IR JSON",
                },
                "backend": {
                    "type": "string",
                    "enum": ["html_css", "react_tailwind", "vue", "svelte", "swiftui", "flutter"],
                    "description": "Target framework backend",
                },
                "baseline": {
                    "type": "string",
                    "description": "Optional baseline PNG for visual comparison",
                },
                "viewport": {
                    "type": "number",
                    "description": "Target viewport width (default: 1440)",
                },
                "out_dir": {
                    "type": "string",
                    "description": "Output directory (default: generated)",
                },
            },
            "required": ["file", "backend"],
        },
    },
    {
        "name": "figmaforge_image_ingest",
        "description": (
            "Analyze any image (screenshot, mockup, wireframe) and produce a "
            "design IR using a vision model. Requires NVIDIA_API_KEY, "
            "ANTHROPIC_API_KEY, or OPENAI_API_KEY."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "image": {
                    "type": "string",
                    "description": "Path to the image file (PNG, JPG, etc.)",
                },
                "file_key": {
                    "type": "string",
                    "description": "Source identifier for the IR (default: image filename stem)",
                },
                "out": {
                    "type": "string",
                    "description": "Optional path to also write the IR JSON",
                },
            },
            "required": ["image"],
        },
    },
    {
        "name": "figmaforge_audit",
        "description": (
            "Audit a raw Figma file payload for completeness: missing assets, "
            "empty frames, unsupported node types, fonts. Returns a machine-readable "
            "report for pre-flight checks."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "Path to a raw Figma file JSON",
                },
            },
            "required": ["file"],
        },
    },
    {
        "name": "figmaforge_iterate",
        "description": (
            "Run the agent iteration loop: generate → render → compare → LLM fix → repeat. "
            "Uses a vision LLM to iteratively improve the generated code until it matches "
            "the baseline design within the target SSIM threshold. Returns the best output "
            "and iteration history."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "Path to a Figma file JSON",
                },
                "backend": {
                    "type": "string",
                    "description": "Backend to generate (html_css, react_tailwind, vue, svelte, swiftui, flutter)",
                },
                "baseline": {
                    "type": "string",
                    "description": "Path to baseline PNG for visual comparison",
                },
                "max_iterations": {
                    "type": "integer",
                    "description": "Maximum iterations (default 10)",
                    "default": 10,
                },
                "target_ssim": {
                    "type": "number",
                    "description": "Target SSIM score 0-1 (default 0.95)",
                    "default": 0.95,
                },
                "viewport": {
                    "type": "integer",
                    "description": "Viewport width in pixels (default 1440)",
                    "default": 1440,
                },
                "out_dir": {
                    "type": "string",
                    "description": "Output directory (default iteration_output)",
                    "default": "iteration_output",
                },
            },
            "required": ["file", "backend", "baseline"],
        },
    },
]

# ---------------------------------------------------------------------------
# Pipeline bridge
# ---------------------------------------------------------------------------

_PIPELINE = str(Path(__file__).resolve().parent / "scripts" / "pipeline.py")
_PYTHON = sys.executable


def _run_pipeline(command: str, args: List[str], timeout: int = 120) -> Dict[str, Any]:
    """Run a pipeline.py subcommand and return the parsed JSON output."""
    cmd = [_PYTHON, _PIPELINE, command] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(Path(__file__).resolve().parent.parent.parent),
        )
    except subprocess.TimeoutExpired:
        return {"error": f"Command timed out after {timeout}s", "exitCode": -1}
    except Exception as exc:
        return {"error": str(exc), "exitCode": -1}

    if result.returncode != 0:
        return {
            "error": result.stderr.strip() or f"Exit code {result.returncode}",
            "exitCode": result.returncode,
        }

    stdout = result.stdout.strip()
    if not stdout:
        return {"error": "No output from pipeline", "exitCode": 0}

    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return {"raw_output": stdout, "exitCode": 0}


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def handle_spec(params: Dict[str, Any]) -> Dict[str, Any]:
    args = ["--file", params["file"]]
    if params.get("out"):
        args += ["--out", params["out"]]
    return _run_pipeline("spec", args)


def handle_generate(params: Dict[str, Any]) -> Dict[str, Any]:
    args = ["--file", params["file"], "--backend", params["backend"]]
    if params.get("viewport"):
        args += ["--viewport", str(params["viewport"])]
    if params.get("out_dir"):
        args += ["--out-dir", params["out_dir"]]
    return _run_pipeline("generate", args)


def handle_compare(params: Dict[str, Any]) -> Dict[str, Any]:
    args = ["--baseline", params["baseline"], "--generated", params["generated"]]
    if params.get("out"):
        args += ["--out", params["out"]]
    return _run_pipeline("compare", args, timeout=60)


def handle_agent_loop(params: Dict[str, Any]) -> Dict[str, Any]:
    args = ["--file", params["file"], "--backend", params["backend"]]
    if params.get("baseline"):
        args += ["--baseline", params["baseline"]]
    if params.get("viewport"):
        args += ["--viewport", str(params["viewport"])]
    if params.get("out_dir"):
        args += ["--out-dir", params["out_dir"]]
    return _run_pipeline("agent-loop", args, timeout=120)


def handle_image_ingest(params: Dict[str, Any]) -> Dict[str, Any]:
    args = ["--image", params["image"]]
    if params.get("file_key"):
        args += ["--file-key", params["file_key"]]
    if params.get("out"):
        args += ["--out", params["out"]]
    return _run_pipeline("image_ingest", args, timeout=300)


def handle_audit(params: Dict[str, Any]) -> Dict[str, Any]:
    args = ["--file", params["file"]]
    return _run_pipeline("audit", args)


def handle_iterate(params: Dict[str, Any]) -> Dict[str, Any]:
    args = [
        "--file", params["file"],
        "--backend", params["backend"],
        "--baseline", params["baseline"],
    ]
    if params.get("max_iterations"):
        args += ["--max-iterations", str(params["max_iterations"])]
    if params.get("target_ssim"):
        args += ["--target-ssim", str(params["target_ssim"])]
    if params.get("viewport"):
        args += ["--viewport", str(params["viewport"])]
    if params.get("out_dir"):
        args += ["--out-dir", params["out_dir"]]
    # Iterate can take a long time (LLM calls + renders)
    return _run_pipeline("iterate", args, timeout=600)


HANDLERS = {
    "figmaforge_spec": handle_spec,
    "figmaforge_generate": handle_generate,
    "figmaforge_compare": handle_compare,
    "figmaforge_agent_loop": handle_agent_loop,
    "figmaforge_image_ingest": handle_image_ingest,
    "figmaforge_audit": handle_audit,
    "figmaforge_iterate": handle_iterate,
}

# ---------------------------------------------------------------------------
# JSON-RPC / MCP protocol
# ---------------------------------------------------------------------------


def _make_response(req_id: Any, result: Any) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _make_error(req_id: Any, code: int, message: str, data: Any = None) -> Dict[str, Any]:
    err: Dict[str, Any] = {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}
    if data is not None:
        err["error"]["data"] = data
    return err


def handle_message(msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Process one JSON-RPC message and return a response (or None for notifications)."""
    method = msg.get("method")
    req_id = msg.get("id")
    params = msg.get("params", {})

    # --- notifications (no response expected) ---
    if method == "notifications/initialized":
        return None
    if method == "notifications/cancelled":
        return None

    # --- initialize ---
    if method == "initialize":
        return _make_response(req_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {
                "tools": {"listChanged": False},
            },
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })

    # --- tools/list ---
    if method == "tools/list":
        return _make_response(req_id, {"tools": TOOLS})

    # --- tools/call ---
    if method == "tools/call":
        tool_name = params.get("name")
        tool_args = params.get("arguments", {})

        if tool_name not in HANDLERS:
            return _make_error(req_id, -32602, f"Unknown tool: {tool_name}")

        try:
            result = HANDLERS[tool_name](tool_args)
        except Exception as exc:
            return _make_response(req_id, {
                "content": [{"type": "text", "text": json.dumps({"error": str(exc)}, indent=2)}],
                "isError": True,
            })

        return _make_response(req_id, {
            "content": [{"type": "text", "text": json.dumps(result, indent=2, ensure_ascii=False)}],
            "isError": False,
        })

    # --- unknown method ---
    return _make_error(req_id, -32601, f"Method not found: {method}")


# ---------------------------------------------------------------------------
# stdio transport
# ---------------------------------------------------------------------------


def run_stdio() -> None:
    """Read JSON-RPC messages from stdin, write responses to stdout."""
    reader = sys.stdin
    writer = sys.stdout

    for line in reader:
        line = line.strip()
        if not line:
            continue

        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        response = handle_message(msg)
        if response is not None:
            writer.write(json.dumps(response, ensure_ascii=False) + "\n")
            writer.flush()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="FigmaForge MCP Server")
    parser.add_argument("--stdio", action="store_true", default=True, help="Use stdio transport (default)")
    args = parser.parse_args()

    if args.stdio:
        run_stdio()
    else:
        print("Only stdio transport is currently supported.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
