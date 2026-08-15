/**
 * Comprehensive tests for all FigmaForge runtime modules.
 *
 * Covers: types, events, checkpoints, artifacts, tools, state machine,
 * budget tracker, retry logic, security, pipeline, evaluation.
 */

import * as fs from "node:fs";
import * as path from "node:path";
import * as os from "node:os";
import { spawnSync } from "node:child_process";
import { pathToFileURL } from "node:url";
import * as zlib from "node:zlib";

import { describe, it, assert, assertEqual, assertThrows, assertRejects, assertGreaterThan, assertLessOrEqual } from "./test_framework.js";
import type { SuiteResult } from "./test_framework.js";

import { PIPELINE_STAGES, STAGE_INDEX, makeRunId, makeTaskId, NullModelProvider } from "../src/core/types.js";
import { EventLog } from "../src/core/events.js";
import { CheckpointManager, EMPTY_METRICS } from "../src/core/checkpoint.js";
import { ArtifactStore } from "../src/core/artifacts.js";
import { ToolRegistry } from "../src/core/tools.js";
import { StateMachine, createInitialState } from "../src/core/state.js";
import { BudgetTracker, BudgetExceededError } from "../src/core/budget.js";
import { withRetry, withTimeout, RetryExhaustedError, CancelledError } from "../src/core/retry.js";
import { PathSandbox, SecretGuard, ShellGuard, AssetValidator, ApprovalGate, SecurityViolation } from "../src/core/security.js";
import { PipelineCoordinator } from "../src/core/pipeline.js";
import { compareSnapshot, saveSnapshot, loadFixtures, injectFailure } from "../src/core/evaluation.js";
import { createProvider, AnthropicProvider, OpenAIProvider } from "../src/core/providers.js";
import { ScreenshotComparator, parsePixelDiffOutput } from "../src/core/screenshot_compare.js";
import { vnodeToHtml, buildBrowserRenderScript, parseBrowserRenderOutput, pickLayoutMeta } from "../src/core/render_handler.js";
import { runBackendCodegenTests } from "./backend_codegen.test.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function tmpDir(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), "ff-test-"));
}

function cleanDir(dir: string): void {
  if (fs.existsSync(dir)) {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

// --- Filter-0 PNG generator for comparator tests (Part 12) ----------------

let CRC_TABLE: Uint32Array | null = null;

function crc32(data: Buffer): number {
  if (!CRC_TABLE) {
    CRC_TABLE = new Uint32Array(256);
    for (let n = 0; n < 256; n++) {
      let c = n;
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      CRC_TABLE[n] = c >>> 0;
    }
  }
  let c = 0xffffffff;
  for (let i = 0; i < data.length; i++) {
    c = CRC_TABLE[(c ^ data[i]) & 0xff] ^ (c >>> 8);
  }
  return (c ^ 0xffffffff) >>> 0;
}

function pngChunk(type: string, data: Buffer): Buffer {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length);
  const typeBuf = Buffer.from(type, "ascii");
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(Buffer.concat([typeBuf, data])));
  return Buffer.concat([len, typeBuf, data, crc]);
}

interface Rect { x: number; y: number; w: number; h: number; rgb: [number, number, number]; }

function makePng(width: number, height: number, fill: [number, number, number], rect?: Rect): Buffer {
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8;   // bit depth
  ihdr[9] = 2;   // color type RGB
  const rows: Buffer[] = [];
  for (let y = 0; y < height; y++) {
    const row = Buffer.alloc(1 + width * 3);
    row[0] = 0;  // filter 0
    for (let x = 0; x < width; x++) {
      const inRect = rect !== undefined
        && x >= rect.x && x < rect.x + rect.w
        && y >= rect.y && y < rect.y + rect.h;
      const [r, g, b] = inRect ? rect.rgb : fill;
      row[1 + x * 3] = r;
      row[2 + x * 3] = g;
      row[3 + x * 3] = b;
    }
    rows.push(row);
  }
  const idat = zlib.deflateSync(Buffer.concat(rows));
  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    pngChunk("IHDR", ihdr),
    pngChunk("IDAT", idat),
    pngChunk("IEND", Buffer.alloc(0)),
  ]);
}

// ---------------------------------------------------------------------------
// Test suites
// ---------------------------------------------------------------------------

export async function runAllTests(): Promise<SuiteResult[]> {
  const results: SuiteResult[] = [];

  // 1. Types
  results.push(await describe("types", async () => {
    await it("PIPELINE_STAGES has 10 stages", async () => {
      assertEqual(PIPELINE_STAGES.length, 10);
    });

    await it("STAGE_INDEX maps each stage to its index", async () => {
      assertEqual(STAGE_INDEX["ingest"], 0);
      assertEqual(STAGE_INDEX["verify"], 9);
      assertEqual(STAGE_INDEX["repair"], 8);
    });

    await it("makeRunId generates unique IDs", async () => {
      const a = makeRunId("test");
      const b = makeRunId("test2");
      assertEqual(a, "run-test");
      assertEqual(b, "run-test2");
      assert(a !== b, "IDs should differ");
    });

    await it("makeRunId without seed uses timestamp", async () => {
      const id = makeRunId();
      assert(id.startsWith("run-"), `Expected run- prefix: ${id}`);
    });

    await it("makeTaskId includes run, stage, and attempt", async () => {
      const id = makeTaskId("run-1", "layout", 2);
      assertEqual(id, "run-1:layout:2");
    });

    await it("NullModelProvider returns empty result", async () => {
      const provider = new NullModelProvider();
      assertEqual(provider.name, "null");
      const result = await provider.complete("test");
      assertEqual(result.text, "");
      assertEqual(result.tokensUsed, 0);
    });
  }));

  // 2. Events
  results.push(await describe("events", async () => {
    await it("EventLog emits events with sequential numbers", async () => {
      const log = new EventLog("run-1");
      const e1 = log.emit("run_started", "started");
      const e2 = log.emit("stage_started", "stage", { stage: "ingest" });
      assertEqual(e1.seq, 0);
      assertEqual(e2.seq, 1);
      assertEqual(log.length, 2);
    });

    await it("EventLog filters by kind", async () => {
      const log = new EventLog("run-1");
      log.emit("run_started", "a");
      log.emit("stage_started", "b", { stage: "ingest" });
      log.emit("stage_started", "c", { stage: "normalize" });
      const stageEvents = log.byKind("stage_started");
      assertEqual(stageEvents.length, 2);
    });

    await it("EventLog filters by stage", async () => {
      const log = new EventLog("run-1");
      log.emit("stage_started", "a", { stage: "ingest" });
      log.emit("stage_completed", "b", { stage: "ingest" });
      log.emit("stage_started", "c", { stage: "normalize" });
      assertEqual(log.byStage("ingest").length, 2);
      assertEqual(log.byStage("normalize").length, 1);
    });

    await it("EventLog filters by level", async () => {
      const log = new EventLog("run-1");
      log.emit("run_started", "a", { level: "info" });
      log.emit("stage_failed", "b", { level: "error" });
      log.emit("retry_attempt", "c", { level: "warn" });
      assertEqual(log.byLevel("error").length, 1);
      assertEqual(log.byLevel("warn").length, 2); // warn + error
    });

    await it("EventLog serializes and restores", async () => {
      const log = new EventLog("run-1");
      log.emit("run_started", "started");
      log.emit("stage_started", "ingest", { stage: "ingest" });
      const json = log.toJSON();
      const restored = EventLog.fromJSON("run-1", json);
      assertEqual(restored.length, 2);
      assertEqual(restored.all()[0].message, "started");
    });

    await it("EventLog events have timestamps", async () => {
      const log = new EventLog("run-1");
      const e = log.emit("run_started", "test");
      assert(e.timestamp.length > 0, "Timestamp should be set");
      assert(e.timestamp.includes("T"), "Timestamp should be ISO format");
    });
  }));

  // 3. Checkpoints
  results.push(await describe("checkpoints", async () => {
    const dir = tmpDir();
    try {
      await it("CheckpointManager saves and loads checkpoints", async () => {
        const mgr = new CheckpointManager("run-1", dir);
        mgr.init();
        mgr.save("ingest", { data: "test" }, { ...EMPTY_METRICS, similarityScore: 0.5 });
        const cp = mgr.load("ingest");
        assert(cp !== null, "Checkpoint should exist");
        assertEqual(cp!.runId, "run-1");
        assertEqual(cp!.stage, "ingest");
        assertEqual(cp!.nextStage, "normalize");
      });

      await it("loadLatest returns the most advanced checkpoint", async () => {
        const mgr = new CheckpointManager("run-2", dir);
        mgr.save("ingest", {}, EMPTY_METRICS);
        mgr.save("normalize", {}, EMPTY_METRICS);
        mgr.save("resolve", {}, EMPTY_METRICS);
        const latest = mgr.loadLatest();
        assert(latest !== null);
        assertEqual(latest!.stage, "resolve");
        assertEqual(latest!.nextStage, "layout");
      });

      await it("load returns null for missing checkpoint", async () => {
        const mgr = new CheckpointManager("run-3", dir);
        const cp = mgr.load("layout");
        assertEqual(cp, null);
      });

      await it("isCompleted checks checkpoint existence", async () => {
        const mgr = new CheckpointManager("run-4", dir);
        assertEqual(mgr.isCompleted("ingest"), false);
        mgr.save("ingest", {}, EMPTY_METRICS);
        assertEqual(mgr.isCompleted("ingest"), true);
      });

      await it("list returns all checkpoints", async () => {
        const mgr = new CheckpointManager("run-5", dir);
        mgr.save("ingest", {}, EMPTY_METRICS);
        mgr.save("normalize", {}, EMPTY_METRICS);
        const list = mgr.list();
        assertEqual(list.length, 2);
      });

      await it("clear removes all checkpoints", async () => {
        const mgr = new CheckpointManager("run-6", dir);
        mgr.save("ingest", {}, EMPTY_METRICS);
        mgr.clear();
        assertEqual(mgr.list().length, 0);
      });

      await it("checkpoint stores metrics", async () => {
        const mgr = new CheckpointManager("run-7", dir);
        const metrics = { ...EMPTY_METRICS, tokensUsed: 500, similarityScore: 0.85 };
        mgr.save("layout", {}, metrics);
        const cp = mgr.load("layout");
        assertEqual(cp!.metrics.tokensUsed, 500);
        assertEqual(cp!.metrics.similarityScore, 0.85);
      });
    } finally {
      cleanDir(dir);
    }
  }));

  // 4. Artifacts
  results.push(await describe("artifacts", async () => {
    const dir = tmpDir();
    try {
      await it("ArtifactStore stores JSON artifacts", async () => {
        const store = new ArtifactStore("run-1", dir);
        const artifact = store.storeJSON("design_ir", "normalize", "ir", { nodes: [] });
        assertEqual(artifact.kind, "design_ir");
        assertEqual(artifact.stage, "normalize");
        assertGreaterThan(artifact.size, 0);
        assert(store.count > 0);
      });

      await it("ArtifactStore stores buffer artifacts", async () => {
        const store = new ArtifactStore("run-2", dir);
        const buf = Buffer.from("fake png data");
        const artifact = store.storeBuffer("screenshot", "render", "screenshot", buf);
        assertEqual(artifact.kind, "screenshot");
        assertEqual(artifact.size, buf.length);
      });

      await it("ArtifactStore loads JSON artifacts", async () => {
        const store = new ArtifactStore("run-3", dir);
        const data = { test: "value", num: 42 };
        const artifact = store.storeJSON("design_ir", "normalize", "test", data);
        const loaded = store.loadJSON(artifact);
        assertEqual((loaded as Record<string, unknown>).test, "value");
        assertEqual((loaded as Record<string, number>).num, 42);
      });

      await it("ArtifactStore filters by stage and kind", async () => {
        const store = new ArtifactStore("run-4", dir);
        store.storeJSON("design_ir", "normalize", "a", {});
        store.storeJSON("layout_plan", "layout", "b", {});
        store.storeJSON("design_ir", "normalize", "c", {});
        assertEqual(store.byStage("normalize").length, 2);
        assertEqual(store.byKind("layout_plan").length, 1);
      });

      await it("ArtifactStore generates manifest", async () => {
        const store = new ArtifactStore("run-5", dir);
        store.storeJSON("design_ir", "normalize", "a", {});
        const manifest = store.manifest();
        assertEqual(manifest.runId, "run-5");
        assertEqual(manifest.artifacts.length, 1);
      });

      await it("ArtifactStore tracks total size", async () => {
        const store = new ArtifactStore("run-6", dir);
        store.storeJSON("design_ir", "normalize", "a", { data: "hello" });
        assertGreaterThan(store.totalSize, 0);
      });
    } finally {
      cleanDir(dir);
    }
  }));

  // 5. Tool Registry
  results.push(await describe("tools", async () => {
    await it("ToolRegistry registers and retrieves tools", async () => {
      const registry = new ToolRegistry();
      registry.register({
        name: "test.tool",
        description: "A test tool",
        stage: "ingest",
        isModelAssisted: false,
        execute: async () => ({ result: "ok" }),
      });
      const tool = registry.get("test.tool");
      assert(tool !== undefined);
      assertEqual(tool!.name, "test.tool");
    });

    await it("ToolRegistry throws on duplicate registration", async () => {
      const registry = new ToolRegistry();
      const tool = {
        name: "dup",
        description: "",
        stage: "ingest" as const,
        isModelAssisted: false,
        execute: async () => ({}),
      };
      registry.register(tool);
      assertThrows(() => registry.register(tool), "already registered");
    });

    await it("ToolRegistry invokes tools and tracks invocations", async () => {
      const registry = new ToolRegistry();
      registry.register({
        name: "compute",
        description: "computes something",
        stage: "layout",
        isModelAssisted: false,
        execute: async (input) => ({ sum: (input.a as number) + (input.b as number) }),
      });
      const result = await registry.invoke("compute", { a: 3, b: 4 }, {
        runId: "run-1", outputDir: "/tmp", pluginDir: ".", pythonBin: "python3",
      });
      assertEqual(result.sum, 7);
      assertEqual(registry.getInvocations().length, 1);
    });

    await it("ToolRegistry records failed invocations", async () => {
      const registry = new ToolRegistry();
      registry.register({
        name: "fail",
        description: "always fails",
        stage: "ingest",
        isModelAssisted: false,
        execute: async () => { throw new Error("boom"); },
      });
      await assertRejects(
        () => registry.invoke("fail", {}, {
          runId: "run-1", outputDir: "/tmp", pluginDir: ".", pythonBin: "python3",
        }),
        "boom",
      );
      const inv = registry.getInvocations()[0];
      assertEqual(inv.error, "boom");
    });

    await it("ToolRegistry lists tools by stage", async () => {
      const registry = new ToolRegistry();
      registry.register({ name: "a", description: "", stage: "ingest", isModelAssisted: false, execute: async () => ({}) });
      registry.register({ name: "b", description: "", stage: "layout", isModelAssisted: false, execute: async () => ({}) });
      registry.register({ name: "c", description: "", stage: "ingest", isModelAssisted: false, execute: async () => ({}) });
      assertEqual(registry.listByStage("ingest").length, 2);
      assertEqual(registry.listByStage("layout").length, 1);
    });
  }));

  // 6. State Machine
  results.push(await describe("state machine", async () => {
    const dir = tmpDir();
    try {
      await it("starts in pending state", async () => {
        const events = new EventLog("run-1");
        const checkpoints = new CheckpointManager("run-1", dir);
        const sm = new StateMachine(events, checkpoints, "run-1");
        assertEqual(sm.state.status, "pending");
        assertEqual(sm.state.currentStage, null);
      });

      await it("transitions through lifecycle", async () => {
        const events = new EventLog("run-1");
        const checkpoints = new CheckpointManager("run-1", dir);
        const sm = new StateMachine(events, checkpoints, "run-1");
        sm.start();
        assertEqual(sm.state.status, "running");
        sm.beginStage("ingest");
        assertEqual(sm.state.currentStage, "ingest");
        sm.completeStage("ingest", { data: "test" });
        assertEqual(sm.state.completedStages.length, 1);
        assertEqual(sm.state.currentStage, null);
      });

      await it("enforces stage order", async () => {
        const events = new EventLog("run-1");
        const checkpoints = new CheckpointManager("run-1", dir);
        const sm = new StateMachine(events, checkpoints, "run-1");
        sm.start();
        assertThrows(() => sm.beginStage("layout"), 'Expected stage "ingest"');
      });

      await it("supports pause and resume", async () => {
        const events = new EventLog("run-1");
        const checkpoints = new CheckpointManager("run-1", dir);
        const sm = new StateMachine(events, checkpoints, "run-1");
        sm.start();
        sm.pause("need approval");
        assertEqual(sm.state.status, "paused");
        sm.resume();
        assertEqual(sm.state.status, "running");
      });

      await it("supports cancel", async () => {
        const events = new EventLog("run-1");
        const checkpoints = new CheckpointManager("run-1", dir);
        const sm = new StateMachine(events, checkpoints, "run-1");
        sm.start();
        sm.cancel();
        assertEqual(sm.state.status, "cancelled");
      });

      await it("supports fail", async () => {
        const events = new EventLog("run-1");
        const checkpoints = new CheckpointManager("run-1", dir);
        const sm = new StateMachine(events, checkpoints, "run-1");
        sm.start();
        sm.fail("something broke");
        assertEqual(sm.state.status, "failed");
      });

      await it("resumes from checkpoint", async () => {
        const events = new EventLog("run-1");
        const checkpoints = new CheckpointManager("run-1", dir);
        const sm = new StateMachine(events, checkpoints, "run-1");
        sm.start();
        sm.beginStage("ingest");
        sm.completeStage("ingest", {});
        sm.beginStage("normalize");
        sm.completeStage("normalize", {});

        // Create a new state machine and resume
        const events2 = new EventLog("run-1");
        const sm2 = new StateMachine(events2, checkpoints, "run-1");
        const nextStage = sm2.resumeFromCheckpoint();
        assertEqual(nextStage, "resolve");
        assertEqual(sm2.state.completedStages.length, 2);
      });

      await it("nextStage returns correct next stage", async () => {
        const events = new EventLog("run-1");
        const checkpoints = new CheckpointManager("run-1", dir);
        const sm = new StateMachine(events, checkpoints, "run-1");
        assertEqual(sm.nextStage("ingest"), "normalize");
        assertEqual(sm.nextStage("verify"), "done");
      });

      await it("records retry attempts", async () => {
        const events = new EventLog("run-1");
        const checkpoints = new CheckpointManager("run-1", dir);
        const sm = new StateMachine(events, checkpoints, "run-1");
        sm.start();
        sm.beginStage("ingest");
        sm.retryAttempt("ingest", 1, "timeout");
        assertEqual(sm.state.currentAttempt, 1);
        const retryEvents = events.byKind("retry_attempt");
        assertEqual(retryEvents.length, 1);
      });
    } finally {
      cleanDir(dir);
    }
  }));

  // 7. Budget Tracker
  results.push(await describe("budget", async () => {
    await it("tracks token usage", async () => {
      const tracker = new BudgetTracker({ maxTokens: 1000, maxTimeMs: 60000, maxIterations: 10, maxRepairIterations: 5 });
      tracker.addTokens(500);
      assertEqual(tracker.current.tokensUsed, 500);
      tracker.checkTokens(); // should not throw
    });

    await it("throws when token budget exceeded", async () => {
      const tracker = new BudgetTracker({ maxTokens: 100, maxTimeMs: 60000, maxIterations: 10, maxRepairIterations: 5 });
      tracker.addTokens(150);
      assertThrows(() => tracker.checkTokens(), "tokens");
    });

    await it("tracks iterations", async () => {
      const tracker = new BudgetTracker({ maxTokens: 1000, maxTimeMs: 60000, maxIterations: 3, maxRepairIterations: 5 });
      tracker.addIteration();
      tracker.addIteration();
      tracker.addIteration();
      tracker.checkIterations(); // should not throw
      tracker.addIteration();
      assertThrows(() => tracker.checkIterations(), "iterations");
    });

    await it("tracks repair iterations", async () => {
      const tracker = new BudgetTracker({ maxTokens: 1000, maxTimeMs: 60000, maxIterations: 10, maxRepairIterations: 2 });
      tracker.addRepairIteration();
      tracker.addRepairIteration();
      tracker.checkRepairIterations(); // should not throw
      tracker.addRepairIteration();
      assertThrows(() => tracker.checkRepairIterations(), "repair_iterations");
    });

    await it("calculates remaining budget", async () => {
      const tracker = new BudgetTracker({ maxTokens: 1000, maxTimeMs: 60000, maxIterations: 10, maxRepairIterations: 5 });
      tracker.addTokens(500);
      const remaining = tracker.remaining();
      assertEqual(remaining.tokens, 0.5);
      assertLessOrEqual(remaining.time, 1.0);
    });

    await it("restores state from checkpoint", async () => {
      const tracker = new BudgetTracker({ maxTokens: 1000, maxTimeMs: 60000, maxIterations: 10, maxRepairIterations: 5 });
      tracker.restore({ tokensUsed: 300, iterationsUsed: 5, repairIterations: 2 });
      assertEqual(tracker.current.tokensUsed, 300);
      assertEqual(tracker.current.iterationsUsed, 5);
    });

    await it("BudgetExceededError has correct properties", async () => {
      const err = new BudgetExceededError("tokens", 100, 150);
      assertEqual(err.dimension, "tokens");
      assertEqual(err.limit, 100);
      assertEqual(err.used, 150);
      assert(err.message.includes("tokens"));
    });
  }));

  // 8. Retry Logic
  results.push(await describe("retry", async () => {
    await it("succeeds on first attempt", async () => {
      const result = await withRetry(
        async () => 42,
        "test",
        { maxAttempts: 3, baseDelayMs: 10, maxDelayMs: 100, backoffMultiplier: 2 },
      );
      assertEqual(result.value, 42);
      assertEqual(result.attempts, 1);
    });

    await it("retries on failure and succeeds", async () => {
      let attempt = 0;
      const result = await withRetry(
        async () => {
          attempt++;
          if (attempt < 3) throw new Error("fail");
          return "ok";
        },
        "test",
        { maxAttempts: 3, baseDelayMs: 10, maxDelayMs: 100, backoffMultiplier: 2 },
      );
      assertEqual(result.value, "ok");
      assertEqual(result.attempts, 3);
    });

    await it("throws RetryExhaustedError after max attempts", async () => {
      await assertRejects(
        () => withRetry(
          async () => { throw new Error("always fails"); },
          "test",
          { maxAttempts: 2, baseDelayMs: 10, maxDelayMs: 100, backoffMultiplier: 2 },
        ),
        "Retry exhausted",
      );
    });

    await it("supports cancellation via AbortSignal", async () => {
      const ac = new AbortController();
      ac.abort();
      await assertRejects(
        () => withRetry(
          async () => { throw new Error("fail"); },
          "test",
          { maxAttempts: 3, baseDelayMs: 10, maxDelayMs: 100, backoffMultiplier: 2 },
          ac.signal,
        ),
        "cancelled",
      );
    });

    await it("withTimeout resolves within limit", async () => {
      const result = await withTimeout(
        async () => "fast",
        1000,
        "test",
      );
      assertEqual(result, "fast");
    });

    await it("withTimeout rejects on timeout", async () => {
      await assertRejects(
        () => withTimeout(
          () => new Promise((resolve) => setTimeout(resolve, 5000)),
          50,
          "slow-op",
        ),
        "timeout",
      );
    });

    await it("calls onRetry callback", async () => {
      const retries: number[] = [];
      let attempt = 0;
      await withRetry(
        async () => {
          attempt++;
          if (attempt < 3) throw new Error("fail");
          return "ok";
        },
        "test",
        { maxAttempts: 3, baseDelayMs: 10, maxDelayMs: 100, backoffMultiplier: 2 },
        undefined,
        (attempt, _delay, _error) => { retries.push(attempt); },
      );
      assertEqual(retries.length, 2);
      assertEqual(retries[0], 1);
      assertEqual(retries[1], 2);
    });
  }));

  // 9. Security
  results.push(await describe("security", async () => {
    const dir = tmpDir();
    try {
      await it("PathSandbox allows paths within approved dirs", async () => {
        const sandbox = new PathSandbox([dir]);
        sandbox.assertAllowed(path.join(dir, "file.txt"));
        assert(sandbox.isAllowed(path.join(dir, "sub", "file.txt")));
      });

      await it("PathSandbox blocks paths outside approved dirs", async () => {
        const sandbox = new PathSandbox([dir]);
        assertThrows(
          () => sandbox.assertAllowed("/etc/passwd"),
          "not within approved",
        );
        assert(!sandbox.isAllowed("/etc/passwd"));
      });

      await it("PathSandbox can approve new dirs at runtime", async () => {
        const sandbox = new PathSandbox([dir]);
        const newDir = path.join(dir, "new");
        fs.mkdirSync(newDir, { recursive: true });
        sandbox.approve(newDir);
        sandbox.assertAllowed(path.join(newDir, "file.txt"));
      });

      await it("PathSandbox readFileSync works for allowed paths", async () => {
        const testFile = path.join(dir, "test.txt");
        fs.writeFileSync(testFile, "hello");
        const sandbox = new PathSandbox([dir]);
        const content = sandbox.readFileSync(testFile);
        assertEqual(content, "hello");
      });

      await it("SecretGuard detects secrets", async () => {
        const guard = new SecretGuard();
        assert(guard.containsSecrets("api_key=sk-1234567890abcdef"));
        assert(guard.containsSecrets("Bearer eyJhbGciOiJIUzI1NiJ9.test.signature"));
        assert(!guard.containsSecrets("just a normal string"));
      });

      await it("SecretGuard redacts secrets", async () => {
        const guard = new SecretGuard();
        const redacted = guard.redact("api_key=sk-1234567890abcdef");
        assert(redacted.includes("[REDACTED]"), `Expected redaction: ${redacted}`);
        assert(!redacted.includes("sk-1234567890abcdef"));
      });

      await it("SecretGuard redacts objects deeply", async () => {
        const guard = new SecretGuard();
        const obj = { key: "api_key=sk-1234567890abcdef", nested: { token: "ghp_abcdefghijklmnop" } };
        const redacted = guard.redactObject(obj) as Record<string, unknown>;
        assert(typeof redacted.key === "string");
        assert((redacted.key as string).includes("[REDACTED]"));
      });

      await it("ShellGuard allows approved commands", async () => {
        const guard = new ShellGuard();
        assert(guard.isAllowed("python3"));
        assert(guard.isAllowed("node"));
        assert(!guard.isAllowed("rm"));
      });

      await it("ShellGuard blocks dangerous arguments", async () => {
        const guard = new ShellGuard();
        assertThrows(
          () => guard.assertAllowed("python3", ["script.py; rm -rf /"]),
          "Dangerous argument",
        );
      });

      await it("AssetValidator validates files", async () => {
        const validator = new AssetValidator();
        const testFile = path.join(dir, "test.json");
        fs.writeFileSync(testFile, '{"valid": true}');
        const result = validator.validateFile(testFile);
        assert(result.valid, result.error);
      });

      await it("AssetValidator rejects missing files", async () => {
        const validator = new AssetValidator();
        const result = validator.validateFile(path.join(dir, "nonexistent.json"));
        assert(!result.valid);
      });

      await it("AssetValidator rejects empty files", async () => {
        const validator = new AssetValidator();
        const emptyFile = path.join(dir, "empty.json");
        fs.writeFileSync(emptyFile, "");
        const result = validator.validateFile(emptyFile);
        assert(!result.valid, "Should reject empty file");
      });

      await it("ApprovalGate grants pre-approved actions", async () => {
        const gate = new ApprovalGate();
        gate.preApprove("write_file");
        await gate.assertApproved({
          action: "write_file",
          description: "Write a file",
          affectedFiles: [],
        });
      });

      await it("ApprovalGate denies without callback", async () => {
        const gate = new ApprovalGate();
        await assertRejects(
          () => gate.assertApproved({
            action: "write_file",
            description: "Write a file",
            affectedFiles: ["/tmp/test.txt"],
          }),
          "requires approval",
        );
      });

      await it("ApprovalGate uses callback for approval", async () => {
        const gate = new ApprovalGate(async () => true);
        await gate.assertApproved({
          action: "write_file",
          description: "Write a file",
          affectedFiles: [],
        });
      });

      await it("ApprovalGate denies when callback returns false", async () => {
        const gate = new ApprovalGate(async () => false);
        await assertRejects(
          () => gate.assertApproved({
            action: "write_file",
            description: "Write a file",
            affectedFiles: [],
          }),
          "denied",
        );
      });
    } finally {
      cleanDir(dir);
    }
  }));

  // 10. Pipeline
  results.push(await describe("pipeline", async () => {
    const dir = tmpDir();
    try {
      await it("runs a full pipeline with no-op handlers", async () => {
        const config = {
          runId: "run-test",
          fileKey: "test-key",
          outputDir: dir,
          approvedDirs: [dir],
          requireApproval: false,
          retry: { maxAttempts: 1, baseDelayMs: 10, maxDelayMs: 100, backoffMultiplier: 2 },
          budgets: { maxTokens: 10000, maxTimeMs: 30000, maxIterations: 100, maxRepairIterations: 10 },
          similarityThreshold: 0.95,
          minProgress: 0.005,
          viewport: { width: 1440, height: 900 },
          pythonBin: "python3",
          pluginDir: ".",
          target: { framework: "html", styling: "css" },
        };

        const events = new EventLog(config.runId);
        const checkpoints = new CheckpointManager(config.runId, config.outputDir);
        const artifacts = new ArtifactStore(config.runId, config.outputDir);
        const tools = new ToolRegistry();
        const budget = new BudgetTracker(config.budgets);

        const pipeline = new PipelineCoordinator(config, events, checkpoints, artifacts, tools, budget);

        // Register no-op handlers for all stages
        for (const stage of PIPELINE_STAGES) {
          pipeline.onStage(stage, async (_ctx, input) => ({
            stage: input.stage,
            status: "ok",
          }));
        }

        const result = await pipeline.run();
        assertEqual(result.status, "completed");
        assertGreaterThan(result.artifacts, 0);
        assertGreaterThan(result.events, 0);
        assertEqual(result.errors.length, 0);
      });

      await it("handles stage failure", async () => {
        const config = {
          runId: "run-fail",
          fileKey: "test-key",
          outputDir: dir,
          approvedDirs: [dir],
          requireApproval: false,
          retry: { maxAttempts: 1, baseDelayMs: 10, maxDelayMs: 100, backoffMultiplier: 2 },
          budgets: { maxTokens: 10000, maxTimeMs: 30000, maxIterations: 100, maxRepairIterations: 10 },
          similarityThreshold: 0.95,
          minProgress: 0.005,
          viewport: { width: 1440, height: 900 },
          pythonBin: "python3",
          pluginDir: ".",
          target: { framework: "html", styling: "css" },
        };

        const events = new EventLog(config.runId);
        const checkpoints = new CheckpointManager(config.runId, config.outputDir);
        const artifacts = new ArtifactStore(config.runId, config.outputDir);
        const tools = new ToolRegistry();
        const budget = new BudgetTracker(config.budgets);

        const pipeline = new PipelineCoordinator(config, events, checkpoints, artifacts, tools, budget);

        // Make ingest fail
        pipeline.onStage("ingest", async () => { throw new Error("ingest failed"); });

        const result = await pipeline.run();
        assertEqual(result.status, "failed");
        assertGreaterThan(result.errors.length, 0);
      });
    } finally {
      cleanDir(dir);
    }
  }));

  // 11. Evaluation
  results.push(await describe("evaluation", async () => {
    const dir = tmpDir();
    try {
      await it("compareSnapshot detects matching snapshots", async () => {
        const data = { nodes: [1, 2, 3] };
        const snapshotPath = path.join(dir, "snapshot.json");
        saveSnapshot(data, snapshotPath);
        const diff = compareSnapshot(data, snapshotPath);
        assert(diff.matches, "Snapshot should match");
      });

      await it("compareSnapshot detects mismatched snapshots", async () => {
        const snapshotPath = path.join(dir, "expected.json");
        saveSnapshot({ a: 1 }, snapshotPath);
        const diff = compareSnapshot({ a: 2 }, snapshotPath);
        assert(!diff.matches, "Snapshot should not match");
        assertGreaterThan(diff.diffDetails.length, 0);
      });

      await it("compareSnapshot handles missing snapshot file", async () => {
        const diff = compareSnapshot({ a: 1 }, path.join(dir, "nonexistent.json"));
        assert(!diff.matches);
      });

      await it("loadFixtures finds golden fixtures", async () => {
        const fixturesDir = path.resolve("runtime/evaluation/fixtures/golden");
        const fixtures = loadFixtures(fixturesDir);
        assertGreaterThan(fixtures.length, 0, "Should find at least one fixture");
        assert(fixtures.some(f => f.name === "simple-button"), "Should find simple-button");
      });

      await it("injectFailure wraps handler with failure", async () => {
        const handler = async () => ({ result: "ok" });
        const failed = injectFailure(handler, {
          stage: "ingest",
          mode: "stage_error",
          message: "injected",
        });
        await assertRejects(() => failed(), "injected");
      });

      await it("injectFailure transient mode succeeds after first", async () => {
        let callCount = 0;
        const handler = async () => { callCount++; return { result: "ok" }; };
        const wrapped = injectFailure(handler, {
          stage: "ingest",
          mode: "stage_error",
          transient: true,
        });
        await assertRejects(() => wrapped());
        const result = await wrapped();
        assertEqual((result as Record<string, string>).result, "ok");
      });
    } finally {
      cleanDir(dir);
    }
  }));

  // 12. Idempotency and rollback
  results.push(await describe("idempotency and rollback", async () => {
    const dir = tmpDir();
    try {
      await it("same input produces same artifacts (idempotency)", async () => {
        const config = {
          runId: "run-idem-1",
          fileKey: "test-key",
          outputDir: dir,
          approvedDirs: [dir],
          requireApproval: false,
          retry: { maxAttempts: 1, baseDelayMs: 10, maxDelayMs: 100, backoffMultiplier: 2 },
          budgets: { maxTokens: 10000, maxTimeMs: 30000, maxIterations: 100, maxRepairIterations: 10 },
          similarityThreshold: 0.95,
          minProgress: 0.005,
          viewport: { width: 1440, height: 900 },
          pythonBin: "python3",
          pluginDir: ".",
          target: { framework: "html", styling: "css" },
        };

        const runPipeline = async (runId: string) => {
          const cfg = { ...config, runId };
          const events = new EventLog(cfg.runId);
          const checkpoints = new CheckpointManager(cfg.runId, cfg.outputDir);
          const artifacts = new ArtifactStore(cfg.runId, cfg.outputDir);
          const tools = new ToolRegistry();
          const budget = new BudgetTracker(cfg.budgets);
          const pipeline = new PipelineCoordinator(cfg, events, checkpoints, artifacts, tools, budget);
          for (const stage of PIPELINE_STAGES) {
            pipeline.onStage(stage, async (_ctx, input) => ({
              stage: input.stage,
              deterministic: true,
            }));
          }
          return pipeline.run();
        };

        const r1 = await runPipeline("run-idem-1");
        const r2 = await runPipeline("run-idem-2");
        assertEqual(r1.status, r2.status);
        assertEqual(r1.errors.length, r2.errors.length);
      });

      await it("state machine rollback preserves previous state", async () => {
        const events = new EventLog("run-1");
        const checkpoints = new CheckpointManager("run-1", dir);
        const sm = new StateMachine(events, checkpoints, "run-1");
        sm.start();
        sm.beginStage("ingest");
        sm.completeStage("ingest", { data: "ok" });

        // Try to begin next stage, then fail
        sm.beginStage("normalize");
        sm.failStage("normalize", "error");

        // State should still have ingest completed
        assertEqual(sm.state.completedStages.length, 1);
        assertEqual(sm.state.completedStages[0], "ingest");
      });
    } finally {
      cleanDir(dir);
    }
  }));

  // 13. Model Providers
  results.push(await describe("providers", async () => {
    await it("createProvider returns NullModelProvider for 'null'", async () => {
      const provider = createProvider({ name: "null" });
      assertEqual(provider.name, "null");
      const result = await provider.complete("test");
      assertEqual(result.text, "");
    });

    await it("createProvider returns AnthropicProvider for 'anthropic'", async () => {
      const provider = createProvider({ name: "anthropic", apiKey: "test-key" });
      assertEqual(provider.name, "anthropic");
    });

    await it("createProvider returns OpenAIProvider for 'openai'", async () => {
      const provider = createProvider({ name: "openai", apiKey: "test-key" });
      assertEqual(provider.name, "openai");
    });

    await it("AnthropicProvider rejects empty API key", async () => {
      const provider = new AnthropicProvider("", "claude-sonnet-4-20250514", "https://api.anthropic.com", 30000);
      await assertRejects(
        () => provider.complete("test"),
        "API key not configured",
      );
    });

    await it("OpenAIProvider rejects empty API key", async () => {
      const provider = new OpenAIProvider("", "gpt-4o", "https://api.openai.com", 30000);
      await assertRejects(
        () => provider.complete("test"),
        "API key not configured",
      );
    });

    await it("createProvider throws for unknown provider", async () => {
      assertThrows(
        () => createProvider({ name: "unknown" as "null" }),
        "Unknown provider",
      );
    });
  }));

  // 14. Screenshot Comparator (real python shell-out — Part 12)
  results.push(await describe("screenshot comparator", async () => {
    const dir = tmpDir();
    try {
      await it("identical buffers produce similarity 1.0 via hash fast-path", async () => {
        const comparator = new ScreenshotComparator();
        const buf = makePng(8, 8, [255, 255, 255]);
        const result = comparator.compareBuffers(buf, buf);
        assertEqual(result.similarity, 1.0);
        assertEqual(result.identical, true);
        assertEqual(result.diffPixelCount, 0);
        assertEqual(result.totalPixels, 64);
        assertEqual(result.width, 8);
        assertEqual(result.height, 8);
        // Part 13: the hash fast-path reports the clean perceptual verdict.
        assertEqual(result.ssim, 1.0);
        assertEqual(result.minRegionSsim, null);
        assertEqual(result.ssimClean, true);
      });

      await it("real localized change reports the perceptual verdict", async () => {
        // 11x11 red block on 100x100 white: diffRatio 1.21% > floor, one
        // region >= min_region_area → regional SSIM verdict (not global).
        const comparator = new ScreenshotComparator();
        const bufA = makePng(100, 100, [255, 255, 255]);
        const bufB = makePng(100, 100, [255, 255, 255], { x: 10, y: 10, w: 11, h: 11, rgb: [255, 0, 0] });
        const result = comparator.compareBuffers(bufA, bufB);
        assertEqual(result.identical, false);
        assertEqual(result.ssimClean, false);
        assert(
          typeof result.ssim === "number" && result.ssim >= 0.9,
          `expected global ssim >= 0.9, got ${result.ssim}`,
        );
        assert(
          typeof result.minRegionSsim === "number"
            && result.minRegionSsim < 0.95,
          `expected minRegionSsim < 0.95, got ${result.minRegionSsim}`,
        );
      });

      await it("different real PNGs shell out and report the diff block", async () => {
        const comparator = new ScreenshotComparator();
        const bufA = makePng(8, 8, [255, 255, 255]);
        const bufB = makePng(8, 8, [255, 255, 255], { x: 0, y: 0, w: 2, h: 2, rgb: [255, 0, 0] });
        const result = comparator.compareBuffers(bufA, bufB);
        assertEqual(result.identical, false);
        assert(result.similarity < 1.0, `Expected < 1.0, got ${result.similarity}`);
        assertEqual(result.diffPixelCount, 4);
        assertEqual(result.totalPixels, 64);
        // White-vs-red block: red channel is identical (255 vs 255), so MAE is
        // carried by the green/blue channels — assert a channel that actually differs.
        assertGreaterThan(result.meanAbsoluteError.g, 0);
      });

      await it("compare reads files from disk", async () => {
        const comparator = new ScreenshotComparator();
        const fileA = path.join(dir, "a.png");
        const fileB = path.join(dir, "b.png");
        const buf = makePng(4, 4, [1, 2, 3]);
        fs.writeFileSync(fileA, buf);
        fs.writeFileSync(fileB, buf);
        const result = comparator.compare(fileA, fileB);
        assertEqual(result.identical, true);
      });

      await it("passesThreshold returns boolean", async () => {
        const comparator = new ScreenshotComparator();
        const fileA = path.join(dir, "x.png");
        const fileB = path.join(dir, "y.png");
        const buf = makePng(4, 4, [9, 9, 9]);
        fs.writeFileSync(fileA, buf);
        fs.writeFileSync(fileB, buf);
        assert(comparator.passesThreshold(fileA, fileB, 0.95));
      });

      await it("passesThreshold fails for different images with high threshold", async () => {
        const comparator = new ScreenshotComparator();
        const fileA = path.join(dir, "p.png");
        const fileB = path.join(dir, "q.png");
        fs.writeFileSync(fileA, makePng(8, 8, [255, 255, 255]));
        fs.writeFileSync(fileB, makePng(8, 8, [0, 0, 0]));
        assert(!comparator.passesThreshold(fileA, fileB, 0.99));
      });

      await it("generateDiffReport for identical images", async () => {
        const comparator = new ScreenshotComparator();
        const fileA = path.join(dir, "same1.png");
        const fileB = path.join(dir, "same2.png");
        const buf = makePng(4, 4, [7, 7, 7]);
        fs.writeFileSync(fileA, buf);
        fs.writeFileSync(fileB, buf);
        const report = comparator.generateDiffReport(fileA, fileB);
        assertEqual(report.summary, "Images are identical");
        assertEqual(report.regions.length, 0);
      });

      await it("generateDiffReport for different images", async () => {
        const comparator = new ScreenshotComparator();
        const fileA = path.join(dir, "diff1.png");
        const fileB = path.join(dir, "diff2.png");
        fs.writeFileSync(fileA, makePng(8, 8, [255, 255, 255]));
        fs.writeFileSync(fileB, makePng(8, 8, [0, 0, 0]));
        const report = comparator.generateDiffReport(fileA, fileB);
        assert(report.summary.includes("differ"), `Expected 'differ' in: ${report.summary}`);
        assertGreaterThan(report.regions.length, 0);
      });

      await it("parsePixelDiffOutput parses the last JSON line", async () => {
        const stdout = [
          "python startup noise",
          JSON.stringify({
            similarity: 0.9, diffPixelCount: 10, diffPercentage: 0.1,
            totalPixels: 100, width: 10, height: 10, identical: false,
            meanAbsoluteError: { r: 1, g: 2, b: 3 },
          }),
        ].join("\n");
        const parsed = parsePixelDiffOutput(stdout);
        assert(parsed !== null, "expected parsed result");
        assertEqual(parsed!.similarity, 0.9);
        assertEqual(parsed!.diffPixelCount, 10);
        assertEqual(parsed!.meanAbsoluteError.b, 3);
        // Part 13 keys absent in old output → null (backward compatible).
        assertEqual(parsed!.ssim, null);
        assertEqual(parsed!.minRegionSsim, null);
        assertEqual(parsed!.ssimClean, null);
      });

      await it("parsePixelDiffOutput parses Part 13 SSIM keys", async () => {
        const stdout = JSON.stringify({
          similarity: 0.9, diffPixelCount: 10, diffPercentage: 0.1,
          totalPixels: 100, width: 10, height: 10, identical: false,
          meanAbsoluteError: { r: 1, g: 2, b: 3 },
          ssim: 0.9876, min_region_ssim: 0.9123, ssim_clean: false,
        });
        const parsed = parsePixelDiffOutput(stdout);
        assert(parsed !== null, "expected parsed result");
        assertEqual(parsed!.ssim, 0.9876);
        assertEqual(parsed!.minRegionSsim, 0.9123);
        assertEqual(parsed!.ssimClean, false);
      });

      await it("parsePixelDiffOutput returns null for garbage and error payloads", async () => {
        assertEqual(parsePixelDiffOutput("not json at all"), null);
        assertEqual(parsePixelDiffOutput(""), null);
        assertEqual(
          parsePixelDiffOutput(JSON.stringify({ error: "size mismatch" })),
          null,
        );
      });

      await it("missing python binary yields clean typed failure", async () => {
        const comparator = new ScreenshotComparator(
          undefined,
          { pythonBin: "/nonexistent/python3", pluginDir: "./plugin/figmaforge" },
        );
        const bufA = makePng(4, 4, [255, 255, 255]);
        const bufB = makePng(4, 4, [0, 0, 0]);
        const result = comparator.compareBuffers(bufA, bufB);
        assertEqual(result.similarity, 0.0);
        assertEqual(result.diffPixelCount, -1);
        assertEqual(result.identical, false);
      });

      await it("size-mismatched PNGs yield clean typed failure", async () => {
        const comparator = new ScreenshotComparator();
        const bufA = makePng(4, 4, [255, 255, 255]);
        const bufB = makePng(6, 4, [255, 255, 255]);
        const result = comparator.compareBuffers(bufA, bufB);
        assertEqual(result.similarity, 0.0);
        assertEqual(result.diffPixelCount, -1);
        assertEqual(result.ssim, null);
        assertEqual(result.ssimClean, null);
      });

      await it("cmdCompare prints the perceptual verdict for a real change", async () => {
        const outDir = path.join(dir, "out", "cmp-real");
        const runId = "cmp-real";
        const renders = path.join(outDir, runId, "renders");
        fs.mkdirSync(renders, { recursive: true });
        fs.writeFileSync(
          path.join(renders, "screenshot.png"),
          makePng(100, 100, [255, 255, 255], { x: 10, y: 10, w: 11, h: 11, rgb: [255, 0, 0] }),
        );
        const base = path.join(outDir, "base.png");
        fs.writeFileSync(base, makePng(100, 100, [255, 255, 255]));
        const cli = path.join(process.cwd(), "dist", "runtime", "src", "cli", "main.js");
        const result = spawnSync(
          process.execPath,
          [cli, "compare", "--run-id", runId, "--output-dir", outDir, "--baseline", base],
          { cwd: process.cwd(), encoding: "utf-8" },
        );
        assertEqual(result.status, 0);
        const stdout = result.stdout ?? "";
        assert(stdout.includes("Perceptual change"), `expected verdict, got: ${stdout}`);
        assert(stdout.includes("SSIM"), `expected SSIM, got: ${stdout}`);
      });

      await it("cmdCompare reports identical screenshots via the hash fast-path", async () => {
        const outDir = path.join(dir, "out", "cmp-same");
        const runId = "cmp-same";
        const renders = path.join(outDir, runId, "renders");
        fs.mkdirSync(renders, { recursive: true });
        const buf = makePng(4, 4, [9, 9, 9]);
        fs.writeFileSync(path.join(renders, "screenshot.png"), buf);
        const base = path.join(outDir, "base2.png");
        fs.writeFileSync(base, buf);
        const cli = path.join(process.cwd(), "dist", "runtime", "src", "cli", "main.js");
        const result = spawnSync(
          process.execPath,
          [cli, "compare", "--run-id", runId, "--output-dir", outDir, "--baseline", base],
          { cwd: process.cwd(), encoding: "utf-8" },
        );
        assertEqual(result.status, 0);
        assert((result.stdout ?? "").includes("Screenshots are identical."));
      });
    } finally {
      cleanDir(dir);
    }
  }));

  // 15. Render Handler (VNode → HTML)
  results.push(await describe("render handler", async () => {
    await it("vnodeToHtml converts simple text node", async () => {
      const html = vnodeToHtml("Hello world");
      assertEqual(html, "Hello world");
    });

    await it("vnodeToHtml converts element with tag", async () => {
      const html = vnodeToHtml({ tag: "div", text: "Hello" });
      assert(html.includes("<div>"), `Expected <div> in: ${html}`);
      assert(html.includes("Hello"), `Expected 'Hello' in: ${html}`);
      assert(html.includes("</div>"), `Expected </div> in: ${html}`);
    });

    await it("vnodeToHtml converts element with attributes", async () => {
      const html = vnodeToHtml({ tag: "div", attrs: { id: "main", class: "container" } });
      assert(html.includes('id="main"'), `Expected id attr in: ${html}`);
      assert(html.includes('class="container"'), `Expected class attr in: ${html}`);
    });

    await it("vnodeToHtml converts element with styles", async () => {
      const html = vnodeToHtml({
        tag: "div",
        style: { fontSize: "14px", backgroundColor: "red" },
      });
      assert(html.includes("font-size: 14px"), `Expected font-size in: ${html}`);
      assert(html.includes("background-color: red"), `Expected background-color in: ${html}`);
    });

    await it("vnodeToHtml handles nested children", async () => {
      const html = vnodeToHtml({
        tag: "div",
        children: [
          { tag: "h1", text: "Title" },
          { tag: "p", text: "Body text" },
        ],
      });
      assert(html.includes("<h1>"), `Expected <h1> in: ${html}`);
      assert(html.includes("Title"), `Expected 'Title' in: ${html}`);
      assert(html.includes("<p>"), `Expected <p> in: ${html}`);
      assert(html.includes("Body text"), `Expected 'Body text' in: ${html}`);
    });

    await it("vnodeToHtml handles self-closing tags", async () => {
      const html = vnodeToHtml({ tag: "img", attrs: { src: "test.png" } });
      assert(html.includes("/>"), `Expected self-closing in: ${html}`);
    });

    await it("vnodeToHtml escapes HTML in text", async () => {
      const html = vnodeToHtml({ tag: "p", text: "<script>alert('xss')</script>" });
      assert(!html.includes("<script>"), "Should escape script tags");
      assert(html.includes("&lt;script&gt;"), `Expected escaped tags in: ${html}`);
    });

    await it("vnodeToHtml escapes HTML in string children", async () => {
      const html = vnodeToHtml("A < B & C > D");
      assert(html.includes("&lt;"), "Should escape <");
      assert(html.includes("&amp;"), "Should escape &");
      assert(html.includes("&gt;"), "Should escape >");
    });
  }));

  // 16. Browser render bridge (tryBrowserRender helpers)
  results.push(await describe("browser render bridge", async () => {
    await it("buildBrowserRenderScript embeds viewport and paths", async () => {
      const script = buildBrowserRenderScript(
        "/tmp/r/render_abc.html",
        "/tmp/r/screenshot_abc.png",
        { width: 1440, height: 900 },
      );
      assert(script.includes('"width": 1440'), `Expected width in: ${script}`);
      assert(script.includes('"height": 900'), `Expected height in: ${script}`);
      assert(script.includes(`page.goto("${pathToFileURL("/tmp/r/render_abc.html").href}")`),
        "Expected goto target built from pathToFileURL");
      assert(script.includes(JSON.stringify("/tmp/r/render_abc.html")),
        "Expected JSON-escaped html literal");
      assert(script.includes("/tmp/r/screenshot_abc.png"), "Expected screenshot path");
      assert(script.split("/tmp/r/screenshot_abc.png").length === 3,
        "screenshot path must appear as the screenshot target and in the payload");
      assert(script.includes("sync_playwright"), "Expected playwright usage");
      assert(script.includes("window.__figmaforge_meta"), "Expected meta extraction");
    });

    await it("buildBrowserRenderScript escapes hostile paths as Python literals", async () => {
      const weirdDir = "/tmp/we\"ird\\dir";
      const htmlPath = `${weirdDir}/render.html`;
      const shotPath = `${weirdDir}/shot.png`;
      const script = buildBrowserRenderScript(htmlPath, shotPath, { width: 800, height: 600 });
      assert(script.includes(JSON.stringify(htmlPath)),
        `Expected JSON-escaped html literal in: ${script}`);
      assert(script.includes(JSON.stringify(shotPath)),
        `Expected JSON-escaped screenshot literal in: ${script}`);
      assert(script.includes(`page.goto("${pathToFileURL(htmlPath).href}")`),
        "Expected percent-encoded goto target");
      assert(!script.includes(`file://${htmlPath}`), "goto must not embed the raw path");
    });

    await it("parseBrowserRenderOutput parses valid payload", async () => {
      const parsed = parseBrowserRenderOutput(
        JSON.stringify({ screenshot: "/tmp/s.png", meta: { n1: { x: 0 } } }),
      );
      assert(parsed !== null, "Should parse");
      assertEqual(parsed!.screenshotPath, "/tmp/s.png");
      assertEqual((parsed!.meta.n1 as Record<string, number>).x, 0);
    });

    await it("parseBrowserRenderOutput takes the last stdout line", async () => {
      const payload = JSON.stringify({ screenshot: "/tmp/s2.png", meta: {} });
      const parsed = parseBrowserRenderOutput(`warning: something\n${payload}\n`);
      assert(parsed !== null, "Should parse last line");
      assertEqual(parsed!.screenshotPath, "/tmp/s2.png");
    });

    await it("parseBrowserRenderOutput returns null for error payload", async () => {
      const parsed = parseBrowserRenderOutput(
        JSON.stringify({ error: "playwright_not_installed" }),
      );
      assertEqual(parsed, null);
    });

    await it("parseBrowserRenderOutput returns null for garbage", async () => {
      assertEqual(parseBrowserRenderOutput("not json at all"), null);
      assertEqual(parseBrowserRenderOutput(""), null);
    });

    await it("buildBrowserRenderScript rejects non-finite viewport dimensions", async () => {
      const badViewports: { width: number; height: number }[] = [
        { width: NaN, height: 600 },
        { width: 800, height: Number.POSITIVE_INFINITY },
        { width: "800" as unknown as number, height: 600 },
      ];
      for (const viewport of badViewports) {
        let caught: unknown = null;
        try {
          buildBrowserRenderScript("/tmp/a.html", "/tmp/a.png", viewport);
        } catch (err) {
          caught = err;
        }
        assert(caught instanceof TypeError,
          `Expected TypeError for viewport ${JSON.stringify(viewport)}`);
      }
    });

    await it("pickLayoutMeta prefers non-empty browser meta", async () => {
      const browser = { n1: { x: 1 } };
      const staticMeta = { n1: { x: 2 } };
      assertEqual(pickLayoutMeta(browser, staticMeta), browser);
    });

    await it("pickLayoutMeta falls back to static meta for unusable browser meta", async () => {
      const staticMeta = { n1: { x: 2 } };
      assertEqual(pickLayoutMeta(null, staticMeta), staticMeta);
      assertEqual(pickLayoutMeta(undefined, staticMeta), staticMeta);
      assertEqual(pickLayoutMeta({}, staticMeta), staticMeta);
      assertEqual(pickLayoutMeta("nope" as unknown as Record<string, unknown>, staticMeta), staticMeta);
      assertEqual(pickLayoutMeta([1] as unknown as Record<string, unknown>, staticMeta), staticMeta);
    });
  }));

  // 13b. cmdRun render+compare (Part 19) — measured visual verdict
  results.push(await describe("cmdRun render+compare (Part 19)", async () => {
    const dir = tmpDir();
    const FIXTURE = path.resolve("plugin/figmaforge/fixtures/figma/layout_desktop.json");
    const cli = path.resolve("dist/runtime/src/cli/main.js");
    const PYTHON_BIN = process.env.PYTHON_BIN ?? "python3";
    try {
      await it("html+css run prints a measured score and visual verdict (reference baseline)", async () => {
        const outDir = path.join(dir, "run-a");
        const res = spawnSync(process.execPath, [
          cli, "run", `--file=${FIXTURE}`, "--target=html+css", "--no-approval",
          `--output-dir=${outDir}`,
        ], {
          cwd: path.resolve("."),
          env: { ...process.env, PYTHON_BIN },
          encoding: "utf-8",
          timeout: 240_000,
        });
        assertEqual(res.status, 0, `run exited ${res.status}: ${res.stderr ?? ""}`);
        const stdout = res.stdout ?? "";
        const scoreLine = stdout.split("\n").find((l) => l.includes("Score:")) ?? "";
        assert(scoreLine.length > 0, "run should print a Score line");
        const score = parseFloat(scoreLine.split(":")[1] ?? "0");
        assert(score > 0.9, `measured score should be > 0.9, got ${score}`);
        assert(stdout.includes("Visual verdict"), "run should print the visual verdict");

        const runs = fs.readdirSync(outDir).filter((f) => f.startsWith("run-"));
        assertEqual(runs.length, 1, "expected exactly one run dir");
        const runDir = path.join(outDir, runs[0]);
        const manifest = JSON.parse(
          fs.readFileSync(path.join(runDir, "manifest.json"), "utf-8"),
        );
        // 10 stage artifacts (ingest…verify, Part 20) + the event log = 11.
        assertEqual(manifest.artifacts.length, 11,
          `expected 11 artifacts, got ${manifest.artifacts.length}`);
        const diffArtifact = manifest.artifacts.find(
          (a: { kind: string }) => a.kind === "diff_report",
        );
        assert(diffArtifact !== undefined, "expected a diff_report artifact");
        const report = JSON.parse(
          fs.readFileSync(path.join(runDir, "artifacts", diffArtifact.path), "utf-8"),
        );
        assertEqual(report.baseline_kind, "reference");
        assert(typeof report.raster_stats.ssim_clean === "boolean",
          "SSIM verdict should be a real boolean");
        assert(report.similarity_score > 0.9,
          "html_css should closely match the reference render");
      });

      await it("--baseline override detects a real change and records explicit kind", async () => {
        const outDir = path.join(dir, "run-b");
        const base = path.join(dir, "red-baseline.png");
        fs.writeFileSync(base, makePng(1440, 900, [255, 0, 0]));
        const res = spawnSync(process.execPath, [
          cli, "run", `--file=${FIXTURE}`, "--target=html+css", "--no-approval",
          `--output-dir=${outDir}`, `--baseline=${base}`,
        ], {
          cwd: path.resolve("."),
          env: { ...process.env, PYTHON_BIN },
          encoding: "utf-8",
          timeout: 240_000,
        });
        assertEqual(res.status, 0, `run exited ${res.status}: ${res.stderr ?? ""}`);
        const runs = fs.readdirSync(outDir).filter((f) => f.startsWith("run-"));
        const runDir = path.join(outDir, runs[0]);
        const manifest = JSON.parse(
          fs.readFileSync(path.join(runDir, "manifest.json"), "utf-8"),
        );
        const diffArtifact = manifest.artifacts.find(
          (a: { kind: string }) => a.kind === "diff_report",
        );
        assert(diffArtifact !== undefined, "expected a diff_report artifact");
        const report = JSON.parse(
          fs.readFileSync(path.join(runDir, "artifacts", diffArtifact.path), "utf-8"),
        );
        assertEqual(report.baseline_kind, "explicit");
        assert(report.similarity_score < 0.9, "a red baseline must drop the score");
        assertEqual(report.raster_stats.ssim_clean, false);
      });

      await it("--figma-baseline without a token fails with a token error", async () => {
        const outDir = path.join(dir, "run-c");
        const env: Record<string, string> = { ...process.env, PYTHON_BIN };
        delete env.FIGMA_TOKEN;
        const res = spawnSync(process.execPath, [
          cli, "run", `--file=${FIXTURE}`, "--file-key=abc123", "--target=html+css",
          "--no-approval", "--figma-baseline", `--output-dir=${outDir}`,
        ], {
          cwd: path.resolve("."),
          env,
          encoding: "utf-8",
          timeout: 240_000,
        });
        assertEqual(res.status, 1, `run should fail: ${res.stdout ?? ""} ${res.stderr ?? ""}`);
        const combined = (res.stdout ?? "") + (res.stderr ?? "");
        assert(combined.includes("FIGMA_TOKEN"),
          `expected a FIGMA_TOKEN error, got: ${combined}`);
      });
    } finally {
      cleanDir(dir);
    }
  }));

  // 13c. cmdRun repair+verify (Part 20) — ten-stage run with a terminal gate
  results.push(await describe("cmdRun repair+verify (Part 20)", async () => {
    const dir = tmpDir();
    const FIXTURE = path.resolve("plugin/figmaforge/fixtures/figma/layout_desktop.json");
    const cli = path.resolve("dist/runtime/src/cli/main.js");
    const PYTHON_BIN = process.env.PYTHON_BIN ?? "python3";
    try {
      await it("html+css run completes all ten stages with a PASSED verification", async () => {
        const outDir = path.join(dir, "run-a");
        const res = spawnSync(process.execPath, [
          cli, "run", `--file=${FIXTURE}`, "--target=html+css", "--no-approval",
          `--output-dir=${outDir}`,
        ], {
          cwd: path.resolve("."),
          env: { ...process.env, PYTHON_BIN },
          encoding: "utf-8",
          timeout: 240_000,
        });
        assertEqual(res.status, 0, `run exited ${res.status}: ${res.stderr ?? ""}`);
        const stdout = res.stdout ?? "";
        const scoreLine = stdout.split("\n").find((l) => l.includes("Score:")) ?? "";
        const score = parseFloat(scoreLine.split(":")[1] ?? "0");
        assert(score > 0.9, `measured score should be > 0.9, got ${score}`);
        assert(stdout.includes("Visual verdict"), "run should print the visual verdict");
        assert(stdout.includes("Verification: PASSED"),
          `expected a PASSED verification line, got:\n${stdout}`);
        const repairsLine = stdout.split("\n").find((l) => l.includes("Repairs:")) ?? "";
        const repairs = parseInt(repairsLine.split(":")[1] ?? "0", 10);
        assertEqual(repairs, 0, "no repair should have run");

        const runs = fs.readdirSync(outDir).filter((f) => f.startsWith("run-"));
        assertEqual(runs.length, 1, "expected exactly one run dir");
        const runDir = path.join(outDir, runs[0]);
        const manifest = JSON.parse(
          fs.readFileSync(path.join(runDir, "manifest.json"), "utf-8"),
        );
        // 10 stage artifacts (ingest…verify) + the event log = 11.
        assertEqual(manifest.artifacts.length, 11,
          `expected 11 artifacts, got ${manifest.artifacts.length}`);
        const verifyArtifact = manifest.artifacts.find(
          (a: { kind: string }) => a.kind === "metrics",
        );
        assert(verifyArtifact !== undefined, "expected a metrics (verify) artifact");
        const verify = JSON.parse(
          fs.readFileSync(path.join(runDir, "artifacts", verifyArtifact.path), "utf-8"),
        );
        assertEqual(verify.passed, true);
        assertEqual(verify.source, "compare");
        assertEqual(verify.baseline_kind, "reference");
      });

      await it("red baseline runs the real repair loop and verifies FAILED honestly", async () => {
        const outDir = path.join(dir, "run-b");
        const base = path.join(dir, "red-baseline.png");
        fs.writeFileSync(base, makePng(1440, 900, [255, 0, 0]));
        const res = spawnSync(process.execPath, [
          cli, "run", `--file=${FIXTURE}`, "--target=html+css", "--no-approval",
          `--output-dir=${outDir}`, `--baseline=${base}`,
        ], {
          cwd: path.resolve("."),
          env: { ...process.env, PYTHON_BIN },
          encoding: "utf-8",
          timeout: 240_000,
        });
        assertEqual(res.status, 0, `run exited ${res.status}: ${res.stderr ?? ""}`);
        const stdout = res.stdout ?? "";
        // The full-red baseline scores ~0, so repair genuinely runs (the
        // default gate is 0.95) and verify honestly fails after re-measuring.
        assert(stdout.includes("Verification: FAILED"),
          `expected a FAILED verification line, got:\n${stdout}`);
        const repairsLine = stdout.split("\n").find((l) => l.includes("Repairs:")) ?? "";
        const repairs = parseInt(repairsLine.split(":")[1] ?? "0", 10);
        assert(repairs >= 1,
          `the repair loop should have run real iterations, got Repairs: ${repairs}`);

        const runs = fs.readdirSync(outDir).filter((f) => f.startsWith("run-"));
        const runDir = path.join(outDir, runs[0]);
        const manifest = JSON.parse(
          fs.readFileSync(path.join(runDir, "manifest.json"), "utf-8"),
        );
        assertEqual(manifest.artifacts.length, 11,
          `expected 11 artifacts, got ${manifest.artifacts.length}`);
        const repairArtifact = manifest.artifacts.find(
          (a: { kind: string }) => a.kind === "repair_result",
        );
        assert(repairArtifact !== undefined, "expected a repair_result artifact");
        const repair = JSON.parse(
          fs.readFileSync(path.join(runDir, "artifacts", repairArtifact.path), "utf-8"),
        );
        assert(repair.iterations_run >= 1,
          `repair should have run loop iterations, got ${repair.iterations_run}`);
        assert(repair.repaired_styles_path !== null,
          "repaired styles must serialize to the repair out dir");
        // The repair work dir exists with the regenerated html_css output.
        const genDir = path.join(runDir, "repair", "generated", "html_css");
        assert(fs.existsSync(genDir), "regenerated html_css should exist");
        assert(fs.readdirSync(genDir).some((f: string) => f.endsWith(".html")),
          "the repair work dir should contain regenerated html");

        // Verify re-measured the regenerated code (not the compare score).
        const verifyArtifact = manifest.artifacts.find(
          (a: { kind: string }) => a.kind === "metrics",
        );
        assert(verifyArtifact !== undefined, "expected a metrics (verify) artifact");
        const verify = JSON.parse(
          fs.readFileSync(path.join(runDir, "artifacts", verifyArtifact.path), "utf-8"),
        );
        assertEqual(verify.passed, false);
        assertEqual(verify.source, "re-rendered");
        assertEqual(verify.baseline_kind, "explicit");
      });

      await it("--no-repair skips the repair stage and still verifies honestly", async () => {
        const outDir = path.join(dir, "run-c");
        const base = path.join(dir, "red-baseline.png");
        fs.writeFileSync(base, makePng(1440, 900, [255, 0, 0]));
        const res = spawnSync(process.execPath, [
          cli, "run", `--file=${FIXTURE}`, "--target=html+css", "--no-approval",
          `--output-dir=${outDir}`, `--baseline=${base}`, "--no-repair",
        ], {
          cwd: path.resolve("."),
          env: { ...process.env, PYTHON_BIN },
          encoding: "utf-8",
          timeout: 240_000,
        });
        assertEqual(res.status, 0, `run exited ${res.status}: ${res.stderr ?? ""}`);
        const stdout = res.stdout ?? "";
        assert(stdout.includes("Verification: FAILED"),
          `expected a FAILED verification line, got:\n${stdout}`);
        const repairsLine = stdout.split("\n").find((l) => l.includes("Repairs:")) ?? "";
        const repairs = parseInt(repairsLine.split(":")[1] ?? "0", 10);
        assertEqual(repairs, 0, "--no-repair must not run the loop");

        const runs = fs.readdirSync(outDir).filter((f) => f.startsWith("run-"));
        const runDir = path.join(outDir, runs[0]);
        const manifest = JSON.parse(
          fs.readFileSync(path.join(runDir, "manifest.json"), "utf-8"),
        );
        const repairArtifact = manifest.artifacts.find(
          (a: { kind: string }) => a.kind === "repair_result",
        );
        assert(repairArtifact !== undefined, "expected a repair_result artifact");
        const repair = JSON.parse(
          fs.readFileSync(path.join(runDir, "artifacts", repairArtifact.path), "utf-8"),
        );
        assertEqual(repair.repairs, 0);
        assert(repair.note.includes("disabled"),
          `expected the disabled note, got: ${repair.note}`);
        // No repair work dir was ever created.
        assert(!fs.existsSync(path.join(runDir, "repair")),
          "--no-repair must never create the repair dir");
      });
    } finally {
      cleanDir(dir);
    }
  }));

  // 14. Backend code generation (Part 15) — real Python backends through the pipeline
  results.push(...await runBackendCodegenTests());

  return results;
}
