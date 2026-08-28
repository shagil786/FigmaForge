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
import { defaultRenderer, targetKey } from "./types.js";
import type { PipelineContext, StageHandler } from "./pipeline.js";
import { ScreenshotComparator } from "./screenshot_compare.js";

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
  /** Part-17 asset manifest; downloaded entries thread into generated code. */
  assetsManifest?: unknown;
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
  options?: { timeoutMs?: number },
): Promise<PythonResult> {
  return new Promise((resolve, reject) => {
    const proc = spawn(pythonBin, [scriptPath, ...args], {
      cwd,
      env: { ...process.env, PYTHONIOENCODING: "utf-8" },
    });

    let stdout = "";
    let stderr = "";
    let settled = false;
    const timeoutMs = options?.timeoutMs;
    const timer = timeoutMs && timeoutMs > 0
      ? setTimeout(() => {
          stderr += `pipeline.py timed out after ${timeoutMs}ms`;
          proc.kill("SIGTERM");
          setTimeout(() => proc.kill("SIGKILL"), 2_000).unref();
        }, timeoutMs)
      : undefined;

    proc.stdout.on("data", (chunk: Buffer) => { stdout += chunk.toString(); });
    proc.stderr.on("data", (chunk: Buffer) => { stderr += chunk.toString(); });

    proc.on("close", (code: number | null) => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      resolve({ exitCode: code ?? 1, stdout, stderr });
    });
    proc.on("error", (err: Error) => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      reject(err);
    });
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
    if (options?.assetsManifest !== undefined) {
      const assetsPath = path.join(tmp, "assets.json");
      fs.writeFileSync(assetsPath, JSON.stringify(options.assetsManifest), "utf-8");
      args.push("--assets", assetsPath);
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
  /** Any image file path (screenshot, mockup, wireframe). */
  image?: string;
  /** Vision model provider (anthropic | openai). */
  imageProvider?: string;
  /** Vision model API key (or set via env var). */
  imageApiKey?: string;
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
    {
      timeoutMs: (() => {
        const configured = Number(
          process.env.FIGMAFORGE_FIGMA_STAGE_TIMEOUT_SECONDS ?? 60,
        );
        return (Number.isFinite(configured) && configured > 0
          ? configured : 60) * 1000;
      })(),
    },
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

/**
 * Analyze an image (screenshot, mockup, wireframe) via vision model and
 * return the resulting design IR via ``scripts/pipeline.py image_ingest``.
 *
 * The image is passed directly to the CLI — no staging needed since it's
 * a real file, not JSON.  The vision model extracts layout, colors,
 * typography, spacing, and component relationships into a design IR that
 * feeds the same layout → code pipeline as Figma JSON input.
 */
export async function invokeImageIngest(
  cfg: { pythonBin: string; pluginDir: string },
  source: {
    image: string;
    fileKey?: string;
    provider?: string;
    apiKey?: string;
  },
): Promise<{ fileKey: string; fileJson: Record<string, unknown> }> {
  const args = [
    "image_ingest",
    "--image", source.image,
  ];
  if (source.fileKey) {
    args.push("--file-key", source.fileKey);
  }
  if (source.provider) {
    args.push("--api-provider", source.provider);
  }
  if (source.apiKey) {
    args.push("--api-key", source.apiKey);
  }

  const result = await spawnPython(
    cfg.pythonBin,
    path.join(cfg.pluginDir, "scripts", "pipeline.py"),
    args,
    cfg.pluginDir,
    {
      timeoutMs: (() => {
        // Vision API calls can be slow for large images
        const configured = Number(
          process.env.FIGMAFORGE_IMAGE_STAGE_TIMEOUT_SECONDS ?? 120,
        );
        return (Number.isFinite(configured) && configured > 0
          ? configured : 120) * 1000;
      })(),
    },
  );
  if (result.exitCode !== 0) {
    const detail = result.stderr.trim() || result.stdout.trim();
    throw new Error(
      `pipeline.py image_ingest exited ${result.exitCode}: ${detail}`,
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
  subcommand: "audit" | "normalize" | "resolve" | "layout",
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
      {
        // The Python stage has its own 120s network budget.  Keep a process
        // boundary as well in case a resolver or child I/O operation ignores
        // that budget and never returns control to Python.
        timeoutMs: (() => {
          const configured = Number(
            process.env.FIGMAFORGE_ASSET_STAGE_TIMEOUT_SECONDS ?? 120,
          );
          return (Number.isFinite(configured) && configured > 0
            ? configured : 120) * 1000;
        })(),
      },
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
  stages: {
    irJson: unknown;
    layoutJson: unknown;
    resolutionJson?: unknown;
    assetsManifest?: unknown;
  },
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
    if (stages.assetsManifest !== undefined) {
      const assetsPath = path.join(tmp, "assets.json");
      fs.writeFileSync(assetsPath, JSON.stringify(stages.assetsManifest), "utf-8");
      args.push("--assets", assetsPath);
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
    // Image input: the ingest stage already produced a valid IRDocument —
    // skip Figma-specific audit + normalize entirely.
    const existingIr = ctx.shared.get("irJson");
    if (existingIr && ctx.shared.get("imageIngestSource")) {
      return { irJson: existingIr, sourceAudit: { ready_for_generation: true, image_source: true } };
    }
    const fileJson = ctx.shared.get("fileJson");
    if (!fileJson) {
      throw new Error("normalize stage requires ingest output (no fileJson available)");
    }
    const sourceAudit = await invokeJsonStage(
      { pythonBin: ctx.toolCtx.pythonBin, pluginDir: ctx.config.pluginDir },
      "audit",
      fileJson,
    );
    ctx.shared.set("sourceAudit", sourceAudit);
    if (sourceAudit.ready_for_generation !== true) {
      throw new Error(
        `Figma source is incomplete; generation stopped: ${JSON.stringify(sourceAudit)}`,
      );
    }
    const irJson = await invokeNormalize(
      { pythonBin: ctx.toolCtx.pythonBin, pluginDir: ctx.config.pluginDir },
      fileJson,
    );
    ctx.shared.set("irJson", irJson);
    return { irJson, sourceAudit };
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

// ---------------------------------------------------------------------------
// Render stage (Part 19) — browser screenshot of the generated output
// ---------------------------------------------------------------------------

/** One rendered generated file: path, screenshot, and measured meta. */
export interface RenderOutputRow {
  file: string;
  html: string;
  screenshot: string;
  meta: Record<string, unknown>;
}

/**
 * Render a generated standalone HTML file through the real Playwright
 * harness via ``scripts/pipeline.py render --html``.  ``outDir`` is the
 * persistent directory (``<run>/renders/``) where the PNG + written HTML
 * land — unlike the temp-staged invoke helpers, the output must survive.
 */
export async function invokeRender(
  cfg: { pythonBin: string; pluginDir: string },
  htmlPath: string,
  viewport: { width: number; height: number },
  outDir: string,
): Promise<{ screenshot: string; html: string; meta: Record<string, unknown> }> {
  fs.mkdirSync(outDir, { recursive: true });
  const result = await spawnPython(
    cfg.pythonBin,
    path.join(cfg.pluginDir, "scripts", "pipeline.py"),
    [
      "render", "--html", htmlPath,
      "--viewport", `${viewport.width}x${viewport.height}`,
      "--out", outDir,
    ],
    cfg.pluginDir,
  );
  if (result.exitCode !== 0) {
    const detail = result.stderr.trim() || result.stdout.trim();
    throw new Error(`pipeline.py render exited ${result.exitCode}: ${detail}`);
  }
  const parsed = parseJsonLine(result.stdout);
  return {
    screenshot: String(parsed.screenshot ?? ""),
    html: String(parsed.html ?? ""),
    meta: (parsed.meta ?? {}) as Record<string, unknown>,
  };
}

/**
 * Render a bundler-backed backend's generated output (react/vue/svelte)
 * through the real Vite harness via ``scripts/pipeline.py render --bundle``
 * (Part 21).  ``outDir`` (``<run>/renders/``) receives the built project
 * (``bundle/``) and the per-component screenshots (``screens/*.png``).
 * The shared asset manifest is converted from its list shape to the
 * harness's ``{node_id: {path}}`` contract and staged to a temp file.
 */
export async function invokeBundleRender(
  cfg: { pythonBin: string; pluginDir: string },
  backend: string,
  generatedDir: string,
  assetManifest: AssetManifest | undefined,
  viewport: { width: number; height: number },
  outDir: string,
): Promise<{
  backend: string;
  screens: Array<{ component: string; png: string; html: string }>;
  viewport: { width: number; height: number };
}> {
  fs.mkdirSync(outDir, { recursive: true });
  const args = [
    "render", "--bundle", "--backend", backend,
    "--dir", generatedDir,
    "--out", outDir,
    "--viewport", `${viewport.width}x${viewport.height}`,
  ];
  let manifestDir: string | null = null;
  if (assetManifest && assetManifest.assets.length > 0) {
    manifestDir = fs.mkdtempSync(path.join(os.tmpdir(), "ff-bundle-manifest-"));
    const byNode: Record<string, { path: string }> = {};
    for (const entry of assetManifest.assets) {
      if (entry.status === "downloaded" && entry.local_path) {
        byNode[entry.node_id] = { path: entry.local_path };
      }
    }
    fs.writeFileSync(
      path.join(manifestDir, "asset_manifest.json"),
      JSON.stringify(byNode),
      "utf-8",
    );
    args.push("--assets", path.join(manifestDir, "asset_manifest.json"));
  }
  try {
    const result = await spawnPython(
      cfg.pythonBin,
      path.join(cfg.pluginDir, "scripts", "pipeline.py"),
      args,
      cfg.pluginDir,
    );
    if (result.exitCode !== 0) {
      const detail = result.stderr.trim() || result.stdout.trim();
      throw new Error(
        `pipeline.py render (bundle) exited ${result.exitCode}: ${detail}`,
      );
    }
    const parsed = parseJsonLine(result.stdout);
    return {
      backend: String(parsed.backend ?? backend),
      screens: (parsed.screens ?? []) as Array<{
        component: string;
        png: string;
        html: string;
      }>,
      viewport: (parsed.viewport ?? viewport) as {
        width: number;
        height: number;
      },
    };
  } finally {
    if (manifestDir) {
      fs.rmSync(manifestDir, { recursive: true, force: true });
    }
  }
}

/** Bundler-backed backends the render stage can measure via ``render --bundle``. */
const BUNDLE_BACKENDS = new Set(["react_tailwind", "vue", "svelte"]);

/**
 * Render stage handler — generated code → browser screenshot + metadata.
 *
 * Browser-renderable targets with directly-renderable HTML (html_css
 * standalone files today) are rendered through the real harness; each file
 * becomes a ``RenderOutputRow`` stored in shared ``renderOutputs``.
 * Bundler-backed targets (react/vue/svelte) are measured through the real
 * Vite harness (``render --bundle``) — their screenshots feed the same
 * compare/verify machinery.  Honest degradation (never a fabricated
 * score): native renderers, ``--no-bundle``, and un-harnessed outputs
 * return a ``{note, screenshotPath: null}`` payload instead of invoking
 * Python.
 */
export function createRenderStageHandler(opts?: {
  noBundle?: boolean;
  bundleInvoker?: typeof invokeBundleRender;
}): StageHandler {
  return async (ctx: PipelineContext) => {
    const rendersDir = path.join(ctx.config.outputDir, ctx.config.runId, "renders");

    // Native targets (xcode_preview, flutter_simulator, …) cannot render in
    // a browser — degrade before touching the generated files.
    if (defaultRenderer(ctx.config.target.framework) !== "browser") {
      return {
        note: `Visual comparison for ${targetKey(ctx.config.target)} requires ` +
          `${defaultRenderer(ctx.config.target.framework)}; no browser screenshot available.`,
        screenshotPath: null,
        rendersDir,
      };
    }

    const generatedManifest = ctx.shared.get("generatedManifest") as
      BackendManifest | undefined;
    if (!generatedManifest) {
      throw new Error(
        "render stage requires generate output (no generatedManifest available)",
      );
    }
    const filesDir = path.join(
      ctx.config.outputDir, ctx.config.runId, "generated", generatedManifest.backend,
    );
    if (!fs.existsSync(filesDir)) {
      throw new Error(`render stage: generated files dir missing: ${filesDir}`);
    }
    const htmlFiles = fs.readdirSync(filesDir)
      .filter((f) => f.endsWith(".html"))
      .sort();
    if (htmlFiles.length === 0) {
      // No directly-renderable HTML: bundler-backed backends are measured
      // through the real Vite harness; everything else degrades honestly
      // (never a fabricated score).
      const backend = generatedManifest.backend;
      if (opts?.noBundle) {
        return {
          note: `--no-bundle: generated output for ${backend} has no ` +
            "directly-renderable HTML; no measured score.",
          screenshotPath: null,
          rendersDir,
        };
      }
      if (BUNDLE_BACKENDS.has(backend)) {
        const bundle = opts?.bundleInvoker ?? invokeBundleRender;
        const assetManifest = ctx.shared.get("assetManifest") as
          AssetManifest | undefined;
        const result = await bundle(
          { pythonBin: ctx.toolCtx.pythonBin, pluginDir: ctx.config.pluginDir },
          backend,
          filesDir,
          assetManifest,
          ctx.config.viewport,
          rendersDir,
        );
        const screenshots: RenderOutputRow[] = result.screens.map((s) => ({
          file: s.html,
          html: path.join(rendersDir, "bundle", "dist", s.html),
          screenshot: path.join(rendersDir, s.png),
          meta: {},
        }));
        ctx.shared.set("renderOutputs", screenshots);
        return { screenshots, rendersDir, bundle: result.backend };
      }
      return {
        note: `generated output for ${backend} has no directly-renderable HTML ` +
          "and no bundler harness; no measured score.",
        screenshotPath: null,
        rendersDir,
      };
    }

    fs.mkdirSync(rendersDir, { recursive: true });
    const cfg = { pythonBin: ctx.toolCtx.pythonBin, pluginDir: ctx.config.pluginDir };
    const screenshots: RenderOutputRow[] = [];
    for (const file of htmlFiles) {
      const result = await invokeRender(
        cfg,
        path.join(filesDir, file),
        ctx.config.viewport,
        rendersDir,
      );
      screenshots.push({
        file,
        html: result.html,
        screenshot: result.screenshot,
        meta: result.meta,
      });
    }
    ctx.shared.set("renderOutputs", screenshots);
    return { screenshots, rendersDir };
  };
}

// ---------------------------------------------------------------------------
// Compare stage (Part 19) — measured similarity vs a baseline
// ---------------------------------------------------------------------------

/**
 * Render the IR reference (the intended render) via ``pipeline.py render
 * --ir + --layout``.  ``outDir`` (``<run>/baselines/``) holds the baseline
 * PNG + written HTML; the JSON is staged to a temp file like the other
 * stage invocations.
 */
export async function invokeRenderReference(
  cfg: { pythonBin: string; pluginDir: string },
  irJson: unknown,
  layoutJson: unknown,
  viewport: { width: number; height: number },
  outDir: string,
): Promise<{ screenshot: string; html: string; meta: Record<string, unknown> }> {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "ff-ref-"));
  try {
    const irPath = path.join(tmp, "ir.json");
    const layoutPath = path.join(tmp, "layout.json");
    fs.writeFileSync(irPath, JSON.stringify(irJson), "utf-8");
    fs.writeFileSync(layoutPath, JSON.stringify(layoutJson), "utf-8");
    const result = await spawnPython(
      cfg.pythonBin,
      path.join(cfg.pluginDir, "scripts", "pipeline.py"),
      [
        "render", "--ir", irPath, "--layout", layoutPath,
        "--viewport", `${viewport.width}x${viewport.height}`,
        "--out", outDir,
      ],
      cfg.pluginDir,
    );
    if (result.exitCode !== 0) {
      const detail = result.stderr.trim() || result.stdout.trim();
      throw new Error(
        `pipeline.py render (reference) exited ${result.exitCode}: ${detail}`,
      );
    }
    const parsed = parseJsonLine(result.stdout);
    return {
      screenshot: String(parsed.screenshot ?? ""),
      html: String(parsed.html ?? ""),
      meta: (parsed.meta ?? {}) as Record<string, unknown>,
    };
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
}

/**
 * Download live Figma baselines via ``pipeline.py render --baselines``.
 * Requires ``FIGMA_TOKEN`` + a real file key (exit 3/2 from the CLI, surfaced
 * as a typed stage error).  Returns ``node_id -> local_path``.
 */
export async function invokeRenderBaselines(
  cfg: { pythonBin: string; pluginDir: string },
  fileKey: string,
  nodeIds: string[],
  outDir: string,
): Promise<Record<string, string>> {
  fs.mkdirSync(outDir, { recursive: true });
  const result = await spawnPython(
    cfg.pythonBin,
    path.join(cfg.pluginDir, "scripts", "pipeline.py"),
    [
      "render", "--baselines",
      "--file-key", fileKey,
      "--nodes", nodeIds.join(","),
      "--out", outDir,
    ],
    cfg.pluginDir,
  );
  if (result.exitCode !== 0) {
    const detail = result.stderr.trim() || result.stdout.trim();
    throw new Error(
      `pipeline.py render (baselines) exited ${result.exitCode}: ${detail}`,
    );
  }
  const parsed = parseJsonLine(result.stdout);
  return (parsed.baselines ?? {}) as Record<string, string>;
}

/**
 * Compare stage handler — screenshots vs a baseline → measured similarity.
 *
 * Baseline resolution (priority): shared ``baselinePath`` (explicit
 * ``--baseline``) → shared ``figmaBaseline`` flag (live ``download_baselines``,
 * token-gated) → the IR reference render (default).  Each screenshot row is
 * compared with the SSIM-gated ``ScreenshotComparator``; the headline score
 * is the mean across screens.  The diff report (shaped like the Python
 * ``DiffReport``) is stored as the ``diff_report`` artifact AND the score is
 * written into run metrics via ``ctx.updateMetrics`` so ``figmaforge run``
 * prints a real measured Score.  No screenshots (render degraded) → null
 * score + note, metrics untouched — never a fabricated score.
 */
export function createCompareStageHandler(): StageHandler {
  return async (ctx: PipelineContext) => {
    const rendersDir = path.join(ctx.config.outputDir, ctx.config.runId, "renders");
    const baselinesDir = path.join(ctx.config.outputDir, ctx.config.runId, "baselines");
    const renderOutputs = ctx.shared.get("renderOutputs") as RenderOutputRow[] | undefined;

    if (!renderOutputs || renderOutputs.length === 0) {
      return {
        similarity_score: null,
        categories: { geometry: null, style: null, pixels: null },
        raster_stats: null,
        screens: [],
        baseline: null,
        baseline_kind: null,
        resized: false,
        note: "no screenshots to compare — the render stage degraded " +
          "(non-browser target or bundler-required output); no measured score.",
      };
    }

    const cfg = { pythonBin: ctx.toolCtx.pythonBin, pluginDir: ctx.config.pluginDir };

    // Baseline resolution.
    let baseline: string;
    let baselineKind: "explicit" | "figma" | "reference";
    const explicit = ctx.shared.get("baselinePath") as string | undefined;
    if (explicit) {
      if (!fs.existsSync(explicit)) {
        throw new Error(`compare stage: --baseline file not found: ${explicit}`);
      }
      baseline = explicit;
      baselineKind = "explicit";
    } else if (ctx.shared.get("figmaBaseline")) {
      const layoutJson = ctx.shared.get("layoutJson") as
        | { screens?: Array<{ node_id: string }> }
        | undefined;
      const nodeIds = (layoutJson?.screens ?? [])
        .map((s) => s.node_id)
        .filter((n) => n.length > 0);
      if (nodeIds.length === 0) {
        throw new Error(
          "compare stage (--figma-baseline): no screen node ids in the layout plan",
        );
      }
      const baselines = await invokeRenderBaselines(
        cfg, ctx.config.fileKey, nodeIds, baselinesDir,
      );
      const first = nodeIds.find((n) => baselines[n]);
      if (!first) {
        throw new Error(
          "compare stage (--figma-baseline): no baseline downloaded for the screens",
        );
      }
      baseline = baselines[first];
      baselineKind = "figma";
    } else {
      const irJson = ctx.shared.get("irJson");
      const layoutJson = ctx.shared.get("layoutJson");
      if (!irJson || !layoutJson) {
        throw new Error(
          "compare stage requires normalize/layout output for the reference " +
          "baseline (no irJson/layoutJson available)",
        );
      }
      const ref = await invokeRenderReference(
        cfg, irJson, layoutJson, ctx.config.viewport, baselinesDir,
      );
      baseline = ref.screenshot;
      baselineKind = "reference";
    }

    // Figma exports are commonly at the design's native pixel dimensions,
    // while browser captures use the configured viewport.  Compare them at a
    // common resolution so a valid visual signal is produced instead of the
    // pixel comparator's size-mismatch sentinel (similarity 0).
    const comparator = new ScreenshotComparator(
      { colorThreshold: 16, resize: baselineKind === "figma" },
      cfg,
    );
    const screens: Array<{
      file: string;
      similarity: number;
      ssim: number | null;
      ssimClean: boolean | null;
    }> = [];
    let totalSimilarity = 0;
    let firstStats: {
      ssim: number | null;
      minRegionSsim: number | null;
      ssimClean: boolean | null;
      diffPercentage: number;
      meanAbsoluteError: { r: number; g: number; b: number };
    } | null = null;
    for (const row of renderOutputs) {
      const cmp = comparator.compare(row.screenshot, baseline);
      if (firstStats === null) {
        firstStats = {
          ssim: cmp.ssim ?? null,
          minRegionSsim: cmp.minRegionSsim ?? null,
          ssimClean: cmp.ssimClean ?? null,
          diffPercentage: cmp.diffPercentage,
          meanAbsoluteError: cmp.meanAbsoluteError,
        };
      }
      screens.push({
        file: row.file,
        similarity: cmp.similarity,
        ssim: cmp.ssim ?? null,
        ssimClean: cmp.ssimClean ?? null,
      });
      totalSimilarity += cmp.similarity;
    }
    const overall = screens.length > 0
      ? totalSimilarity / screens.length
      : 0;

    const report = {
      similarity_score: overall,
      categories: { geometry: null, style: null, pixels: overall },
      raster_stats: firstStats
        ? {
            ssim: firstStats.ssim,
            min_region_ssim: firstStats.minRegionSsim,
            ssim_clean: firstStats.ssimClean,
            diff_percentage: firstStats.diffPercentage,
            mae: firstStats.meanAbsoluteError,
          }
        : null,
      screens,
      baseline,
      baseline_kind: baselineKind,
      resized: baselineKind === "figma",
      note: baselineKind === "figma"
        ? "Figma baseline was resized to common comparison dimensions"
        : null,
    };
    ctx.shared.set("diffReport", report);
    // Share the resolved baseline so the repair/verify stages consume the
    // exact PNG + kind this stage compared against (Part 20).
    ctx.shared.set("compareBaseline", baseline);
    ctx.shared.set("compareBaselineKind", baselineKind);
    ctx.updateMetrics({ similarityScore: overall });
    return report;
  };
}

// ---------------------------------------------------------------------------
// Repair stage (Part 20) — real RepairLoop against an external baseline
// ---------------------------------------------------------------------------

/** Options threaded into ``pipeline.py repair``. */
export interface RepairOptions {
  viewport: { width: number; height: number };
  maxIterations?: number;
  threshold?: number;
  /**
   * The backend to regenerate (Part 22): the run's generated backend, so
   * the fixes reach the SAME code the run rendered (html_css default when
   * absent).  Python rejects native backends honestly (exit 2).
   */
  backend?: string;
  /** Resolution report (F1) — component refs survive regeneration. */
  resolutionJson?: unknown;
  /** Shared asset manifest — image fills survive regeneration (Part 18). */
  assetsManifest?: unknown;
}

/**
 * Run the real Python repair loop via ``scripts/pipeline.py repair``.
 * Stages IR + layout to temp files (like the other staged invoke helpers),
 * spawns the loop against the resolved external baseline, and parses the
 * single JSON line.  Nonzero exit → typed error with stderr detail.  The
 * loop converges by mutating the shared layer; regenerated html_css lands
 * under ``outDir/generated/html_css``, repaired styles + full history under
 * ``outDir/``.
 */
export async function invokeRepair(
  cfg: { pythonBin: string; pluginDir: string },
  irJson: unknown,
  layoutJson: unknown,
  baseline: string,
  outDir: string,
  opts: RepairOptions,
): Promise<Record<string, unknown>> {
  fs.mkdirSync(outDir, { recursive: true });
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "ff-repair-"));
  try {
    const irPath = path.join(tmp, "ir.json");
    const layoutPath = path.join(tmp, "layout.json");
    fs.writeFileSync(irPath, JSON.stringify(irJson), "utf-8");
    fs.writeFileSync(layoutPath, JSON.stringify(layoutJson), "utf-8");
    const args = [
      "repair", "--ir", irPath, "--layout", layoutPath,
      "--baseline", baseline,
      "--out", outDir,
      "--viewport", `${opts.viewport.width}x${opts.viewport.height}`,
    ];
    if (opts.maxIterations !== undefined) {
      args.push("--max-iterations", String(opts.maxIterations));
    }
    if (opts.threshold !== undefined) {
      args.push("--threshold", String(opts.threshold));
    }
    if (opts.backend !== undefined) {
      args.push("--backend", opts.backend);
    }
    if (opts.resolutionJson !== undefined) {
      const resolutionPath = path.join(tmp, "resolution.json");
      fs.writeFileSync(resolutionPath, JSON.stringify(opts.resolutionJson), "utf-8");
      args.push("--resolution", resolutionPath);
    }
    if (opts.assetsManifest !== undefined) {
      const assetsPath = path.join(tmp, "assets.json");
      fs.writeFileSync(assetsPath, JSON.stringify(opts.assetsManifest), "utf-8");
      args.push("--assets", assetsPath);
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
        `pipeline.py repair exited ${result.exitCode}: ${detail}`,
      );
    }
    return parseJsonLine(result.stdout);
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
}

/**
 * Repair stage handler — measure, then auto-repair toward the baseline.
 *
 * Reads the compare stage's shared resolved baseline + diff report.  Honest
 * short-circuits that NEVER spawn Python: no measured score (render degraded)
 * → ``{repairs: 0, success: null}``; gate already satisfied (score ≥
 * threshold) → ``{repairs: 0}``; ``baseline_kind === "reference"`` → the
 * by-construction contract (the reference render IS the intended render — a
 * low score there is a codegen regression the verify stage catches, not
 * something repair can converge against).  Otherwise it spawns the real
 * RepairLoop into ``<run>/repair/``, shares the outputs for the verify stage
 * (``repairOut``/``repairManifest``/``repairStylesPath``), and bumps the
 * budget ``repairIterations`` by the real iterations so the ``Repairs:``
 * summary line is honest.
 */
export function createRepairStageHandler(): StageHandler {
  return async (ctx: PipelineContext) => {
    const threshold = ctx.config.similarityThreshold;
    const repairDir = path.join(ctx.config.outputDir, ctx.config.runId, "repair");
    const report = ctx.shared.get("diffReport") as
      | { similarity_score: number | null }
      | undefined;
    const baseline = ctx.shared.get("compareBaseline") as string | undefined;
    const baselineKind = ctx.shared.get("compareBaselineKind") as string | undefined;

    const inert = (note: string) => ({
      repairs: 0,
      success: null,
      iterations_run: 0,
      final_score: report?.similarity_score ?? null,
      stop_reason: null,
      repaired_styles: null,
      repaired_styles_path: null,
      generated: null,
      out_dir: repairDir,
      note,
    });

    // Short-circuit 0: explicitly disabled (--no-repair).
    if (ctx.shared.get("noRepair")) {
      return inert("repair disabled (--no-repair)");
    }
    // Short-circuit 1: no measured score — nothing to repair.
    if (!baseline || !report || report.similarity_score === null) {
      return inert("no measured score — nothing to repair");
    }
    // Short-circuit 2: the gate is already satisfied.
    if (report.similarity_score >= threshold) {
      return inert("gate already satisfied");
    }
    // Short-circuit 3: reference baseline — the by-construction contract.
    if (baselineKind === "reference") {
      return inert(
        "reference baseline is the intended render; a low score is a codegen " +
        "regression the verify stage will catch — nothing to repair",
      );
    }

    // Real repair: spawn the Python loop against the shared external baseline.
    const irJson = ctx.shared.get("irJson");
    const layoutJson = ctx.shared.get("layoutJson");
    if (!irJson || !layoutJson) {
      throw new Error(
        "repair stage requires normalize/layout output (no irJson/layoutJson available)",
      );
    }
    // The run's generated backend is the one repair regenerates (Part 22)
    // — so the fixes reach the SAME code the run rendered (bundler-backed
    // react/vue/svelte through the Vite harness, not html_css).  Missing
    // generatedManifest (defensive, F7) → html_css default with a note.
    const generatedManifest = ctx.shared.get("generatedManifest") as
      | { backend: string }
      | undefined;
    const resolutionJson = ctx.shared.get("resolutionJson");
    const assetManifest = ctx.shared.get("assetManifest") as
      | AssetManifest
      | undefined;
    const cfg = { pythonBin: ctx.toolCtx.pythonBin, pluginDir: ctx.config.pluginDir };
    const payload = await invokeRepair(
      cfg,
      irJson,
      layoutJson,
      baseline,
      repairDir,
      {
        viewport: ctx.config.viewport,
        maxIterations: ctx.config.budgets.maxRepairIterations,
        threshold,
        backend: generatedManifest?.backend ?? "html_css",
        resolutionJson,
        assetsManifest: assetManifest,
      },
    );
    const iterationsRun = Number(payload.iterations_run ?? 0);
    // The coordinator overwrites metrics.repairIterations from the budget
    // after each stage, so bump the real budget — not just the metrics.
    for (let i = 0; i < iterationsRun; i++) {
      ctx.budget.addRepairIteration();
    }

    const stylesPath = path.join(repairDir, "styles.repaired.json");
    const result = {
      ok: payload.ok ?? true,
      success: (payload.success as boolean | null) ?? null,
      final_score: (payload.final_score as number | null) ?? null,
      iterations_run: iterationsRun,
      stop_reason: (payload.stop_reason as string | null) ?? null,
      repairs: payload.repairs ?? [],
      categories: payload.categories ?? null,
      repaired_styles: (payload.repaired_styles as string | null) ?? null,
      repaired_styles_path: fs.existsSync(stylesPath) ? stylesPath : null,
      generated: (payload.generated as
        | { backend: string; files: Array<{ path: string }> }
        | null) ?? null,
      out_dir: repairDir,
      note: generatedManifest
        ? null
        : "no generatedManifest — regenerated html_css (default)",
    };
    ctx.shared.set("repairOut", repairDir);
    ctx.shared.set("repairStylesPath", result.repaired_styles_path);
    ctx.shared.set("repairManifest", payload);
    return result;
  };
}

// ---------------------------------------------------------------------------
// Verify stage (Part 20) — final measured pass/fail gate
// ---------------------------------------------------------------------------

/**
 * Verify stage handler — the terminal gate after repair.
 *
 * If repair regenerated files, re-renders each regenerated file through the
 * real harness into ``<run>/verify-renders/`` and compares it against the
 * SAME baseline the compare stage resolved (via the shared
 * ``compareBaseline``), giving the honest post-repair measurement.  If no
 * repair ran, reuses the compare stage's diff-report score — the final check
 * of the same measurement.  ``passed = score >= threshold`` (shared/config).
 * No screenshots anywhere → ``{passed: null, note: "no measured score —
 * cannot verify"}`` — never a fabricated pass/fail.  Writes the score into
 * run metrics via ``ctx.updateMetrics`` so the run's final Score + a
 * ``Verification:`` line reflect the verified result.
 *
 * Post-repair re-rendering (Part 22): a bundler-backed regenerated backend
 * (react/vue/svelte) is re-rendered through the real Vite harness
 * (``opts.bundleInvoker``, injectable for tests) — the same machinery as
 * the render stage — so the re-measurement covers the actual built output
 * against the same baseline.  html_css keeps the per-file path; a native
 * regenerated backend is a defensive inert note (never a fabricated score).
 */
export function createVerifyStageHandler(opts?: {
  bundleInvoker?: typeof invokeBundleRender;
}): StageHandler {
  return async (ctx: PipelineContext) => {
    const threshold =
      (ctx.shared.get("similarityThreshold") as number | undefined) ??
      ctx.config.similarityThreshold;
    const baseline = ctx.shared.get("compareBaseline") as string | undefined;
    const baselineKind = ctx.shared.get("compareBaselineKind") as string | null | undefined;
    const report = ctx.shared.get("diffReport") as
      | {
          similarity_score: number | null;
          screens: Array<{
            file: string;
            similarity: number;
            ssim: number | null;
            ssimClean: boolean | null;
          }>;
        }
      | undefined;

    if (!baseline || !report || report.similarity_score === null) {
      return {
        passed: null,
        similarity_score: null,
        threshold,
        baseline_kind: baselineKind ?? null,
        screens: [],
        source: null,
        note: "no measured score — cannot verify",
      };
    }

    // Post-repair path: re-render the regenerated files for a fresh score.
    const repairManifest = ctx.shared.get("repairManifest") as
      | {
          generated: { backend: string; files: Array<{ path: string }> } | null;
        }
      | undefined;
    const generated = repairManifest?.generated;
    if (generated && generated.files.length > 0) {
      const cfg = { pythonBin: ctx.toolCtx.pythonBin, pluginDir: ctx.config.pluginDir };
      const verifyDir = path.join(ctx.config.outputDir, ctx.config.runId, "verify-renders");
      const genDir = path.join(
        ctx.config.outputDir, ctx.config.runId,
        "repair", "generated", generated.backend,
      );
      fs.mkdirSync(verifyDir, { recursive: true });
      const comparator = new ScreenshotComparator(
        { colorThreshold: 16, resize: baselineKind === "figma" },
        cfg,
      );
      const screens: Array<{
        file: string;
        similarity: number;
        ssim: number | null;
        ssimClean: boolean | null;
      }> = [];
      let total = 0;
      if (BUNDLE_BACKENDS.has(generated.backend)) {
        // Bundler-backed regenerated output (react/vue/svelte, Part 22):
        // re-build through the real Vite harness — same machinery as the
        // render stage — so the re-measurement covers the actual built
        // output against the same baseline.
        const bundle = opts?.bundleInvoker ?? invokeBundleRender;
        const assetManifest = ctx.shared.get("assetManifest") as
          | AssetManifest
          | undefined;
        const result = await bundle(
          cfg,
          generated.backend,
          genDir,
          assetManifest,
          ctx.config.viewport,
          verifyDir,
        );
        for (const s of result.screens) {
          const screenshot = path.join(verifyDir, s.png);
          if (!fs.existsSync(screenshot)) continue;
          const cmp = comparator.compare(screenshot, baseline);
          screens.push({
            file: s.html,
            similarity: cmp.similarity,
            ssim: cmp.ssim ?? null,
            ssimClean: cmp.ssimClean ?? null,
          });
          total += cmp.similarity;
        }
      } else if (generated.backend !== "html_css") {
        // Native regenerated backend (defensive guard): no browser harness —
        // an honest inert note, never a fabricated score or a spawn.
        return {
          passed: null,
          similarity_score: null,
          threshold,
          baseline_kind: baselineKind ?? null,
          screens: [],
          source: "re-rendered",
          note: `cannot re-render ${generated.backend} output (no browser harness)`,
        };
      } else {
        for (const f of [...generated.files].sort((a, b) => a.path.localeCompare(b.path))) {
          const htmlPath = path.join(genDir, f.path);
          if (!fs.existsSync(htmlPath)) continue;
          const shot = await invokeRender(cfg, htmlPath, ctx.config.viewport, verifyDir);
          const cmp = comparator.compare(shot.screenshot, baseline);
          screens.push({
            file: f.path,
            similarity: cmp.similarity,
            ssim: cmp.ssim ?? null,
            ssimClean: cmp.ssimClean ?? null,
          });
          total += cmp.similarity;
        }
      }
      if (screens.length === 0) {
        return {
          passed: null,
          similarity_score: null,
          threshold,
          baseline_kind: baselineKind ?? null,
          screens: [],
          source: "re-rendered",
          note: "no regenerated files could be re-rendered — cannot verify",
        };
      }
      const score = total / screens.length;
      const passed = score >= threshold;
      ctx.updateMetrics({ similarityScore: score });
      return {
        passed,
        similarity_score: score,
        threshold,
        baseline_kind: baselineKind ?? null,
        screens,
        source: "re-rendered",
        note: null,
      };
    }

    // No-repair path: reuse the compare measurement — the final check of the
    // same screens against the same baseline.
    const score = report.similarity_score;
    const passed = score >= threshold;
    ctx.updateMetrics({ similarityScore: score });
    return {
      passed,
      similarity_score: score,
      threshold,
      baseline_kind: baselineKind ?? null,
      screens: report.screens ?? [],
      source: "compare",
      note: null,
    };
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
    const imagePath = ctx.shared.get("imagePath") as string | undefined;
    const fileKey = String(input.fileKey ?? ctx.config.fileKey ?? "");
    const cfg = { pythonBin: ctx.toolCtx.pythonBin, pluginDir: ctx.config.pluginDir };

    let result: { fileKey: string; fileJson: Record<string, unknown> };
    if (imagePath) {
      // Image-to-IR path: vision model extracts structure from any image.
      // The image_analyzer produces a valid IRDocument directly, so we store
      // it as both fileJson (for backward compat) AND irJson (so downstream
      // stages skip the Figma-specific normalize/audit).
      result = await invokeImageIngest(
        cfg,
        {
          image: imagePath,
          fileKey: fileKey || undefined,
          provider: ctx.shared.get("imageProvider") as string | undefined,
          apiKey: ctx.shared.get("imageApiKey") as string | undefined,
        },
      );
      // The image analyzer output IS the IR — store it so normalize skips
      ctx.shared.set("irJson", result.fileJson);
      ctx.shared.set("imageIngestSource", true);
    } else {
      // Figma JSON path: local file or live API
      result = await invokeIngest(
        cfg,
        filePath ? { file: String(filePath) } : { fileKey },
      );
    }
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
    const assetsManifest = ctx.shared.get("assetManifest") ?? input.assetManifest;
    let result: BackendGenerateResult;
    if (irJson && layoutJson) {
      const resolutionJson = ctx.shared.get("resolutionJson") ?? input.resolutionJson;
      result = await invokeBackendGeneratorFromStages(
        cfg, ctx.config.target,
        { irJson, layoutJson, resolutionJson, assetsManifest }, outDir, options,
      );
    } else {
      const fileJson = ctx.shared.get("fileJson") ?? input.fileJson;
      if (!fileJson) {
        throw new Error("generate stage requires ingest or front-half stage output");
      }
      result = await invokeBackendGenerator(
        cfg, ctx.config.target, fileJson, outDir,
        { ...options, assetsManifest },
      );
    }

    ctx.shared.set("generatedManifest", result.manifest);
    return {
      backend: result.manifest.backend,
      filesDir: result.filesDir,
      manifest: result.manifest,
    };
  };
}
