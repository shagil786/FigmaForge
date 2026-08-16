/**
 * Adaptive preflight bridge.
 *
 * Invokes ``scripts/adaptive_plan.py`` and parses the final JSON line into a
 * typed adaptive plan. The bridge intentionally mirrors the existing Python
 * spawn mechanics used by backend_codegen.ts.
 */

import * as path from "node:path";
import { spawn } from "node:child_process";

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

export interface AdaptivePlan {
  schema_version: 1;
  request: string;
  root: string;
  detection: Record<string, unknown>;
  route: {
    phases: string[];
    roles: Array<Record<string, unknown>>;
    external_skills: string[];
    execution_mode: string;
    stack_status: string;
    approval_gates: string[];
    unloaded_modules: string[];
  };
}

export class AdaptivePreflightError extends Error {
  readonly stderr: string;
  readonly stdout: string;

  constructor(
    message: string,
    details: { stderr?: string; stdout?: string; cause?: unknown } = {},
  ) {
    super(message, details.cause === undefined ? undefined : { cause: details.cause });
    this.name = "AdaptivePreflightError";
    this.stderr = details.stderr ?? "";
    this.stdout = details.stdout ?? "";
  }
}

// ---------------------------------------------------------------------------
// Spawn helper
// ---------------------------------------------------------------------------

interface PythonResult {
  exitCode: number;
  stdout: string;
  stderr: string;
}

function spawnPython(
  pythonBin: string,
  scriptPath: string,
  args: string[],
  cwd: string,
): Promise<PythonResult> {
  return new Promise((resolve, reject) => {
    const proc = spawn(pythonBin, [scriptPath, ...args], {
      cwd,
      env: { ...process.env, PYTHONIOENCODING: "utf-8" },
    });

    let stdout = "";
    let stderr = "";

    proc.stdout.on("data", (chunk: Buffer) => { stdout += chunk.toString(); });
    proc.stderr.on("data", (chunk: Buffer) => { stderr += chunk.toString(); });

    proc.on("close", (code: number | null) => {
      resolve({ exitCode: code ?? 1, stdout, stderr });
    });
    proc.on("error", (err: Error) => reject(err));
  });
}

// ---------------------------------------------------------------------------
// Parsing helpers
// ---------------------------------------------------------------------------

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isRecordArray(value: unknown): value is Array<Record<string, unknown>> {
  return Array.isArray(value) && value.every((item) => isRecord(item));
}

function parseFinalJsonLine(stdout: string, stderr: string): Record<string, unknown> {
  const lines = stdout.split(/\r?\n/).filter((line) => line.trim().length > 0);
  if (lines.length === 0) {
    throw new AdaptivePreflightError("adaptive_plan.py printed no output", {
      stderr,
      stdout,
    });
  }

  const candidate = lines[lines.length - 1];
  let parsed: unknown;
  try {
    parsed = JSON.parse(candidate);
  } catch (err) {
    throw new AdaptivePreflightError(
      `adaptive_plan.py output is not JSON: ${(err as Error).message}`,
      { stderr, stdout },
    );
  }

  if (!isRecord(parsed)) {
    throw new AdaptivePreflightError("adaptive_plan.py output is not a JSON object", {
      stderr,
      stdout,
    });
  }

  return parsed;
}

function validateAdaptivePlan(
  parsed: Record<string, unknown>,
  stderr: string,
  stdout: string,
): AdaptivePlan {
  if (parsed.schema_version !== 1) {
    throw new AdaptivePreflightError("adaptive_plan.py output has invalid schema_version", {
      stderr,
      stdout,
    });
  }
  if (typeof parsed.request !== "string") {
    throw new AdaptivePreflightError("adaptive_plan.py output is missing request", {
      stderr,
      stdout,
    });
  }
  if (typeof parsed.root !== "string") {
    throw new AdaptivePreflightError("adaptive_plan.py output is missing root", {
      stderr,
      stdout,
    });
  }
  if (!isRecord(parsed.detection)) {
    throw new AdaptivePreflightError("adaptive_plan.py output is missing detection", {
      stderr,
      stdout,
    });
  }
  if (!isRecord(parsed.route)) {
    throw new AdaptivePreflightError("adaptive_plan.py output is missing route", {
      stderr,
      stdout,
    });
  }

  const route = parsed.route;
  if (!isStringArray(route.phases)) {
    throw new AdaptivePreflightError("adaptive_plan.py output is missing route.phases", {
      stderr,
      stdout,
    });
  }
  if (!isRecordArray(route.roles)) {
    throw new AdaptivePreflightError("adaptive_plan.py output is missing route.roles", {
      stderr,
      stdout,
    });
  }
  if (!isStringArray(route.external_skills)) {
    throw new AdaptivePreflightError("adaptive_plan.py output is missing route.external_skills", {
      stderr,
      stdout,
    });
  }
  if (typeof route.execution_mode !== "string") {
    throw new AdaptivePreflightError("adaptive_plan.py output is missing route.execution_mode", {
      stderr,
      stdout,
    });
  }
  if (typeof route.stack_status !== "string") {
    throw new AdaptivePreflightError("adaptive_plan.py output is missing route.stack_status", {
      stderr,
      stdout,
    });
  }
  if (!isStringArray(route.approval_gates)) {
    throw new AdaptivePreflightError("adaptive_plan.py output is missing route.approval_gates", {
      stderr,
      stdout,
    });
  }
  if (!isStringArray(route.unloaded_modules)) {
    throw new AdaptivePreflightError("adaptive_plan.py output is missing route.unloaded_modules", {
      stderr,
      stdout,
    });
  }

  return {
    schema_version: 1,
    request: parsed.request,
    root: parsed.root,
    detection: parsed.detection,
    route: {
      phases: route.phases,
      roles: route.roles,
      external_skills: route.external_skills,
      execution_mode: route.execution_mode,
      stack_status: route.stack_status,
      approval_gates: route.approval_gates,
      unloaded_modules: route.unloaded_modules,
    },
  };
}

// ---------------------------------------------------------------------------
// Bridge
// ---------------------------------------------------------------------------

export async function invokeAdaptivePreflight(
  cfg: { pythonBin: string; pluginDir: string },
  root: string,
  request: string,
  installedCapabilities: string[] = [],
): Promise<AdaptivePlan> {
  const scriptPath = path.join(cfg.pluginDir, "scripts", "adaptive_plan.py");
  const args = [
    "--root", root,
    "--request", request,
  ];
  for (const capability of installedCapabilities) {
    args.push("--installed-capability", capability);
  }

  let result: PythonResult;
  try {
    result = await spawnPython(cfg.pythonBin, scriptPath, args, cfg.pluginDir);
  } catch (err) {
    throw new AdaptivePreflightError("failed to launch adaptive_plan.py", {
      cause: err,
    });
  }
  if (result.exitCode !== 0) {
    throw new AdaptivePreflightError(`adaptive_plan.py exited ${result.exitCode}`, {
      stderr: result.stderr,
      stdout: result.stdout,
    });
  }

  const parsed = parseFinalJsonLine(result.stdout, result.stderr);
  return validateAdaptivePlan(parsed, result.stderr, result.stdout);
}
