#!/usr/bin/env node
/**
 * FigmaForge CLI — command-line interface for the runtime.
 *
 * Commands:
 *   figmaforge run      — Run the full pipeline
 *   figmaforge inspect  — Inspect a previous run
 *   figmaforge render   — Run only the render stage
 *   figmaforge compare  — Run only the compare stage
 *   figmaforge repair   — Run only the repair stage
 *   figmaforge replay   — Replay a previous run from event log
 */

import * as fs from "node:fs";
import * as path from "node:path";
import { spawnSync } from "node:child_process";
import { pathToFileURL } from "node:url";
import { PIPELINE_STAGES, DEFAULT_CONFIG, DEFAULT_BUDGETS, DEFAULT_RETRY, makeRunId, PRESET_TARGETS, parseTargetKey, targetKey } from "../core/types.js";
import type { RuntimeConfig, PipelineStage, CodegenTarget } from "../core/types.js";
import { EventLog } from "../core/events.js";
import { CheckpointManager } from "../core/checkpoint.js";
import { ArtifactStore } from "../core/artifacts.js";
import { ToolRegistry } from "../core/tools.js";
import { BudgetTracker } from "../core/budget.js";
import { PipelineCoordinator } from "../core/pipeline.js";
import { ScreenshotComparator } from "../core/screenshot_compare.js";
import { buildBrowserRenderScript, parseBrowserRenderOutput } from "../core/render_handler.js";
import { invokeAdaptivePreflight } from "../core/adaptive_preflight.js";
import type { AdaptivePlan } from "../core/adaptive_preflight.js";
import {
  createIngestStageHandler,
  createNormalizeStageHandler,
  createResolveStageHandler,
  createLayoutStageHandler,
  createGenerateStageHandler,
  createAssetsStageHandler,
  createRenderStageHandler,
  createCompareStageHandler,
  createRepairStageHandler,
  createVerifyStageHandler,
  invokeIngest,
  invokeBackendGenerator,
  TARGET_BACKENDS,
} from "../core/backend_codegen.js";

// ---------------------------------------------------------------------------
// Argument parsing
// ---------------------------------------------------------------------------

interface CliArgs {
  command: string;
  flags: Record<string, string>;
  positional: string[];
}

function parseArgs(argv: string[]): CliArgs {
  const command = argv[2] ?? "help";
  const flags: Record<string, string> = {};
  const positional: string[] = [];

  for (let i = 3; i < argv.length; i++) {
    const arg = argv[i];
    if (arg.startsWith("--")) {
      const eqIdx = arg.indexOf("=");
      if (eqIdx > 0) {
        flags[arg.slice(2, eqIdx)] = arg.slice(eqIdx + 1);
      } else {
        const key = arg.slice(2);
        const next = argv[i + 1];
        if (next && !next.startsWith("--")) {
          flags[key] = next;
          i++;
        } else {
          flags[key] = "true";
        }
      }
    } else {
      positional.push(arg);
    }
  }

  return { command, flags, positional };
}

// ---------------------------------------------------------------------------
// Help
// ---------------------------------------------------------------------------

function printHelp(): void {
  console.log(`
FigmaForge Runtime CLI

Usage: figmaforge <command> [options]

Commands:
  run       Run the full pipeline on a Figma file
  inspect   Inspect a previous run's artifacts and events
  render    Run only the render stage for a run
  compare   Run only the compare stage for a run
  repair    Run only the repair stage for a run
  replay    Replay a previous run from its event log
  demo      Generate all six backends and print a comparison table

Options:
  --file-key=<key>         Figma file key (live ingest; requires FIGMA_TOKEN)
  --file=<path>            Local Figma file JSON (offline ingest)
                           Exactly one of --file-key / --file is required for 'run'
  --out=<dir>              Demo output directory (default: ./demo-out)
  --render                 Best-effort render of the html_css output (Playwright)
  --output-dir=<path>      Output directory (default: ./figmaforge-output)
  --target=<framework+styling>  Code generation target (default: html+css)
                           Format: <framework>+<styling> — any combination is valid.
                           Presets: html+css, react+css, react+tailwind, vue+scoped_css,
                           svelte+scoped_css, swiftui+swiftui_modifiers, flutter+flutter_widgets
  --adaptive               Run adaptive preflight with the default request
  --adaptive-request=<text>  Run adaptive preflight with a custom request
  --run-id=<id>            Run ID (auto-generated if not specified)
  --resume                 Resume from latest checkpoint
  --threshold=<0.0-1.0>    Similarity threshold (default: 0.95)
  --max-iterations=<n>     Max pipeline iterations (default: 20)
  --max-repair=<n>         Max repair iterations (default: 10)
  --max-time=<ms>          Max time in milliseconds (default: 300000)
  --viewport=<WxH>         Viewport size (default: 1440x900)
  --baseline=<path.png>    Compare the render against this explicit PNG baseline
                           (overrides the default IR reference baseline)
  --figma-baseline         Use live Figma renders as the baseline (requires
                           --file-key and the FIGMA_TOKEN env var)
  --no-repair              Skip the auto-repair stage (repair short-circuits;
                           verify still reports the final gate)
  --no-bundle              Skip bundling react/vue/svelte renders (honest
                           no-measured-score degrade instead of a real build)
  --similarity-threshold=<0.0-1.0>  Repair/verify similarity gate (alias of
                           --threshold; default: 0.95)
  --no-approval            Skip approval gates
  --approve-dir=<path>     Add an approved directory (repeatable)
  --verbose                Enable verbose output
  --help                   Show this help message

Examples:
  figmaforge run --file-key=abc123 --output-dir=./output
  figmaforge inspect --run-id=run-abc --output-dir=./output
  figmaforge replay --run-id=run-abc --output-dir=./output
`);
}

// ---------------------------------------------------------------------------
// Build config from CLI args
// ---------------------------------------------------------------------------

function buildConfig(args: CliArgs): RuntimeConfig {
  const runId = args.flags["run-id"] ?? makeRunId();
  const outputDir = path.resolve(args.flags["output-dir"] ?? "./figmaforge-output");
  const fileKey = args.flags["file-key"] ?? "";
  const threshold = parseFloat(
    args.flags["threshold"] ?? args.flags["similarity-threshold"] ?? "0.95",
  );
  const maxIterations = parseInt(args.flags["max-iterations"] ?? "20", 10);
  const maxRepair = parseInt(args.flags["max-repair"] ?? "10", 10);
  const maxTime = parseInt(args.flags["max-time"] ?? "300000", 10);

  const viewportStr = args.flags["viewport"] ?? "1440x900";
  const [w, h] = viewportStr.split("x").map(Number);

  const approvedDirs = [outputDir];
  if (args.flags["approve-dir"]) {
    approvedDirs.push(path.resolve(args.flags["approve-dir"]));
  }

  const targetStr = args.flags["target"] ?? "html+css";
  const target: CodegenTarget = parseTargetKey(targetStr);
  const presetKeys = PRESET_TARGETS.map(t => targetKey(t));
  if (!presetKeys.includes(targetKey(target))) {
    console.log(`Note: "${targetKey(target)}" is not a preset — will use backend registry to resolve.`);
    console.log(`  Presets: ${presetKeys.join(", ")}`);
  }

  return {
    runId,
    fileKey,
    outputDir,
    approvedDirs,
    requireApproval: args.flags["no-approval"] !== "true",
    retry: { ...DEFAULT_RETRY },
    budgets: {
      maxTokens: DEFAULT_BUDGETS.maxTokens,
      maxTimeMs: maxTime,
      maxIterations: maxIterations,
      maxRepairIterations: maxRepair,
    },
    similarityThreshold: threshold,
    minProgress: DEFAULT_CONFIG.minProgress,
    viewport: { width: w || 1440, height: h || 900 },
    pythonBin: process.env.PYTHON_BIN ?? DEFAULT_CONFIG.pythonBin,
    pluginDir: path.resolve(args.flags["plugin-dir"] ?? "./plugin/figmaforge"),
    target,
  };
}

// ---------------------------------------------------------------------------
// Commands
// ---------------------------------------------------------------------------

const DEFAULT_ADAPTIVE_REQUEST =
  "Convert this Figma design into the selected code-generation target";

type PipelineRunner = Pick<
  PipelineCoordinator,
  "setAbortSignal" | "setShared" | "onStage" | "run"
>;

interface CliRuntimeDeps {
  invokeAdaptivePreflight: (
    cfg: { pythonBin: string; pluginDir: string },
    root: string,
    request: string,
  ) => Promise<AdaptivePlan>;
  createPipeline: (
    config: RuntimeConfig,
    events: EventLog,
    checkpoints: CheckpointManager,
    artifacts: ArtifactStore,
    tools: ToolRegistry,
    budget: BudgetTracker,
    approvalCallback?: (req: { action: string; description: string }) => Promise<boolean>,
  ) => PipelineRunner;
}

const defaultCliRuntimeDeps: CliRuntimeDeps = {
  invokeAdaptivePreflight,
  createPipeline: (config, events, checkpoints, artifacts, tools, budget, approvalCallback) =>
    new PipelineCoordinator(
      config, events, checkpoints, artifacts, tools, budget, approvalCallback,
    ),
};

let cliRuntimeDeps: CliRuntimeDeps = defaultCliRuntimeDeps;

export function setCliTestDepsForTesting(overrides: Partial<CliRuntimeDeps>): void {
  cliRuntimeDeps = { ...defaultCliRuntimeDeps, ...overrides };
}

export function resetCliTestDepsForTesting(): void {
  cliRuntimeDeps = defaultCliRuntimeDeps;
}

function generateSimpleHtml(vnode: unknown, viewport: { width: number; height: number }): string {
  const data = vnode as Record<string, unknown>;
  const tag = (data.tag as string) ?? "div";
  const text = (data.text as string) ?? "";
  const style = (data.style ?? {}) as Record<string, string | number>;
  const css = Object.entries(style)
    .filter(([, v]) => v !== undefined)
    .map(([k, v]) => `${k.replace(/([A-Z])/g, "-$1").toLowerCase()}: ${v}`)
    .join("; ");
  const children = (data.children as unknown[]) ?? [];
  const childHtml = children.map((c) => generateSimpleHtml(c, viewport)).join("\n");

  return `<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>FigmaForge Render</title>
<style>* { margin: 0; padding: 0; box-sizing: border-box; }
body { width: ${viewport.width}px; min-height: ${viewport.height}px; font-family: sans-serif; }</style>
</head><body>
<div id="figmaforge-root">
<${tag}${css ? ` style="${css}"` : ""}>${text}${childHtml}</${tag}>
</div></body></html>`;
}

async function cmdRun(args: CliArgs): Promise<void> {
  const config = buildConfig(args);
  const localFile = args.flags["file"];

  if (!config.fileKey && !localFile) {
    console.error("Error: --file-key or --file is required for 'run' command");
    process.exit(1);
  }

  console.log(`Starting pipeline run ${config.runId}`);
  console.log(`  File key: ${config.fileKey || "(local file)"}`);
  if (localFile) console.log(`  Local file: ${path.resolve(localFile)}`);
  console.log(`  Output:   ${config.outputDir}`);
  console.log(`  Threshold: ${config.similarityThreshold}`);
  console.log(`  Viewport:  ${config.viewport.width}x${config.viewport.height}`);

  const events = new EventLog(config.runId);
  const checkpoints = new CheckpointManager(config.runId, config.outputDir);
  const artifacts = new ArtifactStore(config.runId, config.outputDir);
  const tools = new ToolRegistry();
  const budget = new BudgetTracker(config.budgets);

  // Set up approval callback
  const approvalCallback = config.requireApproval
    ? async (req: { action: string; description: string }) => {
        console.log(`\n[APPROVAL REQUIRED] ${req.action}`);
        console.log(`  ${req.description}`);
        console.log(`  Auto-denying (non-interactive mode). Use --no-approval to skip.`);
        return false;
      }
    : undefined;

  const pipeline = cliRuntimeDeps.createPipeline(
    config, events, checkpoints, artifacts, tools, budget, approvalCallback,
  );

  // Set up cancellation
  const ac = new AbortController();
  process.on("SIGINT", () => {
    console.log("\nCancelling...");
    ac.abort();
  });
  pipeline.setAbortSignal(ac.signal);

  // Wire the real Python pipeline stages: ingest fetches (or reads locally)
  // the Figma file, normalize/resolve/layout run the front half through the
  // pipeline CLI, assets downloads + content-addresses IR asset refs, and
  // generate lowers the stage artifacts through the backend
  // (Part 15: ingest+generate; Part 16: full front half; Part 17: assets).
  // render + compare (Part 19) then measure the generated output against a
  // baseline (explicit --baseline, live --figma-baseline, or the IR
  // reference render); repair + verify (Part 20) close the ten-stage loop:
  // auto-repair toward an external baseline and measure the final gate.
  if (localFile) {
    pipeline.setShared("filePath", path.resolve(localFile));
  }
  if (args.flags["baseline"]) {
    pipeline.setShared("baselinePath", path.resolve(args.flags["baseline"]));
  }
  if (args.flags["figma-baseline"] === "true") {
    pipeline.setShared("figmaBaseline", true);
  }
  if (args.flags["no-repair"] === "true") {
    pipeline.setShared("noRepair", true);
  }
  const noBundle = args.flags["no-bundle"] === "true";
  pipeline.onStage("ingest", createIngestStageHandler());
  pipeline.onStage("normalize", createNormalizeStageHandler());
  pipeline.onStage("resolve", createResolveStageHandler());
  pipeline.onStage("layout", createLayoutStageHandler());
  pipeline.onStage("assets", createAssetsStageHandler());
  pipeline.onStage("generate", createGenerateStageHandler());
  // NOTE: PIPELINE_STAGES runs assets before generate (Part 18) so the
  // assets-stage manifest can thread resolved paths into generated code.
  pipeline.onStage("render", createRenderStageHandler({ noBundle }));
  pipeline.onStage("compare", createCompareStageHandler());
  pipeline.onStage("repair", createRepairStageHandler());
  pipeline.onStage("verify", createVerifyStageHandler());

  const adaptiveRequest = args.flags["adaptive-request"]
    ?? (args.flags["adaptive"] === "true" ? DEFAULT_ADAPTIVE_REQUEST : undefined);
  if (adaptiveRequest !== undefined) {
    const plan = await cliRuntimeDeps.invokeAdaptivePreflight(
      { pythonBin: config.pythonBin, pluginDir: config.pluginDir },
      process.cwd(),
      adaptiveRequest,
    );
    artifacts.storeJSON("adaptive_plan", "preflight", "adaptive_plan", plan);
    events.emit("adaptive_plan_created", "Adaptive preflight completed", {
      data: {
        stack_status: plan.route.stack_status,
        execution_mode: plan.route.execution_mode,
        phases: plan.route.phases,
        approval_gates: plan.route.approval_gates,
      },
    });
  }

  const result = await pipeline.run();

  console.log(`\nPipeline ${result.status}`);
  console.log(`  Duration:   ${result.totalDurationMs}ms`);
  console.log(`  Score:      ${result.similarityScore}`);
  console.log(`  Repairs:    ${result.repairIterations}`);
  console.log(`  Tokens:     ${result.tokensUsed}`);
  console.log(`  Artifacts:  ${result.artifacts}`);
  console.log(`  Events:     ${result.events}`);

  // Measured visual verdict from the compare stage's diff_report artifact
  // (Part 19) — a real number or an honest "no measured score" note.
  const diffArtifacts = artifacts.byKind("diff_report");
  if (diffArtifacts.length > 0) {
    const report = artifacts.loadJSON(diffArtifacts[0]) as {
      similarity_score: number | null;
      baseline_kind: string | null;
      raster_stats?: { ssim?: number | null; ssim_clean?: boolean | null } | null;
      note?: string | null;
    };
    if (report.similarity_score !== null) {
      const verdict = report.raster_stats?.ssim_clean === true
        ? "perceptually identical"
        : report.raster_stats?.ssim_clean === false
          ? "perceptual change"
          : "unavailable";
      console.log(
        `  Visual verdict: similarity ${report.similarity_score.toFixed(4)} ` +
        `vs ${report.baseline_kind ?? "?"} baseline — ${verdict} ` +
        `(SSIM ${(report.raster_stats?.ssim ?? -1).toFixed(4)})`,
      );
    } else {
      console.log(`  Visual verdict: no measured score (${report.note ?? "no screenshots"})`);
    }
  }

  // Final verification from the verify stage's metrics-kind artifact (Part
  // 20) — the terminal pass/fail gate: PASSED / FAILED / cannot verify.
  // A failed verification does NOT fail the run: the report is valid output
  // (the run already exited 0 for a completed pipeline).
  const verifyArtifacts = artifacts.byKind("metrics");
  if (verifyArtifacts.length > 0) {
    const verify = artifacts.loadJSON(verifyArtifacts[0]) as {
      passed: boolean | null;
      similarity_score: number | null;
      threshold: number;
      note?: string | null;
    };
    if (verify.passed === true) {
      console.log(
        `  Verification: PASSED ` +
        `(${(verify.similarity_score ?? 0).toFixed(4)} >= ${verify.threshold})`,
      );
    } else if (verify.passed === false) {
      console.log(
        `  Verification: FAILED ` +
        `(${(verify.similarity_score ?? 0).toFixed(4)} < ${verify.threshold})`,
      );
    } else {
      console.log(`  Verification: cannot verify (${verify.note ?? "no measured score"})`);
    }
  }

  if (result.errors.length > 0) {
    console.log(`\nErrors:`);
    for (const err of result.errors) {
      console.log(`  - ${err}`);
    }
  }

  if (result.status !== "completed") {
    process.exit(1);
  }
}

async function cmdInspect(args: CliArgs): Promise<void> {
  const config = buildConfig(args);
  const runId = args.flags["run-id"] ?? config.runId;

  const artifactsDir = path.join(config.outputDir, runId, "artifacts");
  const checkpointsDir = path.join(config.outputDir, runId, "checkpoints");
  const manifestPath = path.join(artifactsDir, "..", "manifest.json");

  console.log(`Inspecting run ${runId}`);

  // Load manifest
  if (fs.existsSync(manifestPath)) {
    const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf-8"));
    console.log(`\nArtifacts (${manifest.artifacts.length}):`);
    for (const a of manifest.artifacts) {
      console.log(`  [${a.kind}] ${a.label ?? a.path} (${a.size} bytes)`);
    }
  } else {
    console.log("No manifest found.");
  }

  // Load checkpoints
  if (fs.existsSync(checkpointsDir)) {
    const files = fs.readdirSync(checkpointsDir).filter((f: string) => f.endsWith(".json"));
    console.log(`\nCheckpoints (${files.length}):`);
    for (const f of files) {
      const cp = JSON.parse(fs.readFileSync(path.join(checkpointsDir, f), "utf-8"));
      console.log(`  ${cp.stage} → next: ${cp.nextStage} (score: ${cp.metrics.similarityScore})`);
    }
  }

  // Load event log summary
  const eventLogPath = path.join(artifactsDir, "..", "artifacts", "verify_event_log_*.json");
  // Try to find event log in artifacts
  if (fs.existsSync(artifactsDir)) {
    const eventFiles = fs.readdirSync(artifactsDir).filter((f: string) => f.includes("event_log"));
    if (eventFiles.length > 0) {
      const events = JSON.parse(
        fs.readFileSync(path.join(artifactsDir, eventFiles[0]), "utf-8"),
      );
      console.log(`\nEvents (${events.length}):`);
      const errors = events.filter((e: { level: string }) => e.level === "error");
      const warnings = events.filter((e: { level: string }) => e.level === "warn");
      console.log(`  Errors:   ${errors.length}`);
      console.log(`  Warnings: ${warnings.length}`);
      if (errors.length > 0) {
        console.log(`\n  Error details:`);
        for (const e of errors.slice(0, 10)) {
          console.log(`    [${e.seq}] ${e.message}`);
        }
      }
    }
  }
}

async function cmdReplay(args: CliArgs): Promise<void> {
  const config = buildConfig(args);
  const runId = args.flags["run-id"];

  if (!runId) {
    console.error("Error: --run-id is required for 'replay' command");
    process.exit(1);
  }

  const artifactsDir = path.join(config.outputDir, runId, "artifacts");
  if (!fs.existsSync(artifactsDir)) {
    console.error(`No artifacts found for run ${runId}`);
    process.exit(1);
  }

  // Find and load event log
  const eventFiles = fs.readdirSync(artifactsDir).filter((f: string) => f.includes("event_log"));
  if (eventFiles.length === 0) {
    console.error("No event log found for replay");
    process.exit(1);
  }

  const events = JSON.parse(
    fs.readFileSync(path.join(artifactsDir, eventFiles[0]), "utf-8"),
  );

  console.log(`Replaying run ${runId} (${events.length} events)`);
  console.log("---");

  for (const event of events) {
    const ts = event.timestamp?.slice(11, 23) ?? "?";
    const prefix = event.level === "error" ? "ERR" : event.level === "warn" ? "WRN" : "   ";
    console.log(`[${ts}] ${prefix} [${event.kind}] ${event.message}`);
    if (event.data && args.flags["verbose"] === "true") {
      console.log(`         ${JSON.stringify(event.data).slice(0, 200)}`);
    }
  }
}

async function cmdRender(args: CliArgs): Promise<void> {
  const config = buildConfig(args);
  const runId = args.flags["run-id"] ?? config.runId;
  const artifactsDir = path.join(config.outputDir, runId, "artifacts");

  console.log(`Render stage for run ${runId}`);

  // Look for generated code artifact
  let vnode: unknown = null;
  if (fs.existsSync(artifactsDir)) {
    const codeFiles = fs.readdirSync(artifactsDir).filter((f: string) => f.includes("generated_code"));
    if (codeFiles.length > 0) {
      vnode = JSON.parse(fs.readFileSync(path.join(artifactsDir, codeFiles[0]), "utf-8"));
      console.log(`  Loaded generated code from ${codeFiles[0]}`);
    }
  }

  const outputDir = path.join(config.outputDir, runId, "renders");
  fs.mkdirSync(outputDir, { recursive: true });

  // Generate HTML from VNode or create placeholder
  const viewport = config.viewport;
  const html = vnode
    ? generateSimpleHtml(vnode, viewport)
    : `<!DOCTYPE html><html><body style="width:${viewport.width}px;height:${viewport.height}px;background:#f0f0f0;padding:20px;"><h1>FigmaForge Render</h1><p>No generated code found for run ${runId}.</p></body></html>`;

  const htmlPath = path.join(outputDir, "render.html");
  fs.writeFileSync(htmlPath, html, "utf-8");
  console.log(`  HTML written to ${htmlPath}`);

  // Try to take a screenshot via Playwright
  try {
    const screenshotPath = path.join(outputDir, "screenshot.png");
    const { execFileSync } = await import("node:child_process");
    const pyScript = `
import sys, json
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": ${viewport.width}, "height": ${viewport.height}})
        page.goto("file://${htmlPath}")
        page.wait_for_load_state("networkidle")
        page.screenshot(path="${screenshotPath}", full_page=True)
        browser.close()
        print("ok")
except ImportError:
    print("playwright_not_installed")
except Exception as e:
    print(f"error:{e}")
`;
    const result = execFileSync(config.pythonBin, ["-c", pyScript], {
      timeout: 30_000,
      encoding: "utf-8",
    }).trim();

    if (result === "ok" && fs.existsSync(screenshotPath)) {
      console.log(`  Screenshot saved to ${screenshotPath}`);
    } else {
      console.log(`  Playwright: ${result} — HTML-only render available`);
    }
  } catch {
    console.log("  Playwright not available — HTML-only render available");
  }
}

async function cmdCompare(args: CliArgs): Promise<void> {
  const config = buildConfig(args);
  const runId = args.flags["run-id"] ?? config.runId;
  const artifactsDir = path.join(config.outputDir, runId, "artifacts");
  const rendersDir = path.join(config.outputDir, runId, "renders");

  console.log(`Compare stage for run ${runId}`);

  // Look for diff report
  if (fs.existsSync(artifactsDir)) {
    const diffFiles = fs.readdirSync(artifactsDir).filter((f: string) => f.includes("diff_report"));
    if (diffFiles.length > 0) {
      const report = JSON.parse(fs.readFileSync(path.join(artifactsDir, diffFiles[0]), "utf-8"));
      console.log(`  Similarity: ${report.similarity_score ?? "N/A"}`);
      console.log(`  Categories: ${JSON.stringify(report.categories ?? {})}`);
      console.log(`  Mismatches: ${(report.mismatches ?? []).length}`);
      return;
    }
  }

  // Look for screenshots to compare
  const screenshotPath = path.join(rendersDir, "screenshot.png");
  const baselineFlag = args.flags["baseline"];
  if (fs.existsSync(screenshotPath)) {
    if (!baselineFlag) {
      console.log(`  Screenshot found at ${screenshotPath}`);
      console.log(`  No baseline provided — pass --baseline <path.png> to pixel-diff.`);
      return;
    }
    const baselinePath = path.resolve(baselineFlag);
    if (!fs.existsSync(baselinePath)) {
      console.log(`  Baseline not found at ${baselinePath}`);
      return;
    }
    const comparator = new ScreenshotComparator(
      { colorThreshold: 16 },
      { pythonBin: config.pythonBin, pluginDir: config.pluginDir },
    );
    const result = comparator.compare(screenshotPath, baselinePath);
    if (result.identical) {
      console.log("  Screenshots are identical.");
    } else if (result.diffPixelCount < 0) {
      console.log("  Pixel diff failed (decode or size error); images are not identical.");
    } else {
      console.log(`  Similarity: ${result.similarity.toFixed(4)}`);
      console.log(`  Diff pixels: ${result.diffPixelCount} / ${result.totalPixels} (${(result.diffPercentage * 100).toFixed(2)}%)`);
      if (result.ssimClean === true) {
        console.log(`  Perceptually identical: ${result.diffPixelCount} diff pixels are within visual noise (SSIM ${(result.ssim ?? 0).toFixed(4)}).`);
      } else if (result.ssimClean === false) {
        console.log(`  Perceptual change: SSIM ${(result.ssim ?? -1).toFixed(4)}, min-region SSIM ${(result.minRegionSsim ?? -1).toFixed(4)}.`);
      }
    }
  } else {
    console.log("  No diff report or screenshots found. Run the pipeline first.");
  }
}

async function cmdDemo(args: CliArgs): Promise<void> {
  const config = buildConfig(args);
  const outDir = path.resolve(args.flags["out"] ?? args.flags["output-dir"] ?? "./demo-out");
  const renderFlag = args.flags["render"] === "true";
  const cfg = { pythonBin: config.pythonBin, pluginDir: config.pluginDir };

  // Resolve the source: --file (local), --file-key (live), or the checked-in
  // offline fixture when neither is given.
  let source: { file?: string; fileKey?: string };
  if (args.flags["file"]) {
    source = { file: path.resolve(args.flags["file"]) };
  } else if (args.flags["file-key"]) {
    source = { fileKey: args.flags["file-key"] };
  } else {
    source = { file: path.join(config.pluginDir, "fixtures", "figma", "layout_desktop.json") };
    console.log(`No --file or --file-key given — using the offline fixture: ${source.file}`);
  }

  let fileJson: Record<string, unknown>;
  try {
    const ingest = await invokeIngest(cfg, source);
    fileJson = ingest.fileJson;
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    console.error(`error: ${message}`);
    process.exit(message.includes("FIGMA_TOKEN") ? 3 : 1);
  }

  // Generate through every Python backend, collecting the summary rows.
  console.log(`Generating all backends into ${outDir}`);
  const rows: Array<{ backend: string; files: number; losses: number; nodes: number }> = [];
  for (const [key, backend] of Object.entries(TARGET_BACKENDS)) {
    try {
      const result = await invokeBackendGenerator(
        cfg, key, fileJson, outDir, { viewport: config.viewport.width },
      );
      const nodes = new Set<string>();
      for (const f of result.manifest.files) {
        for (const n of f.node_ids) nodes.add(n);
      }
      rows.push({
        backend,
        files: result.manifest.files.length,
        losses: result.manifest.fidelity_losses.length,
        nodes: nodes.size,
      });
      console.log(
        `  ${backend}: ${result.manifest.files.length} file(s), ` +
        `${result.manifest.fidelity_losses.length} loss(es) → ${result.filesDir}`,
      );
    } catch (err) {
      console.error(`error: ${backend} generation failed: ${err instanceof Error ? err.message : String(err)}`);
      process.exit(1);
    }
  }

  // Deterministic summary table.
  const width = Math.max("backend".length, ...rows.map((r) => r.backend.length));
  console.log("\nSummary:");
  console.log(`  ${`backend`.padEnd(width)}  files  losses  nodes`);
  for (const r of rows) {
    console.log(
      `  ${r.backend.padEnd(width)}  ${String(r.files).padStart(5)}  ` +
      `${String(r.losses).padStart(6)}  ${String(r.nodes).padStart(5)}`,
    );
  }

  if (renderFlag) {
    renderDemoWeb(config, outDir);
  }
}

/**
 * Best-effort demo rendering: only the html_css reference output is directly
 * renderable (complete standalone HTML).  React/Vue/Svelte outputs need a
 * bundler, and native targets need simulators.  Any failure degrades to a
 * note — never a hard error.
 */
function renderDemoWeb(config: RuntimeConfig, outDir: string): void {
  const htmlDir = path.join(outDir, "html_css");
  if (!fs.existsSync(htmlDir)) {
    console.log("  --render: no html_css output to render");
    return;
  }
  const htmlFiles = fs.readdirSync(htmlDir).filter((f: string) => f.endsWith(".html"));
  if (htmlFiles.length === 0) {
    console.log("  --render: no html files in the html_css output");
    return;
  }

  const rendersDir = path.join(outDir, "_renders");
  fs.mkdirSync(rendersDir, { recursive: true });
  console.log("\n--render (best-effort):");
  for (const file of htmlFiles) {
    const htmlPath = path.join(htmlDir, file);
    const pngPath = path.join(rendersDir, file.replace(/\.html$/, ".png"));
    const script = buildBrowserRenderScript(htmlPath, pngPath, config.viewport);
    let res: { stdout?: string; stderr?: string; status: number | null };
    try {
      res = spawnSync(config.pythonBin, ["-"], {
        input: script,
        encoding: "utf-8",
        timeout: 60_000,
      });
    } catch {
      console.log(`  render skipped for ${file}: spawn failed`);
      continue;
    }
    const parsed = parseBrowserRenderOutput(res.stdout ?? "");
    if (parsed) {
      console.log(`  rendered ${file} → ${parsed.screenshotPath}`);
      continue;
    }
    const line = (res.stdout ?? "").trim().split("\n").pop() ?? "";
    let reason = line || (res.stderr ?? "").trim() || "unknown failure";
    try {
      const payload = JSON.parse(line) as { error?: string };
      if (payload.error) reason = payload.error;
    } catch { /* keep the raw line */ }
    console.log(`  render skipped for ${file}: ${reason}`);
  }
  console.log("  (react/vue/svelte outputs require a bundler; native targets need simulators — not rendered)");
}

async function cmdRepair(args: CliArgs): Promise<void> {
  const config = buildConfig(args);
  const runId = args.flags["run-id"] ?? config.runId;
  const artifactsDir = path.join(config.outputDir, runId, "artifacts");

  console.log(`Repair stage for run ${runId}`);

  // Look for diff report to repair from
  if (fs.existsSync(artifactsDir)) {
    const diffFiles = fs.readdirSync(artifactsDir).filter((f: string) => f.includes("diff_report"));
    if (diffFiles.length > 0) {
      const report = JSON.parse(fs.readFileSync(path.join(artifactsDir, diffFiles[0]), "utf-8"));
      const mismatches = report.mismatches ?? [];
      console.log(`  Found diff report with ${mismatches.length} mismatches`);
      console.log(`  Similarity: ${report.similarity_score ?? "N/A"}`);

      if (mismatches.length === 0) {
        console.log("  No mismatches to repair — render matches design.");
        return;
      }

      // Categorize mismatches
      const categories: Record<string, number> = {};
      for (const m of mismatches) {
        const cat = m.type ?? "unknown";
        categories[cat] = (categories[cat] ?? 0) + 1;
      }
      console.log(`  Categories: ${JSON.stringify(categories)}`);
      console.log(`  Run 'figmaforge run' for full repair loop with patch generation.`);
      return;
    }
  }

  console.log("  No diff report found. Run the pipeline first with 'figmaforge run'.");
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

async function main(): Promise<void> {
  await runMainForTests(process.argv);
}

export async function runMainForTests(argv: string[]): Promise<void> {
  const args = parseArgs(argv);

  switch (args.command) {
    case "run":
      await cmdRun(args);
      break;
    case "inspect":
      await cmdInspect(args);
      break;
    case "replay":
      await cmdReplay(args);
      break;
    case "render":
      await cmdRender(args);
      break;
    case "compare":
      await cmdCompare(args);
      break;
    case "repair":
      await cmdRepair(args);
      break;
    case "demo":
      await cmdDemo(args);
      break;
    case "help":
    case "--help":
    case "-h":
      printHelp();
      break;
    default:
      console.error(`Unknown command: ${args.command}`);
      printHelp();
      process.exit(1);
  }
}

if (process.argv[1] !== undefined && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((err) => {
    console.error("Fatal error:", err);
    process.exit(1);
  });
}
