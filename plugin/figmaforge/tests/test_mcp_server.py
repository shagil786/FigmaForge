#!/usr/bin/env python3
"""
Tests for the FigmaForge MCP server (mcp_server.py).

Exercises the JSON-RPC protocol handling, tool discovery, tool execution,
and error handling — all without requiring an MCP client library.

Run:  python3 -m unittest tests.test_mcp_server -v
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

plugin_root = Path(__file__).resolve().parent.parent
if str(plugin_root) not in sys.path:
    sys.path.insert(0, str(plugin_root))

from mcp_server import (
    TOOLS,
    handle_message,
    handle_spec,
    handle_generate,
    handle_compare,
    handle_agent_loop,
    handle_audit,
    PROTOCOL_VERSION,
    SERVER_NAME,
)

FIXTURE = plugin_root / "fixtures" / "figma" / "rich_landing.json"


class TestProtocol(unittest.TestCase):
    """MCP protocol handshake and message routing."""

    def test_initialize_returns_protocol_version(self):
        resp = handle_message({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertIsNotNone(resp)
        self.assertEqual(resp["result"]["protocolVersion"], PROTOCOL_VERSION)
        self.assertEqual(resp["result"]["serverInfo"]["name"], SERVER_NAME)
        self.assertIn("tools", resp["result"]["capabilities"])

    def test_tools_list_returns_all_tools(self):
        resp = handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        self.assertIsNotNone(resp)
        names = [t["name"] for t in resp["result"]["tools"]]
        self.assertIn("figmaforge_spec", names)
        self.assertIn("figmaforge_generate", names)
        self.assertIn("figmaforge_compare", names)
        self.assertIn("figmaforge_agent_loop", names)
        self.assertIn("figmaforge_image_ingest", names)
        self.assertIn("figmaforge_audit", names)
        self.assertEqual(len(resp["result"]["tools"]), len(TOOLS))

    def test_tool_schemas_have_required_fields(self):
        for tool in TOOLS:
            self.assertIn("name", tool)
            self.assertIn("description", tool)
            self.assertIn("inputSchema", tool)
            schema = tool["inputSchema"]
            self.assertEqual(schema["type"], "object")
            self.assertIn("properties", schema)
            self.assertIn("required", schema)

    def test_unknown_method_returns_error(self):
        resp = handle_message({"jsonrpc": "2.0", "id": 3, "method": "unknown/method", "params": {}})
        self.assertIsNotNone(resp)
        self.assertIn("error", resp)
        self.assertEqual(resp["error"]["code"], -32601)

    def test_notifications_return_none(self):
        resp = handle_message({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        self.assertIsNone(resp)

    def test_unknown_tool_returns_error(self):
        resp = handle_message({
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "nonexistent_tool", "arguments": {}},
        })
        self.assertIsNotNone(resp)
        self.assertIn("error", resp)
        self.assertEqual(resp["error"]["code"], -32602)


class TestSpecTool(unittest.TestCase):
    """figmaforge_spec tool execution."""

    def test_spec_returns_sections(self):
        result = handle_spec({"file": str(FIXTURE)})
        self.assertIn("sections", result)
        self.assertIn("design_tokens", result)
        self.assertGreater(len(result["sections"]), 0)

    def test_spec_sections_have_types(self):
        result = handle_spec({"file": str(FIXTURE)})
        types = [s["type"] for s in result["sections"]]
        self.assertIn("navigation", types)
        self.assertIn("hero", types)

    def test_spec_missing_file_returns_error(self):
        result = handle_spec({"file": "/nonexistent/file.json"})
        self.assertIn("error", result)

    def test_spec_output_matches_direct_command(self):
        """MCP tool output must match the CLI command output."""
        import subprocess
        proc = subprocess.run(
            [sys.executable, str(plugin_root / "scripts" / "pipeline.py"),
             "spec", "--file", str(FIXTURE)],
            capture_output=True, text=True, timeout=30,
            cwd=str(plugin_root),
        )
        cli_spec = json.loads(proc.stdout.strip())
        mcp_spec = handle_spec({"file": str(FIXTURE)})
        self.assertEqual(mcp_spec, cli_spec)


class TestGenerateTool(unittest.TestCase):
    """figmaforge_generate tool execution."""

    def test_generate_returns_manifest(self):
        result = handle_generate({"file": str(FIXTURE), "backend": "html_css"})
        self.assertIn("backend", result)
        self.assertEqual(result["backend"], "html_css")
        self.assertIn("files", result)
        self.assertGreater(len(result["files"]), 0)

    def test_generate_unknown_backend_returns_error(self):
        result = handle_generate({"file": str(FIXTURE), "backend": "nonexistent"})
        self.assertIn("error", result)

    def test_generate_all_backends(self):
        for backend in ("html_css", "react_tailwind", "vue", "svelte", "swiftui", "flutter"):
            result = handle_generate({"file": str(FIXTURE), "backend": backend})
            self.assertNotIn("error", result, f"backend {backend} failed: {result}")
            self.assertEqual(result["backend"], backend)


class TestCompareTool(unittest.TestCase):
    """figmaforge_compare tool execution."""

    def _make_png(self, path: Path, r: int, g: int, b: int, size: int = 10):
        from core.png_codec import PngImage, encode_png
        pixels = bytes([r, g, b]) * (size * size)
        path.write_bytes(encode_png(PngImage(width=size, height=size, channels=3, pixels=pixels)))

    def test_identical_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.png"
            b = Path(tmp) / "b.png"
            self._make_png(a, 255, 0, 0)
            self._make_png(b, 255, 0, 0)
            result = handle_compare({"baseline": str(a), "generated": str(b)})
            self.assertAlmostEqual(result["similarity_score"], 1.0, places=4)
            self.assertEqual(result["verdict"], "identical")

    def test_different_images(self):
        from core.png_codec import PngImage, encode_png
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.png"
            b = Path(tmp) / "b.png"
            a_pixels = bytes([255, 0, 0]) * 50 + bytes([255, 0, 0]) * 50
            a.write_bytes(encode_png(PngImage(width=10, height=10, channels=3, pixels=a_pixels)))
            b_pixels = bytes([255, 0, 0]) * 50 + bytes([0, 0, 0]) * 50
            b.write_bytes(encode_png(PngImage(width=10, height=10, channels=3, pixels=b_pixels)))
            result = handle_compare({"baseline": str(a), "generated": str(b)})
            self.assertLess(result["similarity_score"], 1.0)
            self.assertEqual(result["verdict"], "changed")
            self.assertGreater(len(result["mismatches"]), 0)

    def test_missing_file_returns_error(self):
        result = handle_compare({"baseline": "/nonexistent/a.png", "generated": "/nonexistent/b.png"})
        self.assertIn("error", result)


class TestAgentLoopTool(unittest.TestCase):
    """figmaforge_agent_loop tool execution."""

    def test_agent_loop_structure(self):
        result = handle_agent_loop({"file": str(FIXTURE), "backend": "html_css"})
        self.assertIn("spec", result)
        self.assertIn("generated", result)
        self.assertIn("feedback", result)
        self.assertIn("sections", result["spec"])
        self.assertEqual(result["generated"]["backend"], "html_css")

    def test_agent_loop_spec_matches_spec_tool(self):
        spec_result = handle_spec({"file": str(FIXTURE)})
        loop_result = handle_agent_loop({"file": str(FIXTURE), "backend": "html_css"})
        self.assertEqual(loop_result["spec"], spec_result)

    def test_agent_loop_no_baseline(self):
        result = handle_agent_loop({"file": str(FIXTURE), "backend": "html_css"})
        self.assertEqual(result["feedback"]["verdict"], "no_baseline")

    def test_agent_loop_bad_backend(self):
        result = handle_agent_loop({"file": str(FIXTURE), "backend": "nonexistent"})
        self.assertIn("error", result)


class TestAuditTool(unittest.TestCase):
    """figmaforge_audit tool execution."""

    def test_audit_returns_report(self):
        result = handle_audit({"file": str(FIXTURE)})
        self.assertIn("node_types", result)
        self.assertIn("empty_structural_nodes", result)
        self.assertIn("ready_for_generation", result)

    def test_audit_missing_file(self):
        result = handle_audit({"file": "/nonexistent/file.json"})
        self.assertIn("error", result)


class TestToolsCallRouting(unittest.TestCase):
    """End-to-end routing through handle_message."""

    def test_tools_call_spec(self):
        resp = handle_message({
            "jsonrpc": "2.0", "id": 10, "method": "tools/call",
            "params": {"name": "figmaforge_spec", "arguments": {"file": str(FIXTURE)}},
        })
        self.assertIsNotNone(resp)
        self.assertNotIn("error", resp)
        content = resp["result"]["content"][0]["text"]
        result = json.loads(content)
        self.assertIn("sections", result)

    def test_tools_call_error_returns_is_error(self):
        resp = handle_message({
            "jsonrpc": "2.0", "id": 11, "method": "tools/call",
            "params": {"name": "figmaforge_spec", "arguments": {"file": "/nonexistent"}},
        })
        self.assertIsNotNone(resp)
        content = resp["result"]["content"][0]["text"]
        result = json.loads(content)
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
