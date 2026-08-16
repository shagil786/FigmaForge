/**
 * Adaptive run CLI tests.
 *
 * Keeps coverage at the CLI layer by exercising real argument parsing,
 * artifact/event creation, and help output while faking adaptive preflight
 * plus pipeline execution.
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
  assertIncludes,
  printResults,
} from "./test_framework.js";
import type { SuiteResult } from "./test_framework.js";
import type { AdaptivePlan } from "../src/core/adaptive_preflight.js";
import { buildAdaptiveExecutionPolicy } from "../src/core/adaptive_preflight.js";
import {
  resetCliTestDepsForTesting,
  runMainForTests,
  setCliTestDepsForTesting,
} from "../src/cli/main.js";

const DEFAULT_ADAPTIVE_REQUEST =
  "Convert this Figma design into the selected code-generation target";

interface FakeCliResult {
  artifactKinds: string[];
  eventKinds: string[];
  requests: string[];
  pipelineRan: boolean;
  stdout: string;
  adaptivePlanApplied: boolean;
}

function tmpDir(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), "ff-adaptive-run-"));
}

function cleanDir(dir: string): void {
  if (fs.existsSync(dir)) {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

function makePlan(overrides: Partial<AdaptivePlan["route"]> = {}): AdaptivePlan {
  return {
    schema_version: 1,
    request: "Inspect",
    root: "/workspace",
    detection: { status: "classified", confidence: 0.9 },
    route: {
      phases: ["inspect", "classify"],
      roles: [{ name: "router" }],
      external_skills: [],
      execution_mode: "direct",
      stack_status: "classified",
      approval_gates: [],
      unloaded_modules: [],
      ...overrides,
    },
  };
}

async function captureStdout(fn: () => Promise<void>): Promise<string> {
  const lines: string[] = [];
  const originalLog = console.log;
  console.log = (...args: unknown[]) => {
    lines.push(args.map((arg) => String(arg)).join(" "));
  };
  try {
    await fn();
  } finally {
    console.log = originalLog;
  }
  return lines.join("\n");
}

async function runCliWithFakePreflight(
  extraArgs: string[],
  options: { plan?: AdaptivePlan } = {},
): Promise<FakeCliResult> {
  const dir = tmpDir();
  const fixturePath = path.join(dir, "fixture.json");
  fs.writeFileSync(fixturePath, "{}", "utf-8");

  const requests: string[] = [];
  let artifactKinds: string[] = [];
  let eventKinds: string[] = [];
  let pipelineRan = false;
  let adaptivePlanApplied = false;

  setCliTestDepsForTesting({
    invokeAdaptivePreflight: async (_cfg, _root, request) => {
      requests.push(request);
      const plan = options.plan ?? makePlan();
      return { ...plan, request };
    },
    createPipeline: (_config, events, _checkpoints, artifacts) => ({
      setAbortSignal: () => undefined,
      setShared: (key: string) => {
        if (key === "adaptivePlan") adaptivePlanApplied = true;
        if (key === "adaptivePolicy") adaptivePlanApplied = true;
      },
      onStage: () => undefined,
      run: async () => {
        pipelineRan = true;
        artifactKinds = artifacts.manifest().artifacts.map((artifact) => artifact.kind);
        eventKinds = events.all().map((event) => event.kind);
        return {
          runId: "run-test",
          status: "completed",
          similarityScore: 0,
          repairIterations: 0,
          totalDurationMs: 1,
          tokensUsed: 0,
          artifacts: artifacts.count,
          events: events.length,
          checkpoints: 0,
          errors: [],
        };
      },
    }),
  });

  try {
    const stdout = await captureStdout(async () => {
      await runMainForTests([
        "node",
        "figmaforge",
        "run",
        `--file=${fixturePath}`,
        `--output-dir=${path.join(dir, "out")}`,
        "--no-approval",
        ...extraArgs,
      ]);
    });
    return { artifactKinds, eventKinds, requests, pipelineRan, stdout, adaptivePlanApplied };
  } finally {
    resetCliTestDepsForTesting();
    cleanDir(dir);
  }
}

export async function runAdaptiveRunTests(): Promise<SuiteResult[]> {
  return [await describe("adaptive run CLI", async () => {
    await it("reports approval required only for an enforced mutation gate", async () => {
      const policy = buildAdaptiveExecutionPolicy(
        makePlan({ approval_gates: ["project_approval"] }),
        true,
      );
      assertEqual(policy.approval_required, false);
    });

    await it("shows adaptive flags in help output", async () => {
      const stdout = await captureStdout(async () => {
        await runMainForTests(["node", "figmaforge", "help"]);
      });
      assertIncludes(stdout, "--adaptive");
      assertIncludes(stdout, "--adaptive-request=<text>");
    });

    await it("does not invoke adaptive preflight by default", async () => {
      const result = await runCliWithFakePreflight([]);
      assertEqual(result.requests.length, 0);
      assert(!result.artifactKinds.includes("adaptive_plan"));
      assert(!result.eventKinds.includes("adaptive_plan_created"));
      assert(result.pipelineRan, "pipeline should still start without adaptive flags");
    });

    await it("stores an adaptive plan when requested", async () => {
      const result = await runCliWithFakePreflight(["--adaptive-request=Build React UI"]);
      assert(result.artifactKinds.includes("adaptive_plan"));
      assert(result.eventKinds.includes("adaptive_plan_created"));
      assert(result.eventKinds.includes("adaptive_plan_applied"));
      assert(result.eventKinds.includes("adaptive_policy_applied"));
      assert(result.adaptivePlanApplied, "adaptive plan should enter shared pipeline context");
      assertEqual(result.requests[0], "Build React UI");
    });

    await it("--adaptive uses the documented default request", async () => {
      const result = await runCliWithFakePreflight(["--adaptive"]);
      assertEqual(result.requests[0], DEFAULT_ADAPTIVE_REQUEST);
      assert(result.artifactKinds.includes("adaptive_plan"));
    });

    await it("an unclassified plan does not prevent the visual pipeline from starting", async () => {
      const result = await runCliWithFakePreflight(
        ["--adaptive-request=Inspect this design"],
        {
          plan: makePlan({
            stack_status: "unclassified",
            execution_mode: "isolated_scout",
            phases: ["inspect"],
          }),
        },
      );
      assert(result.pipelineRan, "pipeline should still run when adaptive plan is unclassified");
      assert(result.artifactKinds.includes("adaptive_plan"));
      assert(result.eventKinds.includes("adaptive_plan_created"));
    });

    await it("repeated runMainForTests invocations do not accumulate SIGINT listeners", async () => {
      const baseline = process.rawListeners("SIGINT");
      try {
        await runCliWithFakePreflight([]);
        await runCliWithFakePreflight(["--adaptive"]);
        assertEqual(
          process.rawListeners("SIGINT").length,
          baseline.length,
          "cmdRun should clean up its SIGINT listener after each run",
        );
      } finally {
        const baselineSet = new Set(baseline);
        for (const listener of process.rawListeners("SIGINT")) {
          if (!baselineSet.has(listener)) {
            process.removeListener("SIGINT", listener);
          }
        }
      }
    });
  })];
}

async function main(): Promise<void> {
  const results = await runAdaptiveRunTests();
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
