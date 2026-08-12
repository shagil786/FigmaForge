/**
 * Comprehensive tests for all FigmaForge runtime modules.
 *
 * Covers: types, events, checkpoints, artifacts, tools, state machine,
 * budget tracker, retry logic, security, pipeline, evaluation.
 */

import * as fs from "node:fs";
import * as path from "node:path";
import * as os from "node:os";

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

  return results;
}
