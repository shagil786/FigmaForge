/**
 * Adaptive preflight bridge tests.
 *
 * Exercises the TypeScript bridge against temporary Python fixtures so the
 * tests cover argument forwarding, JSON parsing, malformed output, and
 * subprocess failures without depending on the real repository detector.
 */

import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { pathToFileURL } from "node:url";

import {
  describe,
  it,
  assert,
  assertEqual,
  assertDeepEqual,
  printResults,
} from "./test_framework.js";
import type { SuiteResult } from "./test_framework.js";
import {
  AdaptivePreflightError,
  invokeAdaptivePreflight,
} from "../src/core/adaptive_preflight.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function tmpDir(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), "ff-adaptive-"));
}

function cleanDir(dir: string): void {
  if (fs.existsSync(dir)) {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

function makePluginDir(): string {
  const dir = tmpDir();
  fs.mkdirSync(path.join(dir, "scripts"), { recursive: true });
  return dir;
}

function writeScript(pluginDir: string, body: string): string {
  const scriptPath = path.join(pluginDir, "scripts", "adaptive_plan.py");
  fs.writeFileSync(scriptPath, body, "utf-8");
  return scriptPath;
}

function baseScript(lines: string[]): string {
  return [
    "#!/usr/bin/env python3",
    "from __future__ import annotations",
    "import json",
    "import sys",
    ...lines,
    "",
  ].join("\n");
}

function makeConfig(pluginDir: string): { pythonBin: string; pluginDir: string } {
  return {
    pythonBin: "python3",
    pluginDir,
  };
}

async function captureAdaptivePreflightError(
  cfg: { pythonBin: string; pluginDir: string },
  root: string,
  request: string,
  installedCapabilities: string[] = [],
): Promise<AdaptivePreflightError> {
  try {
    await invokeAdaptivePreflight(cfg, root, request, installedCapabilities);
  } catch (err) {
    if (!(err instanceof AdaptivePreflightError)) {
      throw err;
    }
    return err;
  }
  throw new Error("expected invokeAdaptivePreflight to reject");
}

// ---------------------------------------------------------------------------
// Suite
// ---------------------------------------------------------------------------

export async function runAdaptivePreflightTests(): Promise<SuiteResult[]> {
  return [await describe("adaptive preflight bridge", async () => {
    await it("parses a valid adaptive plan and forwards capabilities", async () => {
      const pluginDir = makePluginDir();
      const root = "/workspace/root";
      const script = baseScript([
        "print('preflight started')",
        "caps = [sys.argv[i + 1] for i, arg in enumerate(sys.argv) if arg == '--installed-capability']",
        "payload = {",
        "    'schema_version': 1,",
        "    'request': sys.argv[sys.argv.index('--request') + 1],",
        "    'root': sys.argv[sys.argv.index('--root') + 1],",
        "    'detection': {'status': 'classified', 'confidence': 0.8},",
        "    'route': {",
        "        'phases': ['inspect', 'classify'],",
        "        'roles': [{'name': 'router', 'capability_refs': caps}],",
        "        'external_skills': ['product-skills:architecture'],",
        "        'execution_mode': 'direct',",
        "        'stack_status': 'classified',",
        "        'approval_gates': [],",
        "        'unloaded_modules': [],",
        "    },",
        "}",
        "print(json.dumps(payload))",
      ]);
      try {
        writeScript(pluginDir, script);
        const plan = await invokeAdaptivePreflight(
          makeConfig(pluginDir),
          root,
          "Inspect",
          ["cap.alpha", "cap.beta"],
        );

        assertEqual(plan.schema_version, 1);
        assertEqual(plan.request, "Inspect");
        assertEqual(plan.root, root);
        assertEqual(plan.route.stack_status, "classified");
        assertEqual(plan.route.execution_mode, "direct");
        assertEqual(plan.route.roles.length, 1);
        assertEqual(plan.route.roles[0].name, "router");
        assertDeepEqual(plan.route.roles[0].capability_refs, ["cap.alpha", "cap.beta"]);
      } finally {
        cleanDir(pluginDir);
      }
    });

    await it("rejects an unsupported execution policy explicitly", async () => {
      const pluginDir = makePluginDir();
      const script = baseScript([
        "print(json.dumps({",
        "    'schema_version': 1,",
        "    'request': 'Inspect',",
        "    'root': '/workspace/root',",
        "    'detection': {},",
        "    'route': {",
        "        'phases': [],",
        "        'roles': [],",
        "        'external_skills': [],",
        "        'execution_mode': 'magical_autopilot',",
        "        'stack_status': 'classified',",
        "        'approval_gates': [],",
        "        'unloaded_modules': [],",
        "    },",
        "}))",
      ]);
      try {
        writeScript(pluginDir, script);
        const err = await captureAdaptivePreflightError(
          makeConfig(pluginDir), "/workspace/root", "Inspect", [],
        );
        assertEqual(err.name, "AdaptivePreflightError");
        assert(err.message.includes("unsupported execution_mode"));
      } finally {
        cleanDir(pluginDir);
      }
    });

    await it("preserves stderr and stdout when adaptive plan JSON is malformed", async () => {
      const pluginDir = makePluginDir();
      const script = baseScript([
        "print('malformed output', file=sys.stderr)",
        "print('not json')",
      ]);
      try {
        writeScript(pluginDir, script);
        const err = await captureAdaptivePreflightError(
          makeConfig(pluginDir),
          "/workspace/root",
          "Inspect",
          [],
        );
        assertEqual(err.name, "AdaptivePreflightError");
        assertEqual(err.stderr.trim(), "malformed output");
        assertEqual(err.stdout.includes("not json"), true);
        assertEqual(err.message.includes("malformed output"), false);
      } finally {
        cleanDir(pluginDir);
      }
    });

    await it("preserves stderr and stdout when adaptive plan output is invalid", async () => {
      const pluginDir = makePluginDir();
      const script = baseScript([
        "print('invalid payload', file=sys.stderr)",
        "print(json.dumps({",
        "    'schema_version': 1,",
        "    'request': 'Inspect',",
        "    'root': '/workspace/root',",
        "    'detection': {},",
        "    'route': {",
        "        'phases': [],",
        "        'roles': [],",
        "        'external_skills': [],",
        "        'execution_mode': 'direct',",
        "        'approval_gates': [],",
        "        'unloaded_modules': [],",
        "    },",
        "}))",
      ]);
      try {
        writeScript(pluginDir, script);
        const err = await captureAdaptivePreflightError(
          makeConfig(pluginDir),
          "/workspace/root",
          "Inspect",
          [],
        );
        assertEqual(err.name, "AdaptivePreflightError");
        assertEqual(err.stderr.trim(), "invalid payload");
        assertEqual(err.stdout.includes("\"route\""), true);
        assertEqual(err.message.includes("invalid payload"), false);
      } finally {
        cleanDir(pluginDir);
      }
    });

    await it("wraps spawn launch failures as AdaptivePreflightError", async () => {
      const pluginDir = makePluginDir();
      const root = "/workspace/root";
      try {
        const err = await captureAdaptivePreflightError(
          {
            pythonBin: "definitely-not-a-python-binary",
            pluginDir,
          },
          root,
          "Inspect",
          [],
        );
        assertEqual(err.name, "AdaptivePreflightError");
        assertEqual(err.stderr, "");
        assertEqual(err.stdout, "");
        assert(err.message.includes("failed to launch"), "launch failure should be wrapped");
      } finally {
        cleanDir(pluginDir);
      }
    });
  })];
}

async function main(): Promise<void> {
  const results = await runAdaptivePreflightTests();
  printResults(results);

  const totalFailed = results.reduce((sum, suite) => sum + suite.failed, 0);
  if (totalFailed > 0) {
    process.exit(1);
  }
}

if (process.argv[1] !== undefined && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((err) => {
    console.error("Fatal error:", err);
    process.exit(1);
  });
}
