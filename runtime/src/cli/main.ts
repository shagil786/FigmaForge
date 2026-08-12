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
import { PIPELINE_STAGES, DEFAULT_CONFIG, DEFAULT_BUDGETS, DEFAULT_RETRY, makeRunId } from "../core/types.js";
import type { RuntimeConfig, PipelineStage } from "../core/types.js";
import { EventLog } from "../core/events.js";
import { CheckpointManager } from "../core/checkpoint.js";
import { ArtifactStore } from "../core/artifacts.js";
import { ToolRegistry } from "../core/tools.js";
import { BudgetTracker } from "../core/budget.js";
import { PipelineCoordinator } from "../core/pipeline.js";

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

Options:
  --file-key=<key>         Figma file key (required for 'run')
  --output-dir=<path>      Output directory (default: ./figmaforge-output)
  --run-id=<id>            Run ID (auto-generated if not specified)
  --resume                 Resume from latest checkpoint
  --threshold=<0.0-1.0>    Similarity threshold (default: 0.95)
  --max-iterations=<n>     Max pipeline iterations (default: 20)
  --max-repair=<n>         Max repair iterations (default: 10)
  --max-time=<ms>          Max time in milliseconds (default: 300000)
  --viewport=<WxH>         Viewport size (default: 1440x900)
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
  const threshold = parseFloat(args.flags["threshold"] ?? "0.95");
  const maxIterations = parseInt(args.flags["max-iterations"] ?? "20", 10);
  const maxRepair = parseInt(args.flags["max-repair"] ?? "10", 10);
  const maxTime = parseInt(args.flags["max-time"] ?? "300000", 10);

  const viewportStr = args.flags["viewport"] ?? "1440x900";
  const [w, h] = viewportStr.split("x").map(Number);

  const approvedDirs = [outputDir];
  if (args.flags["approve-dir"]) {
    approvedDirs.push(path.resolve(args.flags["approve-dir"]));
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
    pythonBin: DEFAULT_CONFIG.pythonBin,
    pluginDir: path.resolve(args.flags["plugin-dir"] ?? "./plugin/figmaforge"),
  };
}

// ---------------------------------------------------------------------------
// Commands
// ---------------------------------------------------------------------------

async function cmdRun(args: CliArgs): Promise<void> {
  const config = buildConfig(args);

  if (!config.fileKey) {
    console.error("Error: --file-key is required for 'run' command");
    process.exit(1);
  }

  console.log(`Starting pipeline run ${config.runId}`);
  console.log(`  File key: ${config.fileKey}`);
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

  const pipeline = new PipelineCoordinator(
    config, events, checkpoints, artifacts, tools, budget, approvalCallback,
  );

  // Set up cancellation
  const ac = new AbortController();
  process.on("SIGINT", () => {
    console.log("\nCancelling...");
    ac.abort();
  });
  pipeline.setAbortSignal(ac.signal);

  const result = await pipeline.run();

  console.log(`\nPipeline ${result.status}`);
  console.log(`  Duration:   ${result.totalDurationMs}ms`);
  console.log(`  Score:      ${result.similarityScore}`);
  console.log(`  Repairs:    ${result.repairIterations}`);
  console.log(`  Tokens:     ${result.tokensUsed}`);
  console.log(`  Artifacts:  ${result.artifacts}`);
  console.log(`  Events:     ${result.events}`);

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
  console.log("Render command: runs the render stage only.");
  console.log("This requires a previous run with generated code artifacts.");
  const config = buildConfig(args);
  console.log(`Run ID: ${config.runId}, Output: ${config.outputDir}`);
  // TODO: Implement single-stage execution
}

async function cmdCompare(args: CliArgs): Promise<void> {
  console.log("Compare command: runs the compare stage only.");
  console.log("This requires a previous run with render artifacts.");
  const config = buildConfig(args);
  console.log(`Run ID: ${config.runId}, Output: ${config.outputDir}`);
  // TODO: Implement single-stage execution
}

async function cmdRepair(args: CliArgs): Promise<void> {
  console.log("Repair command: runs the repair stage only.");
  console.log("This requires a previous run with diff report artifacts.");
  const config = buildConfig(args);
  console.log(`Run ID: ${config.runId}, Output: ${config.outputDir}`);
  // TODO: Implement single-stage execution
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

async function main(): Promise<void> {
  const args = parseArgs(process.argv);

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

main().catch((err) => {
  console.error("Fatal error:", err);
  process.exit(1);
});
