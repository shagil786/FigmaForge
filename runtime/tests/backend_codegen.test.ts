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
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";

import { describe, it, assert, assertEqual, assertDeepEqual, assertThrows, assertGreaterThan, assertIncludes } from "./test_framework.js";
import type { SuiteResult } from "./test_framework.js";
import { PRESET_TARGETS, targetKey } from "../src/core/types.js";
import type { RuntimeConfig } from "../src/core/types.js";
import {
  TARGET_BACKENDS,
  backendForTarget,
  UnsupportedTargetError,
  invokeBackendGenerator,
  invokeNormalize,
  invokeAssets,
  invokeRender,
  createIngestStageHandler,
  createGenerateStageHandler,
  createNormalizeStageHandler,
  createResolveStageHandler,
  createLayoutStageHandler,
  createAssetsStageHandler,
  createRenderStageHandler,
  createCompareStageHandler,
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

    await it("five-stage run produces the full front-half artifact set", async () => {
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
        pipeline.onStage("normalize", createNormalizeStageHandler());
        pipeline.onStage("resolve", createResolveStageHandler());
        pipeline.onStage("layout", createLayoutStageHandler());
        pipeline.onStage("generate", createGenerateStageHandler());

        const result = await pipeline.run();
        assertEqual(result.status, "completed");
        assertEqual(result.errors.length, 0);

        const kinds = ["figma_raw", "design_ir", "resolution_report", "layout_plan", "generated_code"] as const;
        for (const kind of kinds) {
          assertGreaterThan(artifacts.byKind(kind).length, 0, `expected ${kind} artifacts`);
        }
        const genArtifacts = artifacts.byStage("generate");
        const manifest = artifacts.loadJSON(genArtifacts[0]) as {
          manifest: { backend: string; files: Array<{ path: string }> };
        };
        assertEqual(manifest.manifest.backend, "flutter");
        assert(manifest.manifest.files.some((f) => f.path.endsWith(".dart")),
          "manifest should name the flutter file");
      } finally {
        cleanDir(dir);
      }
    });

    await it("five-stage generate manifest matches the file-mode backend invocation", async () => {
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
        pipeline.onStage("normalize", createNormalizeStageHandler());
        pipeline.onStage("resolve", createResolveStageHandler());
        pipeline.onStage("layout", createLayoutStageHandler());
        pipeline.onStage("generate", createGenerateStageHandler());
        await pipeline.run();

        const genArtifacts = artifacts.byStage("generate");
        const stored = artifacts.loadJSON(genArtifacts[0]) as {
          manifest: unknown;
        };
        const fileJson = JSON.parse(fs.readFileSync(FIXTURE, "utf-8"));
        const direct = await invokeBackendGenerator(
          { pythonBin: PYTHON_BIN, pluginDir: PLUGIN_DIR },
          { framework: "flutter", styling: "flutter_widgets" },
          fileJson,
          dir,
        );
        assertDeepEqual(stored.manifest, direct.manifest,
          "staged (five-handler) manifest must match the file-mode backend manifest");
      } finally {
        cleanDir(dir);
      }
    });

    await it("six-stage run produces the asset_manifest artifact (deterministic)", async () => {
      const runOnce = async (dir: string) => {
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
        pipeline.onStage("normalize", createNormalizeStageHandler());
        pipeline.onStage("resolve", createResolveStageHandler());
        pipeline.onStage("layout", createLayoutStageHandler());
        pipeline.onStage("assets", createAssetsStageHandler());
        pipeline.onStage("generate", createGenerateStageHandler());

        const result = await pipeline.run();
        assertEqual(result.status, "completed");
        assertEqual(result.errors.length, 0);

        const assetArtifacts = artifacts.byStage("assets");
        assertGreaterThan(assetArtifacts.length, 0, "expected assets artifacts");
        return artifacts.loadJSON(assetArtifacts[0]) as {
          assetManifest: {
            assets: unknown[];
            counts: { total: number; downloaded: number; unresolved: number };
            assets_dir: string;
          };
        };
      };

      const dir1 = tmpDir();
      const dir2 = tmpDir();
      try {
        const first = await runOnce(dir1);
        const second = await runOnce(dir2);
        // assets_dir embeds each run's absolute output dir (like local_path in
        // the generate manifest), so determinism means: identical except the
        // store location.
        const stripDir = (m: { assetManifest: { assets_dir?: string } }) => {
          const copy = structuredClone(m);
          delete copy.assetManifest.assets_dir;
          return copy;
        };
        assertDeepEqual(stripDir(first), stripDir(second),
          "asset manifests must be identical across runs (except assets_dir)");
        const manifest = first.assetManifest;
        assertDeepEqual(manifest.assets, [], "fixture IR carries no assets");
        assertEqual(manifest.counts.total, 0);
        assertEqual(manifest.counts.downloaded, 0);
        assertEqual(manifest.counts.unresolved, 0);
      } finally {
        cleanDir(dir1);
        cleanDir(dir2);
      }
    });

    await it("invokeAssets downloads and content-addresses a file:// URL", async () => {
      const dir = tmpDir();
      try {
        const png = Buffer.from("fake-asset-bytes-1234", "utf-8");
        const src = path.join(dir, "photo.png");
        fs.writeFileSync(src, png);
        const url = "file://" + src;

        const cfg = { pythonBin: PYTHON_BIN, pluginDir: PLUGIN_DIR };
        const fileJson = JSON.parse(fs.readFileSync(FIXTURE, "utf-8"));
        const ir = (await invokeNormalize(cfg, fileJson)) as Record<string, unknown> & {
          assets: Record<string, string>;
        };
        ir.assets["2:1"] = url;

        const store = path.join(dir, "store");
        const manifest = await invokeAssets(cfg, ir, store);
        assertEqual(manifest.counts.total, 1);
        assertEqual(manifest.counts.downloaded, 1);
        assertEqual(manifest.counts.unresolved, 0);
        assertEqual(manifest.assets.length, 1);

        const entry = manifest.assets[0];
        assertEqual(entry.node_id, "2:1");
        assertEqual(entry.status, "downloaded");
        assertEqual(entry.url, url);
        assertEqual(
          entry.content_hash,
          createHash("sha256").update(png).digest("hex"),
        );
        assert(!!entry.local_path && fs.existsSync(entry.local_path),
          "hashed asset file should exist on disk");
        assert(fs.readFileSync(entry.local_path!).equals(png),
          "stored bytes must match the source");
      } finally {
        cleanDir(dir);
      }
    });

    await it("assets without normalize fails with a clear stage error", async () => {
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
        pipeline.onStage("assets", createAssetsStageHandler());

        const result = await pipeline.run();
        assertEqual(result.status, "failed");
        assert(result.errors.some((e) => e.includes("no irJson")),
          `expected a 'no irJson' stage error, got: ${result.errors.join(" | ")}`);
      } finally {
        cleanDir(dir);
      }
    });

    await it("normalize without ingest fails with a clear stage error", async () => {
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
        pipeline.onStage("normalize", createNormalizeStageHandler());
        pipeline.onStage("resolve", createResolveStageHandler());
        pipeline.onStage("layout", createLayoutStageHandler());
        pipeline.onStage("generate", createGenerateStageHandler());

        const result = await pipeline.run();
        assertEqual(result.status, "failed");
        assert(result.errors.some((e) => e.includes("no fileJson")),
          `expected a 'no fileJson' stage error, got: ${result.errors.join(" | ")}`);
      } finally {
        cleanDir(dir);
      }
    });

    await it("ingest+generate only still completes (legacy fallback)", async () => {
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
        assertGreaterThan(artifacts.byKind("generated_code").length, 0);
      } finally {
        cleanDir(dir);
      }
    });

    await it("invokeRender renders a generated HTML file to a real screenshot", async () => {
      const dir = tmpDir();
      try {
        const html = path.join(dir, "screen.html");
        fs.writeFileSync(
          html,
          '<div style="width:200px;height:100px;background:#4a90d9"><p>hi</p></div>',
          "utf-8",
        );
        const result = await invokeRender(
          { pythonBin: PYTHON_BIN, pluginDir: PLUGIN_DIR },
          html,
          { width: 1440, height: 900 },
          dir,
        );
        assert(result.screenshot !== "", "expected a screenshot path");
        assert(fs.existsSync(result.screenshot),
          `screenshot file missing: ${result.screenshot}`);
        assert(fs.existsSync(result.html),
          `written html file missing: ${result.html}`);
        assert(typeof result.meta === "object", "meta should be an object");
      } finally {
        cleanDir(dir);
      }
    });

    await it("render stage captures a real screenshot of generated html_css output", async () => {
      const dir = tmpDir();
      try {
        const config = makeConfig(dir, {
          target: { framework: "html", styling: "css" },
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
        pipeline.onStage("normalize", createNormalizeStageHandler());
        pipeline.onStage("resolve", createResolveStageHandler());
        pipeline.onStage("layout", createLayoutStageHandler());
        pipeline.onStage("assets", createAssetsStageHandler());
        pipeline.onStage("generate", createGenerateStageHandler());
        pipeline.onStage("render", createRenderStageHandler());

        const result = await pipeline.run();
        assertEqual(result.status, "completed");
        assertEqual(result.errors.length, 0);

        const renderArtifacts = artifacts.byStage("render");
        assertGreaterThan(renderArtifacts.length, 0, "expected render artifacts");
        const stored = artifacts.loadJSON(renderArtifacts[0]) as {
          screenshots: Array<{
            file: string;
            html: string;
            screenshot: string;
            meta: Record<string, unknown>;
          }>;
          rendersDir: string;
        };
        assertGreaterThan(stored.screenshots.length, 0, "expected at least one screenshot row");
        assertEqual(stored.screenshots[0].file, "screen_0.html");
        assert(fs.existsSync(stored.screenshots[0].screenshot),
          `rendered screenshot missing: ${stored.screenshots[0].screenshot}`);
        assert(fs.existsSync(stored.rendersDir), "renders dir should exist");
      } finally {
        cleanDir(dir);
      }
    });

    await it("render stage degrades honestly for a non-browser target (flutter)", async () => {
      const dir = tmpDir();
      try {
        const config = makeConfig(dir); // default target: flutter
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
        pipeline.onStage("normalize", createNormalizeStageHandler());
        pipeline.onStage("resolve", createResolveStageHandler());
        pipeline.onStage("layout", createLayoutStageHandler());
        pipeline.onStage("assets", createAssetsStageHandler());
        pipeline.onStage("generate", createGenerateStageHandler());
        pipeline.onStage("render", createRenderStageHandler());

        const result = await pipeline.run();
        assertEqual(result.status, "completed");
        assertEqual(result.errors.length, 0);

        const renderArtifacts = artifacts.byStage("render");
        assertGreaterThan(renderArtifacts.length, 0, "expected a render artifact");
        const stored = artifacts.loadJSON(renderArtifacts[0]) as {
          note: string;
          screenshotPath: string | null;
          rendersDir: string;
        };
        assertEqual(stored.screenshotPath, null,
          "non-browser targets must report no screenshot, never a fabricated one");
        assert(stored.note.length > 0, "degrade must carry an explanatory note");
      } finally {
        cleanDir(dir);
      }
    });

    await it("updateMetrics seam lets a stage write metrics.similarityScore", async () => {
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
        pipeline.onStage("ingest", async (ctx) => {
          ctx.updateMetrics({ similarityScore: 0.42 });
          return {};
        });

        const result = await pipeline.run();
        assertEqual(result.status, "completed");
        const cp = checkpoints.loadLatest();
        assert(cp !== null, "expected a saved checkpoint");
        assertEqual(cp!.metrics.similarityScore, 0.42,
          "stage-written metrics must persist into the checkpoint");
      } finally {
        cleanDir(dir);
      }
    });

    await it("compare stage measures a diff_report against the reference baseline", async () => {
      const dir = tmpDir();
      try {
        const config = makeConfig(dir, {
          target: { framework: "html", styling: "css" },
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
        pipeline.onStage("normalize", createNormalizeStageHandler());
        pipeline.onStage("resolve", createResolveStageHandler());
        pipeline.onStage("layout", createLayoutStageHandler());
        pipeline.onStage("assets", createAssetsStageHandler());
        pipeline.onStage("generate", createGenerateStageHandler());
        pipeline.onStage("render", createRenderStageHandler());
        pipeline.onStage("compare", createCompareStageHandler());

        const result = await pipeline.run();
        assertEqual(result.status, "completed");
        assertEqual(result.errors.length, 0);

        const compareArtifacts = artifacts.byStage("compare");
        assertGreaterThan(compareArtifacts.length, 0, "expected a diff_report artifact");
        const report = artifacts.loadJSON(compareArtifacts[0]) as {
          similarity_score: number;
          categories: { geometry: unknown; style: unknown; pixels: number };
          raster_stats: {
            ssim: number | null;
            min_region_ssim: number | null;
            ssim_clean: boolean | null;
            diff_percentage: number;
          };
          screens: Array<{ file: string; similarity: number }>;
          baseline: string;
          baseline_kind: string;
        };
        assertEqual(report.baseline_kind, "reference");
        assert(fs.existsSync(report.baseline), "reference baseline PNG should exist");
        assert(typeof report.raster_stats.ssim_clean === "boolean",
          "SSIM verdict should be a real boolean");
        assertGreaterThan(report.similarity_score, 0.9,
          "html_css output should closely reproduce the reference render");
        assertGreaterThan(report.screens.length, 0);
        assertEqual(report.screens[0].file, "screen_0.html");
        assertEqual(report.categories.pixels, report.similarity_score);

        // The measured score must reach run metrics + the checkpoint.
        const cp = checkpoints.loadLatest();
        assert(cp !== null);
        assertEqual(
          cp!.metrics.similarityScore, report.similarity_score,
          "run metrics.similarityScore must equal the diff_report score",
        );
      } finally {
        cleanDir(dir);
      }
    });

    await it("explicit --baseline override wins and detects a real visual change", async () => {
      const dir = tmpDir();
      try {
        // A deliberately-different baseline: a full-red render at the exact
        // viewport (margin reset so the full-page capture is 1440x900, the
        // same size as the reference render the generated output matches).
        const redHtml = path.join(dir, "red.html");
        fs.writeFileSync(
          redHtml,
          '<!DOCTYPE html><html><head><style>' +
            '*{margin:0;padding:0;box-sizing:border-box}' +
            'body{width:1440px;height:900px;overflow:hidden}' +
            '</style></head><body>' +
            '<div style="width:1440px;height:900px;background:#ff0000"></div>' +
            '</body></html>',
          "utf-8",
        );
        const red = await invokeRender(
          { pythonBin: PYTHON_BIN, pluginDir: PLUGIN_DIR },
          redHtml,
          { width: 1440, height: 900 },
          dir,
        );

        const config = makeConfig(dir, {
          target: { framework: "html", styling: "css" },
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
        pipeline.setShared("baselinePath", red.screenshot);
        pipeline.onStage("ingest", createIngestStageHandler());
        pipeline.onStage("normalize", createNormalizeStageHandler());
        pipeline.onStage("resolve", createResolveStageHandler());
        pipeline.onStage("layout", createLayoutStageHandler());
        pipeline.onStage("assets", createAssetsStageHandler());
        pipeline.onStage("generate", createGenerateStageHandler());
        pipeline.onStage("render", createRenderStageHandler());
        pipeline.onStage("compare", createCompareStageHandler());

        const result = await pipeline.run();
        assertEqual(result.status, "completed");
        const compareArtifacts = artifacts.byStage("compare");
        const report = artifacts.loadJSON(compareArtifacts[0]) as {
          similarity_score: number;
          baseline_kind: string;
          raster_stats: { ssim_clean: boolean | null };
        };
        assertEqual(report.baseline_kind, "explicit");
        assert(report.similarity_score < 0.9,
          "a full-red baseline vs the fixture render must drop the score");
        assertEqual(report.raster_stats.ssim_clean, false,
          "a real visual change must fail the SSIM gate");
      } finally {
        cleanDir(dir);
      }
    });

    await it("compare stage degrades with no measured score when there is no screenshot", async () => {
      const dir = tmpDir();
      try {
        const config = makeConfig(dir); // flutter — render degrades
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
        pipeline.onStage("normalize", createNormalizeStageHandler());
        pipeline.onStage("resolve", createResolveStageHandler());
        pipeline.onStage("layout", createLayoutStageHandler());
        pipeline.onStage("assets", createAssetsStageHandler());
        pipeline.onStage("generate", createGenerateStageHandler());
        pipeline.onStage("render", createRenderStageHandler());
        pipeline.onStage("compare", createCompareStageHandler());

        const result = await pipeline.run();
        assertEqual(result.status, "completed");
        const compareArtifacts = artifacts.byStage("compare");
        assertGreaterThan(compareArtifacts.length, 0);
        const report = artifacts.loadJSON(compareArtifacts[0]) as {
          similarity_score: number | null;
          note: string;
        };
        assertEqual(report.similarity_score, null,
          "no screenshot → no measured score, never a fabricated one");
        assertGreaterThan(report.note.length, 0);
        const cp = checkpoints.loadLatest();
        assert(cp !== null);
        assertEqual(cp!.metrics.similarityScore, 0,
          "metrics must stay untouched when no score is measured");
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
