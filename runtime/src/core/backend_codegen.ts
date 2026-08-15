/**
 * Backend code generation bridge (Part 15).
 *
 * Maps a CodegenTarget (framework+styling) to a real Python backend name and
 * invokes ``scripts/pipeline.py generate`` through the same spawn mechanics
 * as the Python tool bridge (``tools.ts``).  Also provides the ingest and
 * generate stage handler factories registered by the CLI's ``run`` command.
 *
 * Every target in ``TARGET_BACKENDS`` resolves to a backend registered in
 * the Python registry (html_css, react_tailwind, vue, svelte, swiftui,
 * flutter).  Targets without a Python adapter (e.g. react+css) are rejected
 * with a typed error — never silently approximated.
 */

import * as fs from "node:fs";
import * as path from "node:path";
import * as os from "node:os";
import { spawn } from "node:child_process";
import type { CodegenTarget } from "./types.js";
import { targetKey } from "./types.js";
import type { PipelineContext, StageHandler } from "./pipeline.js";

// ---------------------------------------------------------------------------
// Target → backend map
// ---------------------------------------------------------------------------

/** Every preset with a real Python backend, keyed by ``targetKey``. */
export const TARGET_BACKENDS: Record<string, string> = {
  "html+css": "html_css",
  "react+tailwind": "react_tailwind",
  "vue+scoped_css": "vue",
  "svelte+scoped_css": "svelte",
  "swiftui+swiftui_modifiers": "swiftui",
  "flutter+flutter_widgets": "flutter",
};

/** Raised when a target has no Python backend to generate it. */
export class UnsupportedTargetError extends Error {
  constructor(target: string) {
    super(
      `no Python backend for target "${target}" — available: ` +
      Object.keys(TARGET_BACKENDS).sort().join(", "),
    );
    this.name = "UnsupportedTargetError";
  }
}

/** Resolve a target (or its string key) to a Python backend name. */
export function backendForTarget(target: CodegenTarget | string): string {
  const key = typeof target === "string" ? target : targetKey(target);
  const backend = TARGET_BACKENDS[key];
  if (!backend) {
    throw new UnsupportedTargetError(key);
  }
  return backend;
}

// ---------------------------------------------------------------------------
// Manifest types
// ---------------------------------------------------------------------------

export interface BackendManifestFile {
  path: string;
  language: string;
  node_ids: string[];
  size_bytes: number;
}

export interface BackendManifest {
  backend: string;
  files: BackendManifestFile[];
  fidelity_losses: Array<{
    feature: string;
    node_id: string;
    message: string;
    severity: string;
    fallback_applied?: string;
  }>;
  metadata: Record<string, unknown>;
}

export interface BackendGenerateResult {
  manifest: BackendManifest;
  /** Directory where the generated files were written. */
  filesDir: string;
}

export interface BackendInvokeOptions {
  viewport?: number;
}

// ---------------------------------------------------------------------------
// Python invocation (mirrors createPythonTool's spawn mechanics)
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

/** Parse the single JSON line the pipeline CLI prints on success. */
export function parseManifestLine(stdout: string): BackendManifest {
  const lines = stdout.split(/\r?\n/).filter((l) => l.trim().length > 0);
  if (lines.length === 0) {
    throw new Error("pipeline.py printed no output");
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(lines[lines.length - 1]);
  } catch (err) {
    throw new Error(`pipeline.py output is not JSON: ${(err as Error).message}`);
  }
  if (typeof parsed !== "object" || parsed === null || !("backend" in parsed)) {
    throw new Error("pipeline.py manifest is missing the 'backend' field");
  }
  return parsed as BackendManifest;
}

// ---------------------------------------------------------------------------
// Invocation
// ---------------------------------------------------------------------------

/**
 * Generate backend code from a Figma file JSON via ``scripts/pipeline.py``.
 *
 * The file JSON is staged to a temp file (the CLI reads ``--file``); the CLI
 * writes the generated files under ``<outDir>/<backend>/``.  Returns the
 * parsed manifest plus that directory.
 */
export async function invokeBackendGenerator(
  cfg: { pythonBin: string; pluginDir: string },
  target: CodegenTarget | string,
  fileJson: unknown,
  outDir: string,
  options?: BackendInvokeOptions,
): Promise<BackendGenerateResult> {
  const backend = backendForTarget(target);

  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "ff-codegen-"));
  const inputPath = path.join(tmp, "input.json");
  fs.writeFileSync(inputPath, JSON.stringify(fileJson), "utf-8");

  try {
    const args = [
      "generate",
      "--file", inputPath,
      "--backend", backend,
      "--out-dir", outDir,
    ];
    if (options?.viewport !== undefined) {
      args.push("--viewport", String(options.viewport));
    }

    const result = await spawnPython(
      cfg.pythonBin,
      path.join(cfg.pluginDir, "scripts", "pipeline.py"),
      args,
      cfg.pluginDir,
    );
    if (result.exitCode !== 0) {
      const detail = result.stderr.trim() || result.stdout.trim();
      throw new Error(
        `pipeline.py generate (${backend}) exited ${result.exitCode}: ${detail}`,
      );
    }
    const manifest = parseManifestLine(result.stdout);
    return { manifest, filesDir: path.join(outDir, backend) };
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
}

// ---------------------------------------------------------------------------
// Stage handlers
// ---------------------------------------------------------------------------

/**
 * Ingest stage handler — fetches a Figma file (live, via ``--file-key``) or
 * reads a local fixture (via ``ctx.shared["filePath"]``), then stores the
 * normalized file JSON in shared state for downstream stages.
 */
export function createIngestStageHandler(): StageHandler {
  return async (ctx: PipelineContext, input: Record<string, unknown>) => {
    const filePath = ctx.shared.get("filePath");
    const fileKey = String(input.fileKey ?? ctx.config.fileKey ?? "");
    const args = filePath
      ? ["ingest", "--file", String(filePath)]
      : ["ingest", "--file-key", fileKey];

    const result = await spawnPython(
      ctx.toolCtx.pythonBin,
      path.join(ctx.config.pluginDir, "scripts", "pipeline.py"),
      args,
      ctx.config.pluginDir,
    );
    if (result.exitCode !== 0) {
      const detail = result.stderr.trim() || result.stdout.trim();
      throw new Error(
        `pipeline.py ingest exited ${result.exitCode}: ${detail}`,
      );
    }

    const fileJson = JSON.parse(
      result.stdout.split(/\r?\n/).filter((l) => l.trim().length > 0).slice(-1)[0] ?? "{}",
    ) as Record<string, unknown>;
    ctx.shared.set("fileJson", fileJson);
    return {
      fileKey: String(fileJson.file_key ?? fileKey),
      fileJson,
    };
  };
}

/**
 * Generate stage handler — lowers the ingested file through the configured
 * target's Python backend and records the manifest + files directory.
 */
export function createGenerateStageHandler(): StageHandler {
  return async (ctx: PipelineContext, input: Record<string, unknown>) => {
    const fileJson = ctx.shared.get("fileJson") ?? input.fileJson;
    if (!fileJson) {
      throw new Error("generate stage requires ingest output (no fileJson available)");
    }

    const outDir = path.join(ctx.config.outputDir, ctx.config.runId, "generated");
    const result = await invokeBackendGenerator(
      { pythonBin: ctx.toolCtx.pythonBin, pluginDir: ctx.config.pluginDir },
      ctx.config.target,
      fileJson,
      outDir,
      { viewport: ctx.config.viewport.width },
    );

    ctx.shared.set("generatedManifest", result.manifest);
    return {
      backend: result.manifest.backend,
      filesDir: result.filesDir,
      manifest: result.manifest,
    };
  };
}
