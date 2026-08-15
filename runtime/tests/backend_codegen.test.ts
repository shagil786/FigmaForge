/**
 * Backend code generation tests (Part 15, Task 2).
 *
 * Covers the target→backend map, the typed rejection of targets with no
 * Python backend, and the real ingest + generate stage handlers wired into
 * the pipeline coordinator against the checked-in fixture file.
 */

import * as fs from "node:fs";
import * as path from "node:path";
import * as os from "node:os";
import { spawnSync } from "node:child_process";

import { describe, it, assert, assertEqual, assertThrows, assertGreaterThan, assertIncludes } from "./test_framework.js";
import type { SuiteResult } from "./test_framework.js";
import { PRESET_TARGETS, targetKey } from "../src/core/types.js";
import type { RuntimeConfig } from "../src/core/types.js";
import {
  TARGET_BACKENDS,
  backendForTarget,
  UnsupportedTargetError,
  invokeBackendGenerator,
  createIngestStageHandler,
  createGenerateStageHandler,
} from "../src/core/backend_codegen.js";
import { EventLog } from "../src/core/events.js";
import { CheckpointManager } from "../src/core/checkpoint.js";
import { ArtifactStore } from "../src/core/artifacts.js";
import { ToolRegistry } from "../src/core/tools.js";
import { BudgetTracker } from "../src/core/budget.js";
import { PipelineCoordinator } from "../src/core/pipeline.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const REAL_BACKENDS = ["html_css", "react_tailwind", "vue", "svelte", "swiftui", "flutter"];

const PLUGIN_DIR = path.resolve("plugin/figmaforge");
const FIXTURE = path.join(PLUGIN_DIR, "fixtures", "figma", "layout_desktop.json");
const PYTHON_BIN = process.env.PYTHON_BIN ?? "python3";

function tmpDir(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), "ff-codegen-"));
}

function cleanDir(dir: string): void {
  if (fs.existsSync(dir)) {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

function makeConfig(dir: string, overrides: Partial<RuntimeConfig> = {}): RuntimeConfig {
  return {
    runId: "run-codegen",
    fileKey: "",
    outputDir: dir,
    approvedDirs: [dir],
    requireApproval: false,
    retry: { maxAttempts: 1, baseDelayMs: 10, maxDelayMs: 100, backoffMultiplier: 2 },
    budgets: { maxTokens: 10000, maxTimeMs: 60000, maxIterations: 100, maxRepairIterations: 10 },
    similarityThreshold: 0.95,
    minProgress: 0.005,
    viewport: { width: 1440, height: 900 },
    pythonBin: PYTHON_BIN,
    pluginDir: PLUGIN_DIR,
    target: { framework: "flutter", styling: "flutter_widgets" },
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Suite
// ---------------------------------------------------------------------------

export async function runBackendCodegenTests(): Promise<SuiteResult[]> {
  const results: SuiteResult[] = [];

  results.push(await describe("backend codegen", async () => {
    await it("target map covers every preset with a real Python backend", async () => {
      const real = new Set(REAL_BACKENDS);

      // Every mapped value is a real backend name.
      for (const backend of Object.values(TARGET_BACKENDS)) {
        assert(real.has(backend), `TARGET_BACKENDS maps to unknown backend: ${backend}`);
      }

      // Every preset either maps to its backend or is honestly unmapped
      // (react+css / react+styled_components have no Python adapter).
      for (const preset of PRESET_TARGETS) {
        const key = targetKey(preset);
        if (key in TARGET_BACKENDS) {
          assertEqual(backendForTarget(preset), TARGET_BACKENDS[key], `mapping for ${key}`);
        } else {
          assertThrows(() => backendForTarget(preset), "no Python backend");
        }
      }

      // All six backends are reachable from the presets.
      const reachable = new Set(
        PRESET_TARGETS
          .map((t) => targetKey(t))
          .filter((k) => k in TARGET_BACKENDS)
          .map((k) => TARGET_BACKENDS[k]),
      );
      for (const backend of REAL_BACKENDS) {
        assert(reachable.has(backend), `backend ${backend} not reachable from presets`);
      }
    });

    await it("unknown target is rejected with a typed error", async () => {
      let caught: unknown = null;
      try {
        backendForTarget("vue+css");
      } catch (err) {
        caught = err;
      }
      assert(caught instanceof UnsupportedTargetError, "expected UnsupportedTargetError");
      assert((caught as Error).message.includes("no Python backend"));

      assertThrows(
        () => backendForTarget({ framework: "react", styling: "styled_components" }),
        "no Python backend",
      );
    });

    await it("invokeBackendGenerator produces a manifest + files on disk", async () => {
      const dir = tmpDir();
      try {
        const fileJson = JSON.parse(fs.readFileSync(FIXTURE, "utf-8"));
        const result = await invokeBackendGenerator(
          { pythonBin: PYTHON_BIN, pluginDir: PLUGIN_DIR },
          { framework: "flutter", styling: "flutter_widgets" },
          fileJson,
          dir,
        );
        assertEqual(result.manifest.backend, "flutter");
        assertGreaterThan(result.manifest.files.length, 0);
        assert(result.manifest.files.some((f) => f.path.endsWith(".dart")),
          "manifest should name a dart file");
        assert(result.manifest.fidelity_losses.length > 0,
          "manifest should carry fidelity losses");
        const written = fs.readdirSync(result.filesDir);
        for (const f of result.manifest.files) {
          assert(written.includes(f.path), `file ${f.path} missing from ${result.filesDir}`);
        }
      } finally {
        cleanDir(dir);
      }
    });

    await it("generate stage produces generated_code artifacts from a local fixture", async () => {
      const dir = tmpDir();
      try {
        const config = makeConfig(dir);
        const events = new EventLog(config.runId);
        const checkpoints = new CheckpointManager(config.runId, config.outputDir);
        const artifacts = new ArtifactStore(config.runId, config.outputDir);
        const tools = new ToolRegistry();
        const budget = new BudgetTracker(config.budgets);

        const pipeline = new PipelineCoordinator(
          config, events, checkpoints, artifacts, tools, budget,
        );
        pipeline.setShared("filePath", FIXTURE);
        pipeline.onStage("ingest", createIngestStageHandler());
        pipeline.onStage("generate", createGenerateStageHandler());

        const result = await pipeline.run();
        assertEqual(result.status, "completed");
        assertEqual(result.errors.length, 0);

        const genArtifacts = artifacts.byStage("generate");
        assertGreaterThan(genArtifacts.length, 0, "expected generated_code artifacts");
        const manifest = artifacts.loadJSON(genArtifacts[0]) as {
          manifest: {
            backend: string;
            files: Array<{ path: string }>;
            fidelity_losses: unknown[];
          };
        };
        assertEqual(manifest.manifest.backend, "flutter");
        assertGreaterThan(manifest.manifest.files.length, 0);
        assert(manifest.manifest.files.some((f) => f.path.endsWith(".dart")),
          "manifest should name the flutter file");
        assertGreaterThan(manifest.manifest.fidelity_losses.length, 0,
          "fidelity losses should be present in the manifest");
      } finally {
        cleanDir(dir);
      }
    });

    await it("demo command generates all six backends from a local fixture", async () => {
      const dir = tmpDir();
      try {
        const cli = path.resolve("dist/runtime/src/cli/main.js");
        const res = spawnSync(process.execPath, [cli, "demo", `--file=${FIXTURE}`, `--out=${dir}`], {
          cwd: path.resolve("."),
          env: { ...process.env, PYTHON_BIN },
          encoding: "utf-8",
          timeout: 120_000,
        });
        assertEqual(res.status, 0, `demo exited ${res.status}: ${res.stderr ?? ""}`);

        // One output directory per backend, each with generated files.
        for (const backend of REAL_BACKENDS) {
          const backendDir = path.join(dir, backend);
          assert(fs.existsSync(backendDir), `missing demo output dir for ${backend}`);
          assertGreaterThan(fs.readdirSync(backendDir).length, 0,
            `no files generated for ${backend}`);
        }

        // Summary table with per-backend file and loss counts.
        const stdout = res.stdout ?? "";
        assertIncludes(stdout, "files", "table should have a files column");
        assertIncludes(stdout, "losses", "table should have a losses column");
        for (const backend of REAL_BACKENDS) {
          assertIncludes(stdout, backend, `table should list ${backend}`);
        }
      } finally {
        cleanDir(dir);
      }
    });

    await it("demo falls back to the offline fixture without a token or file", async () => {
      const dir = tmpDir();
      try {
        const cli = path.resolve("dist/runtime/src/cli/main.js");
        const env: Record<string, string> = { ...process.env, PYTHON_BIN };
        delete env.FIGMA_TOKEN;
        const res = spawnSync(process.execPath, [cli, "demo", `--out=${dir}`], {
          cwd: path.resolve("."),
          env,
          encoding: "utf-8",
          timeout: 120_000,
        });
        assertEqual(res.status, 0, `demo exited ${res.status}: ${res.stderr ?? ""}`);
        assertIncludes(res.stdout ?? "", "offline fixture",
          "demo should announce the offline fixture path");
        assert(fs.existsSync(path.join(dir, "html_css")),
          "offline demo should generate html_css");
      } finally {
        cleanDir(dir);
      }
    });

    await it("a target with no Python backend fails the generate stage", async () => {
      const dir = tmpDir();
      try {
        const config = makeConfig(dir, {
          target: { framework: "react", styling: "css" },
        });
        const events = new EventLog(config.runId);
        const checkpoints = new CheckpointManager(config.runId, config.outputDir);
        const artifacts = new ArtifactStore(config.runId, config.outputDir);
        const tools = new ToolRegistry();
        const budget = new BudgetTracker(config.budgets);

        const pipeline = new PipelineCoordinator(
          config, events, checkpoints, artifacts, tools, budget,
        );
        pipeline.setShared("filePath", FIXTURE);
        pipeline.onStage("ingest", createIngestStageHandler());
        pipeline.onStage("generate", createGenerateStageHandler());

        const result = await pipeline.run();
        assertEqual(result.status, "failed");
        assertGreaterThan(result.errors.length, 0);
        assert(result.errors.some((e) => e.includes("no Python backend")),
          `expected a 'no Python backend' stage error, got: ${result.errors.join(" | ")}`);
      } finally {
        cleanDir(dir);
      }
    });
  }));

  return results;
}
