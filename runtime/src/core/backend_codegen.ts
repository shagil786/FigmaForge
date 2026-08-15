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
export function parseJsonLine(stdout: string): Record<string, unknown> {
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
  if (typeof parsed !== "object" || parsed === null) {
    throw new Error("pipeline.py output is not a JSON object");
  }
  return parsed as Record<string, unknown>;
}

/** Parse a pipeline manifest line (same contract, requires the backend field). */
export function parseManifestLine(stdout: string): BackendManifest {
  const parsed = parseJsonLine(stdout);
  if (!("backend" in parsed)) {
    throw new Error("pipeline.py manifest is missing the 'backend' field");
  }
  return parsed as unknown as BackendManifest;
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

export interface IngestSource {
  /** Local Figma file JSON path (offline ingest). */
  file?: string;
  /** Live Figma file key (requires FIGMA_TOKEN). */
  fileKey?: string;
}

/**
 * Ingest a Figma file via ``scripts/pipeline.py`` — from a local file or the
 * live API — and return the normalized file JSON plus its file key.
 */
export async function invokeIngest(
  cfg: { pythonBin: string; pluginDir: string },
  source: IngestSource,
): Promise<{ fileKey: string; fileJson: Record<string, unknown> }> {
  const args = source.file
    ? ["ingest", "--file", source.file]
    : ["ingest", "--file-key", source.fileKey ?? ""];
  const result = await spawnPython(
    cfg.pythonBin,
    path.join(cfg.pluginDir, "scripts", "pipeline.py"),
    args,
    cfg.pluginDir,
  );
  if (result.exitCode !== 0) {
    const detail = result.stderr.trim() || result.stdout.trim();
    throw new Error(
      `pipeline.py ingest exited ${result.exitCode}: ${detail}`,
    );
  }
  const fileJson = parseJsonLine(result.stdout);
  return {
    fileKey: String(fileJson.file_key ?? source.fileKey ?? ""),
    fileJson,
  };
}

// ---------------------------------------------------------------------------
// Front-half stages (Part 16) — normalize / resolve / layout
// ---------------------------------------------------------------------------

/**
 * Run one front-half subcommand against a JSON payload staged to a temp
 * file; returns the parsed single-JSON-line result.
 */
async function invokeJsonStage(
  cfg: { pythonBin: string; pluginDir: string },
  subcommand: "normalize" | "resolve" | "layout",
  inputJson: unknown,
  extraArgs: string[] = [],
): Promise<Record<string, unknown>> {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "ff-stage-"));
  const inputPath = path.join(tmp, "input.json");
  fs.writeFileSync(inputPath, JSON.stringify(inputJson), "utf-8");
  try {
    const result = await spawnPython(
      cfg.pythonBin,
      path.join(cfg.pluginDir, "scripts", "pipeline.py"),
      [subcommand, "--file", inputPath, ...extraArgs],
      cfg.pluginDir,
    );
    if (result.exitCode !== 0) {
      const detail = result.stderr.trim() || result.stdout.trim();
      throw new Error(
        `pipeline.py ${subcommand} exited ${result.exitCode}: ${detail}`,
      );
    }
    return parseJsonLine(result.stdout);
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
}

/** Build + validate the design IR from a Figma file JSON. */
export function invokeNormalize(
  cfg: { pythonBin: string; pluginDir: string },
  fileJson: unknown,
): Promise<Record<string, unknown>> {
  return invokeJsonStage(cfg, "normalize", fileJson);
}

/** Resolve a design IR against the project library. */
export function invokeResolve(
  cfg: { pythonBin: string; pluginDir: string },
  irJson: unknown,
): Promise<Record<string, unknown>> {
  return invokeJsonStage(cfg, "resolve", irJson);
}

/** Infer the layout plan from a design IR. */
export function invokeLayout(
  cfg: { pythonBin: string; pluginDir: string },
  irJson: unknown,
  viewport?: number,
): Promise<Record<string, unknown>> {
  return invokeJsonStage(
    cfg, "layout", irJson,
    viewport !== undefined ? ["--viewport", String(viewport)] : [],
  );
}

// ---------------------------------------------------------------------------
// Assets stage (Part 17) — download + content-address IR asset refs
// ---------------------------------------------------------------------------

export interface AssetManifestEntry {
  node_id: string;
  url: string | null;
  image_ref: string | null;
  kind: string;
  status: string;
  content_hash?: string;
  local_path?: string;
}

export interface AssetManifest {
  schema_version: number;
  file_key: string;
  assets: AssetManifestEntry[];
  counts: { total: number; downloaded: number; unresolved: number };
  assets_dir: string;
}

/**
 * Download + content-address the image/SVG assets an IR references via
 * ``scripts/pipeline.py assets``.  The IR JSON is staged to a temp file;
 * the CLI writes the content-addressed store under ``assetsDir``.
 */
export async function invokeAssets(
  cfg: { pythonBin: string; pluginDir: string },
  irJson: unknown,
  assetsDir: string,
): Promise<AssetManifest> {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "ff-assets-"));
  const irPath = path.join(tmp, "ir.json");
  fs.writeFileSync(irPath, JSON.stringify(irJson), "utf-8");
  try {
    const result = await spawnPython(
      cfg.pythonBin,
      path.join(cfg.pluginDir, "scripts", "pipeline.py"),
      ["assets", "--ir", irPath, "--assets-dir", assetsDir],
      cfg.pluginDir,
    );
    if (result.exitCode !== 0) {
      const detail = result.stderr.trim() || result.stdout.trim();
      throw new Error(
        `pipeline.py assets exited ${result.exitCode}: ${detail}`,
      );
    }
    return parseJsonLine(result.stdout) as unknown as AssetManifest;
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
}

/** Assets stage handler — irJson → assetManifest (shared + artifact). */
export function createAssetsStageHandler(): StageHandler {
  return async (ctx: PipelineContext) => {
    const irJson = ctx.shared.get("irJson");
    if (!irJson) {
      throw new Error("assets stage requires normalize output (no irJson available)");
    }
    const assetsDir = path.join(ctx.config.outputDir, ctx.config.runId, "assets");
    const manifest = await invokeAssets(
      { pythonBin: ctx.toolCtx.pythonBin, pluginDir: ctx.config.pluginDir },
      irJson,
      assetsDir,
    );
    ctx.shared.set("assetManifest", manifest);
    return { assetManifest: manifest };
  };
}

/**
 * Generate backend code from front-half stage artifacts (no recompute):
 * ``generate --ir … --layout … [--resolution …]``.
 */
export async function invokeBackendGeneratorFromStages(
  cfg: { pythonBin: string; pluginDir: string },
  target: CodegenTarget | string,
  stages: { irJson: unknown; layoutJson: unknown; resolutionJson?: unknown },
  outDir: string,
  options?: BackendInvokeOptions,
): Promise<BackendGenerateResult> {
  const backend = backendForTarget(target);

  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "ff-codegen-"));
  try {
    const irPath = path.join(tmp, "ir.json");
    const layoutPath = path.join(tmp, "layout.json");
    fs.writeFileSync(irPath, JSON.stringify(stages.irJson), "utf-8");
    fs.writeFileSync(layoutPath, JSON.stringify(stages.layoutJson), "utf-8");

    const args = [
      "generate",
      "--ir", irPath,
      "--layout", layoutPath,
      "--backend", backend,
      "--out-dir", outDir,
    ];
    if (stages.resolutionJson !== undefined) {
      const resolutionPath = path.join(tmp, "resolution.json");
      fs.writeFileSync(resolutionPath, JSON.stringify(stages.resolutionJson), "utf-8");
      args.push("--resolution", resolutionPath);
    }
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

/** Normalize stage handler — fileJson → irJson (shared + artifact). */
export function createNormalizeStageHandler(): StageHandler {
  return async (ctx: PipelineContext) => {
    const fileJson = ctx.shared.get("fileJson");
    if (!fileJson) {
      throw new Error("normalize stage requires ingest output (no fileJson available)");
    }
    const irJson = await invokeNormalize(
      { pythonBin: ctx.toolCtx.pythonBin, pluginDir: ctx.config.pluginDir },
      fileJson,
    );
    ctx.shared.set("irJson", irJson);
    return { irJson };
  };
}

/** Resolve stage handler — irJson → resolutionJson (shared + artifact). */
export function createResolveStageHandler(): StageHandler {
  return async (ctx: PipelineContext) => {
    const irJson = ctx.shared.get("irJson");
    if (!irJson) {
      throw new Error("resolve stage requires normalize output (no irJson available)");
    }
    const resolutionJson = await invokeResolve(
      { pythonBin: ctx.toolCtx.pythonBin, pluginDir: ctx.config.pluginDir },
      irJson,
    );
    ctx.shared.set("resolutionJson", resolutionJson);
    return { resolutionJson };
  };
}

/** Layout stage handler — irJson → layoutJson (shared + artifact). */
export function createLayoutStageHandler(): StageHandler {
  return async (ctx: PipelineContext) => {
    const irJson = ctx.shared.get("irJson");
    if (!irJson) {
      throw new Error("layout stage requires normalize output (no irJson available)");
    }
    const layoutJson = await invokeLayout(
      { pythonBin: ctx.toolCtx.pythonBin, pluginDir: ctx.config.pluginDir },
      irJson,
      ctx.config.viewport.width,
    );
    ctx.shared.set("layoutJson", layoutJson);
    return { layoutJson };
  };
}

/**
 * Ingest stage handler — fetches a Figma file (live, via ``--file-key``) or
 * reads a local fixture (via ``ctx.shared["filePath"]``), then stores the
 * normalized file JSON in shared state for downstream stages.
 */
export function createIngestStageHandler(): StageHandler {
  return async (ctx: PipelineContext, input: Record<string, unknown>) => {
    const filePath = ctx.shared.get("filePath");
    const fileKey = String(input.fileKey ?? ctx.config.fileKey ?? "");
    const result = await invokeIngest(
      { pythonBin: ctx.toolCtx.pythonBin, pluginDir: ctx.config.pluginDir },
      filePath ? { file: String(filePath) } : { fileKey },
    );
    ctx.shared.set("fileJson", result.fileJson);
    return { fileKey: result.fileKey, fileJson: result.fileJson };
  };
}

/**
 * Generate stage handler — lowers the pipeline's output through the
 * configured target's Python backend.  Prefers the staged front-half
 * artifacts (``--ir/--layout/[--resolution]``); falls back to the legacy
 * ``--file`` recompute path when only ingest output is available.
 */
export function createGenerateStageHandler(): StageHandler {
  return async (ctx: PipelineContext, input: Record<string, unknown>) => {
    const outDir = path.join(ctx.config.outputDir, ctx.config.runId, "generated");
    const cfg = { pythonBin: ctx.toolCtx.pythonBin, pluginDir: ctx.config.pluginDir };
    const options = { viewport: ctx.config.viewport.width };

    const irJson = ctx.shared.get("irJson") ?? input.irJson;
    const layoutJson = ctx.shared.get("layoutJson") ?? input.layoutJson;
    let result: BackendGenerateResult;
    if (irJson && layoutJson) {
      const resolutionJson = ctx.shared.get("resolutionJson") ?? input.resolutionJson;
      result = await invokeBackendGeneratorFromStages(
        cfg, ctx.config.target, { irJson, layoutJson, resolutionJson }, outDir, options,
      );
    } else {
      const fileJson = ctx.shared.get("fileJson") ?? input.fileJson;
      if (!fileJson) {
        throw new Error("generate stage requires ingest or front-half stage output");
      }
      result = await invokeBackendGenerator(cfg, ctx.config.target, fileJson, outDir, options);
    }

    ctx.shared.set("generatedManifest", result.manifest);
    return {
      backend: result.manifest.backend,
      filesDir: result.filesDir,
      manifest: result.manifest,
    };
  };
}
