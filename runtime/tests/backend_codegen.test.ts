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

import { describe, it, assert, assertEqual, assertDeepEqual, assertThrows, assertRejects, assertGreaterThan, assertIncludes } from "./test_framework.js";
import type { SuiteResult } from "./test_framework.js";
import { PRESET_TARGETS, targetKey } from "../src/core/types.js";
import type { RuntimeConfig } from "../src/core/types.js";
import {
  TARGET_BACKENDS,
  backendForTarget,
  UnsupportedTargetError,
  invokeBackendGenerator,
  invokeBackendGeneratorFromStages,
  invokeNormalize,
  invokeResolve,
  invokeLayout,
  invokeAssets,
  invokeRender,
  invokeBundleRender,
  invokeRepair,
  createIngestStageHandler,
  createGenerateStageHandler,
  createNormalizeStageHandler,
  createResolveStageHandler,
  createLayoutStageHandler,
  createAssetsStageHandler,
  createRenderStageHandler,
  createCompareStageHandler,
  createRepairStageHandler,
  createVerifyStageHandler,
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

/** Inject an image fill into the first IR node with an id; return it + id. */
function injectImageFill(irJson: Record<string, unknown>): {
  ir: Record<string, unknown>;
  nodeId: string;
} {
  const STRUCTURAL = new Set(["DOCUMENT", "CANVAS", "PAGE"]);
  const pick = (node: Record<string, any> | undefined): Record<string, any> | null => {
    if (node && node.id && !STRUCTURAL.has(node.node_type)) return node;
    for (const child of node?.children ?? []) {
      const hit = pick(child);
      if (hit) return hit;
    }
    return null;
  };
  const target = pick((irJson as Record<string, any>).root);
  if (!target) throw new Error("no node to inject an image fill");
  target.style = target.style ?? {};
  target.style.fills = [{
    kind: "image", image_ref: "img:1", visible: true, opacity: 1.0,
  }];
  return { ir: irJson, nodeId: target.id };
}

/** A Part-17-shaped asset manifest with one downloaded entry. */
function buildAssetsManifest(nodeId: string, localPath: string): Record<string, unknown> {
  return {
    schema_version: 1,
    file_key: "layout_desktop",
    assets: [{
      node_id: nodeId, url: "file:///tmp/photo.png", image_ref: "img:1",
      kind: "image", status: "downloaded", content_hash: "abc123",
      local_path: localPath,
    }],
    counts: { total: 1, downloaded: 1, unresolved: 0 },
    assets_dir: "/tmp/assets",
  };
}

/**
 * A minimal PNG-shaped buffer (signature + IHDR width/height).  Enough for
 * the comparator's identical-content fast path (which only reads the
 * signature + dimensions); a real pixel-diff against it fails honestly.
 */
function fakePngBytes(width: number, height: number): Buffer {
  const buf = Buffer.alloc(24);
  Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]).copy(buf, 0);
  buf.writeUInt32BE(13, 8);   // IHDR length
  buf.write("IHDR", 12);      // chunk type
  buf.writeUInt32BE(width, 16);
  buf.writeUInt32BE(height, 20);
  return buf;
}

/** The red external-baseline HTML used by the repair/verify tests. */
function redBaselineHtml(): string {
  return "<!DOCTYPE html><html><head><style>" +
    "*{margin:0;padding:0;box-sizing:border-box}" +
    "body{width:1440px;height:900px;overflow:hidden}" +
    "</style></head><body>" +
    '<div style="width:1440px;height:900px;background:#ff0000"></div>' +
    "</body></html>";
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

    await it("staged generate with an asset manifest emits real image references", async () => {
      const dir = tmpDir();
      try {
        const cfg = { pythonBin: PYTHON_BIN, pluginDir: PLUGIN_DIR };
        const fileJson = JSON.parse(fs.readFileSync(FIXTURE, "utf-8"));
        const { ir, nodeId } = injectImageFill(await invokeNormalize(cfg, fileJson));
        const layoutJson = await invokeLayout(cfg, ir, 1440);

        const result = await invokeBackendGeneratorFromStages(
          cfg,
          { framework: "html", styling: "css" },
          { irJson: ir, layoutJson, assetsManifest: buildAssetsManifest(nodeId, "assets/photo.png") },
          dir,
        );
        const html = fs.readdirSync(result.filesDir)
          .map((f) => fs.readFileSync(path.join(result.filesDir, f), "utf-8"))
          .join("\n");
        assertIncludes(html, "background-image: url(assets/photo.png)",
          "a resolved asset manifest should emit the real background url");
        assert(!html.includes("fills_image approximated"),
          "a resolved image must not carry the fidelity marker");
      } finally {
        cleanDir(dir);
      }
    });

    await it("the generate stage threads the asset manifest from the assets stage", async () => {
      const dir = tmpDir();
      try {
        const cfg = { pythonBin: PYTHON_BIN, pluginDir: PLUGIN_DIR };
        const fileJson = JSON.parse(fs.readFileSync(FIXTURE, "utf-8"));
        const { ir, nodeId } = injectImageFill(await invokeNormalize(cfg, fileJson));
        const png = Buffer.from("photo-bytes", "utf-8");
        const src = path.join(dir, "photo.png");
        fs.writeFileSync(src, png);
        (ir as Record<string, any>).assets = {
          ...((ir as Record<string, any>).assets ?? {}),
          [nodeId]: "file://" + src,
        };

        const config = makeConfig(dir, { target: { framework: "html", styling: "css" } });
        const events = new EventLog(config.runId);
        const checkpoints = new CheckpointManager(config.runId, config.outputDir);
        const artifacts = new ArtifactStore(config.runId, config.outputDir);
        const tools = new ToolRegistry();
        const budget = new BudgetTracker(config.budgets);
        const pipeline = new PipelineCoordinator(
          config, events, checkpoints, artifacts, tools, budget,
        );
        // Trivial upstream stubs (ingest reads the fixture verbatim; a real
        // normalize would rebuild the IR without our injected image fill). The
        // assets + generate stages below are the REAL Part 17/18 handlers.
        pipeline.onStage("ingest", async (ctx) => {
          ctx.shared.set("fileJson", fileJson);
          return { fileJson };
        });
        pipeline.onStage("normalize", async (ctx) => {
          ctx.shared.set("irJson", ir);
          return { irJson: ir };
        });
        pipeline.onStage("layout", createLayoutStageHandler());
        pipeline.onStage("assets", createAssetsStageHandler());
        pipeline.onStage("generate", createGenerateStageHandler());

        const result = await pipeline.run();
        assertEqual(result.status, "completed", result.errors.join(" | "));
        assertEqual(result.errors.length, 0);

        const filesDir = path.join(dir, config.runId, "generated", "html_css");
        const html = fs.readdirSync(filesDir)
          .map((f) => fs.readFileSync(path.join(filesDir, f), "utf-8"))
          .join("\n");
        assertIncludes(html, "background-image: url(",
          "the generate stage should thread the assets-stage manifest");
        assert(!html.includes("fills_image approximated"));
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

    await it("render stage bundles a bundler-backed target through invokeBundleRender", async () => {
      const dir = tmpDir();
      try {
        const calls: Array<{
          backend: string;
          generatedDir: string;
          assetManifest: unknown;
          viewport: { width: number; height: number };
          outDir: string;
        }> = [];
        const fakeBundle = async (
          _cfg: { pythonBin: string; pluginDir: string },
          backend: string,
          generatedDir: string,
          assetManifest: unknown,
          viewport: { width: number; height: number },
          outDir: string,
        ) => {
          calls.push({ backend, generatedDir, assetManifest, viewport, outDir });
          const png = path.join(outDir, "screens", "Root.png");
          fs.mkdirSync(path.dirname(png), { recursive: true });
          fs.writeFileSync(png, Buffer.from("fake-png"));
          return {
            ok: true as const,
            kind: "bundle" as const,
            backend,
            screens: [{
              component: "Root", png: "screens/Root.png", html: "Root.html",
            }],
            build_ok: true as const,
            viewport,
          };
        };

        const config = makeConfig(dir, {
          target: { framework: "react", styling: "tailwind" },
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
        pipeline.onStage("render", createRenderStageHandler({ bundleInvoker: fakeBundle }));

        const result = await pipeline.run();
        assertEqual(result.status, "completed");
        assertEqual(result.errors.length, 0);

        // The bundle path was taken, with the generated dir + viewport.
        assertEqual(calls.length, 1, "expected exactly one bundle invocation");
        assertEqual(calls[0].backend, "react_tailwind");
        assert(
          calls[0].generatedDir.endsWith(path.join("generated", "react_tailwind")),
          `unexpected generated dir: ${calls[0].generatedDir}`,
        );
        assertEqual(calls[0].viewport.width, 1440);
        assertEqual(calls[0].viewport.height, 900);
        assert(
          typeof calls[0].assetManifest === "object"
            && calls[0].assetManifest !== null,
          "asset manifest should be shared into the bundle invocation",
        );

        // renderOutputs shared with real rows pointing at the screenshots.
        const shared = pipeline.getShared("renderOutputs") as
          Array<{ file: string; html: string; screenshot: string; meta: unknown }>;
        assertEqual(shared.length, 1);
        assertEqual(shared[0].file, "Root.html");
        assert(fs.existsSync(shared[0].screenshot),
          `bundle screenshot missing: ${shared[0].screenshot}`);
      } finally {
        cleanDir(dir);
      }
    });

    await it("render stage --no-bundle degrades honestly without spawning the bundler", async () => {
      const dir = tmpDir();
      try {
        let spawns = 0;
        const fakeBundle = async () => {
          spawns++;
          return {
            backend: "react_tailwind",
            screens: [],
            viewport: { width: 1440, height: 900 },
          };
        };

        const config = makeConfig(dir, {
          target: { framework: "react", styling: "tailwind" },
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
        pipeline.onStage("render",
          createRenderStageHandler({ noBundle: true, bundleInvoker: fakeBundle }));

        const result = await pipeline.run();
        assertEqual(result.status, "completed");
        assertEqual(spawns, 0, "--no-bundle must never spawn the bundler");
        assertEqual(pipeline.getShared("renderOutputs"), undefined,
          "no render rows without a real render");

        const renderArtifacts = artifacts.byStage("render");
        assertGreaterThan(renderArtifacts.length, 0, "expected a render artifact");
        const stored = artifacts.loadJSON(renderArtifacts[0]) as {
          note: string;
          screenshotPath: string | null;
        };
        assertEqual(stored.screenshotPath, null,
          "--no-bundle must report no screenshot, never a fabricated one");
        assert(stored.note.includes("no measured score"),
          `expected the honest degrade note, got: ${stored.note}`);
      } finally {
        cleanDir(dir);
      }
    });

    await it("invokeBundleRender surfaces a clean typed failure", async () => {
      const dir = tmpDir();
      try {
        await assertRejects(
          () => invokeBundleRender(
            { pythonBin: PYTHON_BIN, pluginDir: PLUGIN_DIR },
            "react_tailwind",
            path.join(dir, "no-such-generated"),
            undefined,
            { width: 1440, height: 900 },
            path.join(dir, "renders"),
          ),
          "exited 4",
        );
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

        // Part 20: the compare stage shares its resolved baseline so the
        // repair/verify stages consume it without re-resolving.
        const sharedBaseline = pipeline.getShared("compareBaseline") as
          string | undefined;
        assert(
          typeof sharedBaseline === "string",
          "compare stage must share the resolved baseline path",
        );
        assert(
          fs.existsSync(sharedBaseline as string),
          "shared baseline PNG should exist",
        );
        assertEqual(pipeline.getShared("compareBaselineKind"), "reference");
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

        // Part 20: the explicit baseline + kind are shared for repair/verify.
        assertEqual(pipeline.getShared("compareBaselineKind"), "explicit");
        assertEqual(pipeline.getShared("compareBaseline"), red.screenshot);
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

    // -----------------------------------------------------------------
    // Part 20 — repair stage handler (Task 5)
    // -----------------------------------------------------------------

    await it("repair short-circuits when the gate is already satisfied", async () => {
      const dir = tmpDir();
      try {
        // Threshold 0.5: the reference-baseline score (≈ 1.0, proven > 0.9
        // in the Part 19 chain) is deterministically above it, so the
        // gate-satisfied branch fires and repair must never spawn.
        const config = makeConfig(dir, {
          target: { framework: "html", styling: "css" },
          similarityThreshold: 0.5,
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
        pipeline.onStage("repair", createRepairStageHandler());

        const result = await pipeline.run();
        assertEqual(result.status, "completed");
        // 9 real stages (ingest→repair) + the event log.
        assertEqual(result.artifacts, 10, "expected 9 stage artifacts + event log");

        const repairArtifacts = artifacts.byStage("repair");
        assertEqual(repairArtifacts.length, 1);
        const repair = artifacts.loadJSON(repairArtifacts[0]) as {
          repairs: number;
          note: string;
          iterations_run: number;
          success: boolean | null;
        };
        assertEqual(repair.repairs, 0);
        assertEqual(repair.note, "gate already satisfied");
        assertEqual(repair.iterations_run, 0);
        assertEqual(repair.success, null);

        // No repair spawn: the repair out dir must not exist and the budget
        // must be untouched.
        const repairDir = path.join(dir, config.runId, "repair");
        assert(!fs.existsSync(repairDir), "short-circuit must never spawn repair");
        assertEqual(budget.current.repairIterations, 0);
      } finally {
        cleanDir(dir);
      }
    });

    await it("repair short-circuits on the reference-baseline contract when the gate fails", async () => {
      const dir = tmpDir();
      try {
        // Threshold 1.5 forces the gate check to fail on the reference
        // baseline (score ∈ [0,1] < 1.5), so the by-construction contract
        // branch is the one that fires — never a spawn.
        const config = makeConfig(dir, {
          target: { framework: "html", styling: "css" },
          similarityThreshold: 1.5,
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
        pipeline.onStage("repair", createRepairStageHandler());

        const result = await pipeline.run();
        assertEqual(result.status, "completed");
        const repairArtifacts = artifacts.byStage("repair");
        assertEqual(repairArtifacts.length, 1);
        const repair = artifacts.loadJSON(repairArtifacts[0]) as {
          repairs: number;
          note: string;
          iterations_run: number;
          success: boolean | null;
        };
        assertEqual(repair.repairs, 0);
        assertEqual(repair.iterations_run, 0);
        assertEqual(repair.success, null);
        assert(repair.note.includes("reference"),
          `expected the reference-baseline contract note, got: ${repair.note}`);
        const repairDir = path.join(dir, config.runId, "repair");
        assert(!fs.existsSync(repairDir),
          "reference-baseline contract must never spawn repair");
        assertEqual(budget.current.repairIterations, 0);
      } finally {
        cleanDir(dir);
      }
    });

    await it("repair stage degrades with no measured score when render degraded", async () => {
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
        pipeline.onStage("repair", createRepairStageHandler());

        const result = await pipeline.run();
        assertEqual(result.status, "completed");
        const repairArtifacts = artifacts.byStage("repair");
        assertEqual(repairArtifacts.length, 1);
        const repair = artifacts.loadJSON(repairArtifacts[0]) as {
          repairs: number;
          success: boolean | null;
          note: string;
        };
        assertEqual(repair.repairs, 0);
        assertEqual(repair.success, null);
        assert(repair.note.includes("no measured score"),
          `expected the no-measured-score degrade note, got: ${repair.note}`);
        const repairDir = path.join(dir, config.runId, "repair");
        assert(!fs.existsSync(repairDir), "degrade must never spawn repair");
        assertEqual(budget.current.repairIterations, 0);
      } finally {
        cleanDir(dir);
      }
    });

    await it("repair runs the real Python loop and regenerates html_css against an external baseline", async () => {
      const dir = tmpDir();
      try {
        // A deliberately-different external baseline: a full-red page at the
        // exact viewport (Part 19's margin-reset trick).  The compare stage
        // scores it well below the threshold, so the repair stage must spawn
        // the real Python loop and converge toward it.
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

        // Threshold 1.0: the compare score (< 0.9 vs the red baseline) is
        // below it, so repair runs; and the loop's own gate is 1.0, forcing
        // real patches (the capped pixel weight alone can't satisfy 1.0).
        const config = makeConfig(dir, {
          target: { framework: "html", styling: "css" },
          similarityThreshold: 1.0,
          budgets: {
            maxTokens: 10000,
            maxTimeMs: 120000,
            maxIterations: 100,
            maxRepairIterations: 3,
          },
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
        pipeline.onStage("repair", createRepairStageHandler());

        const result = await pipeline.run();
        assertEqual(result.status, "completed");
        const repairArtifacts = artifacts.byStage("repair");
        assertEqual(repairArtifacts.length, 1);
        const repair = artifacts.loadJSON(repairArtifacts[0]) as {
          iterations_run: number;
          final_score: number | null;
          repaired_styles: string | null;
          repaired_styles_path: string | null;
          generated: { backend: string; files: Array<{ path: string }> } | null;
        };
        assertGreaterThan(repair.iterations_run, 0,
          "a sub-threshold external baseline must actually run the repair loop");
        assert(typeof repair.final_score === "number",
          "final_score should be measured after repair");
        assert(repair.repaired_styles_path !== null,
          "repaired styles must serialize to the repair out dir");
        assert(fs.existsSync(repair.repaired_styles_path as string));
        assert(repair.generated !== null, "repair must regenerate html_css");
        assertEqual(repair.generated!.backend, "html_css");
        assertGreaterThan(repair.generated!.files.length, 0);

        // The regenerated files exist on disk and carry the repair: the
        // original computed card fill (#1a1a1a) must be gone from the output.
        const genDir = path.join(dir, config.runId, "repair", "generated", "html_css");
        const written = fs.readdirSync(genDir);
        const regeneratedHtml = written
          .filter((f) => f.endsWith(".html"))
          .map((f) => fs.readFileSync(path.join(genDir, f), "utf-8"));
        assertGreaterThan(regeneratedHtml.length, 0,
          "repair must write regenerated html files");
        assert(
          regeneratedHtml.some((h) => !h.includes("#1a1a1a")),
          "regenerated html should drop the original card fill the repair replaced",
        );

        // The budget must record the real iterations (the Repairs: line).
        assertGreaterThan(budget.current.repairIterations, 0,
          "the repair budget must be bumped by the real iterations");
      } finally {
        cleanDir(dir);
      }
    });

    await it("invokeRepair surfaces a typed error for a missing baseline", async () => {
      const dir = tmpDir();
      try {
        const fileJson = JSON.parse(fs.readFileSync(FIXTURE, "utf-8"));
        const irJson = await invokeNormalize(
          { pythonBin: PYTHON_BIN, pluginDir: PLUGIN_DIR },
          fileJson,
        );
        const layoutJson = await invokeLayout(
          { pythonBin: PYTHON_BIN, pluginDir: PLUGIN_DIR },
          irJson,
          1440,
        );
        await assertRejects(
          () => invokeRepair(
            { pythonBin: PYTHON_BIN, pluginDir: PLUGIN_DIR },
            irJson,
            layoutJson,
            path.join(dir, "missing.png"),
            path.join(dir, "repair"),
            { viewport: { width: 1440, height: 900 } },
          ),
          "exited 4",
        );
      } finally {
        cleanDir(dir);
      }
    });

    // -----------------------------------------------------------------
    // Part 20 — verify stage handler (Task 6)
    // -----------------------------------------------------------------

    await it("verify passes against the reference baseline when repair short-circuits", async () => {
      const dir = tmpDir();
      try {
        // Threshold 0.5: the reference score (≈ 1.0) is deterministically
        // above it, so repair short-circuits at the gate and verify runs the
        // final check on the same measurement.
        const config = makeConfig(dir, {
          target: { framework: "html", styling: "css" },
          similarityThreshold: 0.5,
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
        pipeline.onStage("repair", createRepairStageHandler());
        pipeline.onStage("verify", createVerifyStageHandler());

        const result = await pipeline.run();
        assertEqual(result.status, "completed");
        // 10 real stages (ingest→verify) + the event log.
        assertEqual(result.artifacts, 11, "expected 10 stage artifacts + event log");

        // The verify stage stores a metrics-kind artifact; byStage("verify")
        // also matches the event log (stored at stage verify), so filter by kind.
        const verifyArtifacts = artifacts.byKind("metrics");
        assertEqual(verifyArtifacts.length, 1);
        const verify = artifacts.loadJSON(verifyArtifacts[0]) as {
          passed: boolean | null;
          similarity_score: number | null;
          threshold: number;
          baseline_kind: string | null;
          source: string | null;
        };
        assertEqual(verify.passed, true);
        assert(typeof verify.similarity_score === "number");
        assertGreaterThan(verify.similarity_score as number, 0.9,
          "the fixture html_css output should closely reproduce the reference render");
        assertEqual(verify.threshold, 0.5);
        assertEqual(verify.baseline_kind, "reference");
        assertEqual(verify.source, "compare",
          "no repair → verify reuses the compare measurement");

        // Verify updates the checkpoint metric with the final score.
        const cp = checkpoints.loadLatest();
        assert(cp !== null);
        assertEqual(cp!.metrics.similarityScore, verify.similarity_score);
      } finally {
        cleanDir(dir);
      }
    });

    await it("verify re-measures the regenerated code after repair against the same baseline", async () => {
      const dir = tmpDir();
      try {
        // The red external baseline (Part 19 trick) → compare scores it low →
        // repair runs the real loop → verify must re-render the REGENERATED
        // files and measure the honest post-repair improvement.
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
          similarityThreshold: 1.0,
          budgets: {
            maxTokens: 10000,
            maxTimeMs: 180000,
            maxIterations: 100,
            maxRepairIterations: 3,
          },
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
        pipeline.onStage("repair", createRepairStageHandler());
        pipeline.onStage("verify", createVerifyStageHandler());

        const result = await pipeline.run();
        assertEqual(result.status, "completed");

        const compareArtifacts = artifacts.byStage("compare");
        const compareReport = artifacts.loadJSON(compareArtifacts[0]) as {
          similarity_score: number;
        };
        const verifyArtifacts = artifacts.byKind("metrics");
        assertEqual(verifyArtifacts.length, 1);
        const verify = artifacts.loadJSON(verifyArtifacts[0]) as {
          passed: boolean | null;
          similarity_score: number | null;
          threshold: number;
          baseline_kind: string | null;
          source: string | null;
          screens: Array<{ file: string; similarity: number }>;
        };
        assertEqual(verify.source, "re-rendered",
          "after repair, verify must re-render the regenerated files");
        assert(typeof verify.similarity_score === "number");
        assert(typeof verify.passed === "boolean",
          "verify must give a real pass/fail verdict");
        assertGreaterThan(verify.screens.length, 0);
        assertEqual(verify.baseline_kind, "explicit");
        assert(
          (verify.similarity_score as number) > compareReport.similarity_score,
          `post-repair score (${verify.similarity_score}) must beat the ` +
          `pre-repair score (${compareReport.similarity_score})`, 
        );

        // Verify updates the checkpoint metric to the post-repair score.
        const cp = checkpoints.loadLatest();
        assert(cp !== null);
        assertEqual(cp!.metrics.similarityScore, verify.similarity_score);
        // The repair budget carried the real iterations through the run.
        assertGreaterThan(budget.current.repairIterations, 0);
      } finally {
        cleanDir(dir);
      }
    });

    await it("verify degrades honestly when there is no measured score", async () => {
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
        pipeline.onStage("repair", createRepairStageHandler());
        pipeline.onStage("verify", createVerifyStageHandler());

        const result = await pipeline.run();
        assertEqual(result.status, "completed");
        const verifyArtifacts = artifacts.byKind("metrics");
        assertEqual(verifyArtifacts.length, 1);
        const verify = artifacts.loadJSON(verifyArtifacts[0]) as {
          passed: boolean | null;
          similarity_score: number | null;
          note: string;
        };
        assertEqual(verify.passed, null, "no score → never a fabricated verdict");
        assertEqual(verify.similarity_score, null);
        assert(verify.note.includes("no measured score"),
          `expected the no-measured-score degrade note, got: ${verify.note}`);
        const cp = checkpoints.loadLatest();
        assert(cp !== null);
        assertEqual(cp!.metrics.similarityScore, 0,
          "metrics must stay untouched when nothing was measured");
      } finally {
        cleanDir(dir);
      }
    });

    await it("--no-repair path: repair short-circuits and verify still reports honestly", async () => {
      const dir = tmpDir();
      try {
        // A low-scoring external baseline where repair WOULD run, but the
        // noRepair shared flag forces the short-circuit; verify must still
        // report the honest (failing) verdict on the compare measurement.
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
          similarityThreshold: 1.0,
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
        pipeline.setShared("noRepair", true);
        pipeline.onStage("ingest", createIngestStageHandler());
        pipeline.onStage("normalize", createNormalizeStageHandler());
        pipeline.onStage("resolve", createResolveStageHandler());
        pipeline.onStage("layout", createLayoutStageHandler());
        pipeline.onStage("assets", createAssetsStageHandler());
        pipeline.onStage("generate", createGenerateStageHandler());
        pipeline.onStage("render", createRenderStageHandler());
        pipeline.onStage("compare", createCompareStageHandler());
        pipeline.onStage("repair", createRepairStageHandler());
        pipeline.onStage("verify", createVerifyStageHandler());

        const result = await pipeline.run();
        assertEqual(result.status, "completed");

        const repairArtifacts = artifacts.byStage("repair");
        const repair = artifacts.loadJSON(repairArtifacts[0]) as {
          repairs: number;
          note: string;
        };
        assertEqual(repair.repairs, 0);
        assert(repair.note.includes("disabled"),
          `expected the disabled note, got: ${repair.note}`);
        const repairDir = path.join(dir, config.runId, "repair");
        assert(!fs.existsSync(repairDir), "--no-repair must never spawn repair");

        // Verify still reports honestly on the compare measurement.
        const compareArtifacts = artifacts.byStage("compare");
        const compareReport = artifacts.loadJSON(compareArtifacts[0]) as {
          similarity_score: number;
        };
        const verifyArtifacts = artifacts.byKind("metrics");
        assertEqual(verifyArtifacts.length, 1);
        const verify = artifacts.loadJSON(verifyArtifacts[0]) as {
          passed: boolean | null;
          similarity_score: number | null;
          source: string | null;
        };
        assertEqual(verify.source, "compare", "no repair → verify reuses the compare score");
        assertEqual(verify.similarity_score, compareReport.similarity_score);
        assertEqual(verify.passed, false,
          "a 1.0 threshold vs the red baseline must honestly fail verification");
        assertEqual(budget.current.repairIterations, 0,
          "no repair ran → no budget spend");
      } finally {
        cleanDir(dir);
      }
    });

    // -----------------------------------------------------------------
    // Part 22 — repair/verify backend threading (Task 3)
    // -----------------------------------------------------------------

    await it("invokeRepair threads --backend and --resolution into the real regeneration", async () => {
      const dir = tmpDir();
      try {
        // A red external baseline → the loop must patch toward red; the
        // regenerated output must be the requested react_tailwind backend
        // (not the html_css default) — proving --backend reached Python.
        const redHtml = path.join(dir, "red.html");
        fs.writeFileSync(redHtml, redBaselineHtml(), "utf-8");
        const red = await invokeRender(
          { pythonBin: PYTHON_BIN, pluginDir: PLUGIN_DIR },
          redHtml,
          { width: 1440, height: 900 },
          dir,
        );

        const fileJson = JSON.parse(fs.readFileSync(FIXTURE, "utf-8"));
        const irJson = await invokeNormalize(
          { pythonBin: PYTHON_BIN, pluginDir: PLUGIN_DIR }, fileJson,
        );
        const layoutJson = await invokeLayout(
          { pythonBin: PYTHON_BIN, pluginDir: PLUGIN_DIR }, irJson, 1440,
        );
        const resolutionJson = await invokeResolve(
          { pythonBin: PYTHON_BIN, pluginDir: PLUGIN_DIR }, irJson,
        );

        const repairDir = path.join(dir, "repair");
        const payload = await invokeRepair(
          { pythonBin: PYTHON_BIN, pluginDir: PLUGIN_DIR },
          irJson,
          layoutJson,
          red.screenshot,
          repairDir,
          {
            viewport: { width: 1440, height: 900 },
            maxIterations: 3,
            threshold: 1.0,
            backend: "react_tailwind",
            resolutionJson,
          },
        );
        const generated = (payload.generated as
          | { backend: string; files: Array<{ path: string }> }
          | null) ?? null;
        assert(generated !== null, "repair must regenerate");
        assertEqual(generated!.backend, "react_tailwind",
          "the run's backend must reach Python (--backend), not html_css");
        const tsx = path.join(
          repairDir, "generated", "react_tailwind", "Desktop.tsx",
        );
        assert(fs.existsSync(tsx), `regenerated TSX missing: ${tsx}`);
        const content = fs.readFileSync(tsx, "utf-8");
        assertIncludes(content, "bg-[#ff0000]",
          "the repaired (red) background must reach the regenerated TSX");
        assert(!fs.existsSync(path.join(repairDir, "generated", "html_css")),
          "html_css must not be regenerated when a backend is requested");
      } finally {
        cleanDir(dir);
      }
    });

    await it("repair stage regenerates the run's react backend; verify re-bundles and re-measures", async () => {
      const dir = tmpDir();
      try {
        const redHtml = path.join(dir, "red.html");
        fs.writeFileSync(redHtml, redBaselineHtml(), "utf-8");
        const red = await invokeRender(
          { pythonBin: PYTHON_BIN, pluginDir: PLUGIN_DIR },
          redHtml,
          { width: 1440, height: 900 },
          dir,
        );
        const RED_BYTES = fs.readFileSync(red.screenshot);

        const bundleCalls: Array<{
          backend: string;
          generatedDir: string;
          assetManifest: unknown;
          viewport: { width: number; height: number };
          outDir: string;
        }> = [];
        const fakeBundle = async (
          _cfg: { pythonBin: string; pluginDir: string },
          backend: string,
          generatedDir: string,
          assetManifest: unknown,
          viewport: { width: number; height: number },
          outDir: string,
        ) => {
          bundleCalls.push({ backend, generatedDir, assetManifest, viewport, outDir });
          const png = path.join(outDir, "screens", "Root.png");
          fs.mkdirSync(path.dirname(png), { recursive: true });
          // Render stage: bytes that differ from the baseline → the real
          // compare fails → repair runs.  Verify stage: bytes identical to
          // the baseline → the comparator's fast path gives a real 1.0
          // re-measurement of the regenerated output.
          const bytes = outDir.includes("verify-renders") ? RED_BYTES : fakePngBytes(1440, 900);
          fs.writeFileSync(png, bytes);
          return {
            backend,
            screens: [{
              component: "Root", png: "screens/Root.png", html: "Root.html",
            }],
            viewport,
          };
        };

        const config = makeConfig(dir, {
          target: { framework: "react", styling: "tailwind" },
          similarityThreshold: 1.0,
          budgets: {
            maxTokens: 10000,
            maxTimeMs: 180000,
            maxIterations: 100,
            maxRepairIterations: 3,
          },
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
        pipeline.onStage("render", createRenderStageHandler({ bundleInvoker: fakeBundle }));
        pipeline.onStage("compare", createCompareStageHandler());
        pipeline.onStage("repair", createRepairStageHandler());
        pipeline.onStage("verify", createVerifyStageHandler({ bundleInvoker: fakeBundle }));

        const result = await pipeline.run();
        assertEqual(result.status, "completed");

        // Repair regenerated the RUN's backend, not html_css.
        const repairArtifacts = artifacts.byStage("repair");
        const repair = artifacts.loadJSON(repairArtifacts[0]) as {
          generated: { backend: string } | null;
          iterations_run: number;
        };
        assert(repair.generated !== null, "repair must regenerate output");
        assertEqual(repair.generated!.backend, "react_tailwind",
          "the repair stage must regenerate the run's backend (Part 22)");
        assert(
          fs.existsSync(path.join(
            dir, config.runId, "repair", "generated", "react_tailwind",
            "Desktop.tsx",
          )),
          "regenerated react TSX missing under repair/generated/react_tailwind",
        );
        assertGreaterThan(repair.iterations_run, 0);

        // Verify re-bundled the regenerated dir against the same baseline.
        assertEqual(bundleCalls.length, 2, "render + verify each invoke the bundler");
        const verifyCall = bundleCalls[1];
        assertEqual(verifyCall.backend, "react_tailwind");
        assert(
          verifyCall.generatedDir.endsWith(
            path.join("repair", "generated", "react_tailwind"),
          ),
          `verify must re-bundle the regenerated dir, got: ${verifyCall.generatedDir}`,
        );
        assert(verifyCall.outDir.endsWith("verify-renders"),
          `verify must re-render into verify-renders, got: ${verifyCall.outDir}`);
        assert(
          typeof verifyCall.assetManifest === "object"
            && verifyCall.assetManifest !== null,
          "the shared asset manifest must thread into the verify re-bundle",
        );

        const verifyArtifacts = artifacts.byKind("metrics");
        assertEqual(verifyArtifacts.length, 1);
        const verify = artifacts.loadJSON(verifyArtifacts[0]) as {
          passed: boolean | null;
          similarity_score: number | null;
          source: string | null;
          baseline_kind: string | null;
        };
        assertEqual(verify.source, "re-rendered",
          "after repair, verify must re-render the regenerated output");
        assertEqual(verify.similarity_score, 1.0,
          "identical bytes vs the same baseline → a real 1.0 re-measurement");
        assertEqual(verify.passed, true);
        assertEqual(verify.baseline_kind, "explicit");
        const cp = checkpoints.loadLatest();
        assert(cp !== null);
        assertEqual(cp!.metrics.similarityScore, 1.0,
          "verify must update the checkpoint metric to the re-measured score");
      } finally {
        cleanDir(dir);
      }
    });

    await it("repair stage defaults to html_css with a note when generatedManifest is missing", async () => {
      const dir = tmpDir();
      try {
        const redHtml = path.join(dir, "red.html");
        fs.writeFileSync(redHtml, redBaselineHtml(), "utf-8");
        const red = await invokeRender(
          { pythonBin: PYTHON_BIN, pluginDir: PLUGIN_DIR },
          redHtml,
          { width: 1440, height: 900 },
          dir,
        );
        const fileJson = JSON.parse(fs.readFileSync(FIXTURE, "utf-8"));
        const irJson = await invokeNormalize(
          { pythonBin: PYTHON_BIN, pluginDir: PLUGIN_DIR }, fileJson,
        );
        const layoutJson = await invokeLayout(
          { pythonBin: PYTHON_BIN, pluginDir: PLUGIN_DIR }, irJson, 1440,
        );

        const config = makeConfig(dir, {
          target: { framework: "react", styling: "tailwind" },
          similarityThreshold: 1.0,
          budgets: {
            maxTokens: 10000,
            maxTimeMs: 120000,
            maxIterations: 100,
            maxRepairIterations: 2,
          },
        });
        const events = new EventLog(config.runId);
        const checkpoints = new CheckpointManager(config.runId, config.outputDir);
        const artifacts = new ArtifactStore(config.runId, config.outputDir);
        const tools = new ToolRegistry();
        const budget = new BudgetTracker(config.budgets);

        // Repair-only pipeline (no generate/render/compare): the shared
        // compare state is pre-set so the stage runs its real loop, and no
        // generatedManifest exists — the F7 defensive default applies.
        const pipeline = new PipelineCoordinator(
          config, events, checkpoints, artifacts, tools, budget,
        );
        pipeline.setShared("irJson", irJson);
        pipeline.setShared("layoutJson", layoutJson);
        pipeline.setShared("diffReport", { similarity_score: 0.2, screens: [] });
        pipeline.setShared("compareBaseline", red.screenshot);
        pipeline.setShared("compareBaselineKind", "explicit");
        pipeline.onStage("repair", createRepairStageHandler());

        const result = await pipeline.run();
        assertEqual(result.status, "completed");
        const repairArtifacts = artifacts.byStage("repair");
        const repair = artifacts.loadJSON(repairArtifacts[0]) as {
          generated: { backend: string } | null;
          note: string | null;
          iterations_run: number;
        };
        assert(repair.generated !== null, "repair must regenerate");
        assertEqual(repair.generated!.backend, "html_css",
          "missing generatedManifest → html_css default (F7)");
        assert(
          repair.note !== null && repair.note.includes("generatedManifest"),
          `expected the F7 default note, got: ${repair.note}`,
        );
        assertGreaterThan(repair.iterations_run, 0);
      } finally {
        cleanDir(dir);
      }
    });

    await it("verify re-bundles regenerated web output against the same baseline", async () => {
      const dir = tmpDir();
      try {
        const baselinePath = path.join(dir, "baseline.png");
        const pngBytes = fakePngBytes(1440, 900);
        fs.writeFileSync(baselinePath, pngBytes);

        const calls: Array<{
          backend: string;
          generatedDir: string;
          assetManifest: unknown;
          viewport: { width: number; height: number };
          outDir: string;
        }> = [];
        const fakeBundle = async (
          _cfg: { pythonBin: string; pluginDir: string },
          backend: string,
          generatedDir: string,
          assetManifest: unknown,
          viewport: { width: number; height: number },
          outDir: string,
        ) => {
          calls.push({ backend, generatedDir, assetManifest, viewport, outDir });
          const png = path.join(outDir, "screens", "Root.png");
          fs.mkdirSync(path.dirname(png), { recursive: true });
          fs.writeFileSync(png, pngBytes); // identical → 1.0 fast path
          return {
            backend,
            screens: [{
              component: "Root", png: "screens/Root.png", html: "Root.html",
            }],
            viewport,
          };
        };

        const config = makeConfig(dir, {
          target: { framework: "react", styling: "tailwind" },
          similarityThreshold: 0.9,
        });
        const events = new EventLog(config.runId);
        const checkpoints = new CheckpointManager(config.runId, config.outputDir);
        const artifacts = new ArtifactStore(config.runId, config.outputDir);
        const tools = new ToolRegistry();
        const budget = new BudgetTracker(config.budgets);

        // Verify-only pipeline: the shared compare/repair state is pre-set,
        // so the handler's bundler branch runs with a faked invoker (no real
        // npm/chromium — the money test covers the real toolchain).
        const pipeline = new PipelineCoordinator(
          config, events, checkpoints, artifacts, tools, budget,
        );
        pipeline.setShared("diffReport", { similarity_score: 0.2, screens: [] });
        pipeline.setShared("compareBaseline", baselinePath);
        pipeline.setShared("compareBaselineKind", "explicit");
        pipeline.setShared("repairManifest", {
          generated: { backend: "react_tailwind", files: [{ path: "Root.tsx" }] },
        });
        pipeline.setShared("assetManifest", {
          schema_version: 1, file_key: "x", assets: [],
          counts: { total: 0, downloaded: 0, unresolved: 0 },
          assets_dir: "",
        });
        pipeline.onStage("verify", createVerifyStageHandler({ bundleInvoker: fakeBundle }));

        const result = await pipeline.run();
        assertEqual(result.status, "completed");
        assertEqual(calls.length, 1, "the verify bundler branch must invoke the bundler");
        assertEqual(calls[0].backend, "react_tailwind");
        assert(
          calls[0].generatedDir.endsWith(
            path.join("repair", "generated", "react_tailwind"),
          ),
          `verify must re-bundle the regenerated dir, got: ${calls[0].generatedDir}`,
        );
        assert(calls[0].outDir.endsWith("verify-renders"),
          `unexpected verify outDir: ${calls[0].outDir}`);
        assertEqual(calls[0].viewport.width, 1440);

        const verifyArtifacts = artifacts.byKind("metrics");
        assertEqual(verifyArtifacts.length, 1);
        const verify = artifacts.loadJSON(verifyArtifacts[0]) as {
          passed: boolean | null;
          similarity_score: number | null;
          source: string | null;
          baseline_kind: string | null;
          screens: Array<{ file: string; similarity: number }>;
        };
        assertEqual(verify.source, "re-rendered");
        assertEqual(verify.similarity_score, 1.0);
        assertEqual(verify.passed, true);
        assertEqual(verify.baseline_kind, "explicit");
        assertEqual(verify.screens.length, 1);
        const cp = checkpoints.loadLatest();
        assert(cp !== null);
        assertEqual(cp!.metrics.similarityScore, 1.0);
      } finally {
        cleanDir(dir);
      }
    });

    await it("verify guards a non-browser regenerated backend without spawning", async () => {
      const dir = tmpDir();
      try {
        const baselinePath = path.join(dir, "baseline.png");
        fs.writeFileSync(baselinePath, fakePngBytes(1440, 900));

        let spawns = 0;
        const fakeBundle = async () => {
          spawns++;
          return {
            backend: "flutter", screens: [],
            viewport: { width: 1440, height: 900 },
          };
        };

        const config = makeConfig(dir); // flutter target
        const events = new EventLog(config.runId);
        const checkpoints = new CheckpointManager(config.runId, config.outputDir);
        const artifacts = new ArtifactStore(config.runId, config.outputDir);
        const tools = new ToolRegistry();
        const budget = new BudgetTracker(config.budgets);

        const pipeline = new PipelineCoordinator(
          config, events, checkpoints, artifacts, tools, budget,
        );
        pipeline.setShared("diffReport", { similarity_score: 0.2, screens: [] });
        pipeline.setShared("compareBaseline", baselinePath);
        pipeline.setShared("compareBaselineKind", "explicit");
        pipeline.setShared("repairManifest", {
          generated: { backend: "flutter", files: [{ path: "Root.dart" }] },
        });
        pipeline.onStage("verify", createVerifyStageHandler({ bundleInvoker: fakeBundle }));

        const result = await pipeline.run();
        assertEqual(result.status, "completed");
        assertEqual(spawns, 0, "a native regenerated backend must never spawn");
        const verifyArtifacts = artifacts.byKind("metrics");
        assertEqual(verifyArtifacts.length, 1);
        const verify = artifacts.loadJSON(verifyArtifacts[0]) as {
          passed: boolean | null;
          similarity_score: number | null;
          source: string | null;
          note: string;
        };
        assertEqual(verify.source, "re-rendered");
        assertEqual(verify.passed, null, "never a fabricated verdict");
        assertEqual(verify.similarity_score, null);
        assert(verify.note.includes("flutter"),
          `expected the native no-browser-harness note, got: ${verify.note}`);
        const cp = checkpoints.loadLatest();
        assert(cp !== null);
        assertEqual(cp!.metrics.similarityScore, 0,
          "metrics must stay untouched for the native guard");
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
