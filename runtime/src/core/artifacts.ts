/**
 * Artifact storage for pipeline outputs.
 *
 * Every stage produces artifacts (IR JSON, generated code, screenshots,
 * diff reports, patches). The artifact store tracks them by type and
 * provides content-addressed storage.
 */

import * as fs from "node:fs";
import * as path from "node:path";
import * as crypto from "node:crypto";
import type { PipelineStage, RunId } from "./types.js";

// ---------------------------------------------------------------------------
// Artifact types
// ---------------------------------------------------------------------------

export type ArtifactKind =
  | "adaptive_plan"     // Adaptive preflight routing plan
  | "figma_raw"         // Raw Figma API JSON
  | "design_ir"         // Normalized Design IR
  | "resolution_report" // Component/token resolution
  | "layout_plan"       // Layout inference output
  | "generated_code"    // VNode/VStyle (React/CSS)
  | "asset_manifest"    // Asset hash manifest
  | "screenshot"        // Browser render screenshot
  | "render_meta"       // Browser layout metadata
  | "diff_report"       // Visual comparison report
  | "repair_plan"       // Patch plan
  | "repair_result"     // Patch execution result
  | "repair_history"    // Full repair iteration history
  | "event_log"         // Structured event log
  | "checkpoint"        // Run checkpoint
  | "metrics";          // Evaluation metrics

export type ArtifactStage = PipelineStage | "preflight";

export interface Artifact {
  /** Unique artifact ID (content hash). */
  id: string;
  /** Artifact kind. */
  kind: ArtifactKind;
  /** Pipeline stage that produced this artifact. */
  stage: ArtifactStage;
  /** Run ID. */
  runId: RunId;
  /** File path relative to the run's output directory. */
  path: string;
  /** Content hash (SHA-256). */
  hash: string;
  /** Size in bytes. */
  size: number;
  /** ISO-8601 timestamp. */
  createdAt: string;
  /** Optional human-readable label. */
  label?: string;
}

export interface ArtifactManifest {
  runId: RunId;
  artifacts: Artifact[];
}

// ---------------------------------------------------------------------------
// Artifact store
// ---------------------------------------------------------------------------

export class ArtifactStore {
  private artifacts: Artifact[] = [];
  private baseDir: string;

  constructor(
    private readonly runId: RunId,
    outputDir: string,
  ) {
    this.baseDir = path.join(outputDir, runId, "artifacts");
    this.loadManifestFromDisk();
  }

  /** Initialize the artifact directory. */
  init(): void {
    fs.mkdirSync(this.baseDir, { recursive: true });
  }

  /** Store an artifact from a JSON-serializable value. */
  storeJSON(
    kind: ArtifactKind,
    stage: ArtifactStage,
    name: string,
    data: unknown,
  ): Artifact {
    this.init();
    const json = JSON.stringify(data, null, 2);
    const hash = crypto.createHash("sha256").update(json).digest("hex").slice(0, 16);
    const filePath = path.join(this.baseDir, `${stage}_${name}_${hash}.json`);
    fs.writeFileSync(filePath, json, "utf-8");

    const artifact: Artifact = {
      id: hash,
      kind,
      stage,
      runId: this.runId,
      path: path.relative(this.baseDir, filePath),
      hash,
      size: Buffer.byteLength(json),
      createdAt: new Date().toISOString(),
      label: name,
    };
    this.artifacts.push(artifact);
    return artifact;
  }

  /** Load the newest JSON artifact for a kind/stage pair when resuming a run. */
  loadLatestJSON(kind: ArtifactKind, stage: ArtifactStage): unknown | null {
    const candidates = this.artifacts
      .filter((artifact) => artifact.kind === kind && artifact.stage === stage)
      .sort((a, b) => b.createdAt.localeCompare(a.createdAt));

    for (const candidate of candidates) {
      try {
        return JSON.parse(fs.readFileSync(path.join(this.baseDir, candidate.path), "utf-8"));
      } catch {
        // Ignore corrupt candidates and keep searching older artifacts.
      }
    }
    return null;
  }

  /** Store an artifact from a binary buffer (e.g. screenshot). */
  storeBuffer(
    kind: ArtifactKind,
    stage: ArtifactStage,
    name: string,
    buffer: Buffer,
    ext: string = "png",
  ): Artifact {
    this.init();
    const hash = crypto.createHash("sha256").update(buffer).digest("hex").slice(0, 16);
    const filePath = path.join(this.baseDir, `${stage}_${name}_${hash}.${ext}`);
    fs.writeFileSync(filePath, buffer);

    const artifact: Artifact = {
      id: hash,
      kind,
      stage,
      runId: this.runId,
      path: path.relative(this.baseDir, filePath),
      hash,
      size: buffer.length,
      createdAt: new Date().toISOString(),
      label: name,
    };
    this.artifacts.push(artifact);
    return artifact;
  }

  /** Load a stored artifact's JSON content. */
  loadJSON(artifact: Artifact): unknown {
    const fullPath = path.join(this.baseDir, artifact.path);
    return JSON.parse(fs.readFileSync(fullPath, "utf-8"));
  }

  /** Get all artifacts for a stage. */
  byStage(stage: ArtifactStage): Artifact[] {
    return this.artifacts.filter((a) => a.stage === stage);
  }

  /** Get all artifacts of a kind. */
  byKind(kind: ArtifactKind): Artifact[] {
    return this.artifacts.filter((a) => a.kind === kind);
  }

  /** Get the full manifest. */
  manifest(): ArtifactManifest {
    return { runId: this.runId, artifacts: [...this.artifacts] };
  }

  /**
   * Remove oldest non-protected artifacts until optional retention limits hold.
   * Provenance artifacts can be protected by kind and are never deleted.
   */
  prune(options: {
    maxArtifacts?: number;
    maxBytes?: number;
    preserveKinds?: ArtifactKind[];
  } = {}): number {
    const maxArtifacts = options.maxArtifacts;
    const maxBytes = options.maxBytes;
    if (maxArtifacts === undefined && maxBytes === undefined) return 0;
    if (maxArtifacts !== undefined && maxArtifacts < 0) {
      throw new Error("maxArtifacts must be non-negative");
    }
    if (maxBytes !== undefined && maxBytes < 0) {
      throw new Error("maxBytes must be non-negative");
    }

    const preserved = new Set(options.preserveKinds ?? []);
    const candidates = this.artifacts
      .filter((artifact) => !preserved.has(artifact.kind))
      .sort((a, b) => a.createdAt.localeCompare(b.createdAt));
    let removed = 0;
    let totalBytes = this.totalSize;
    while (
      candidates.length > 0
      && ((maxArtifacts !== undefined && this.artifacts.length > maxArtifacts)
        || (maxBytes !== undefined && totalBytes > maxBytes))
    ) {
      const artifact = candidates.shift()!;
      const fullPath = path.join(this.baseDir, artifact.path);
      if (fs.existsSync(fullPath)) fs.rmSync(fullPath, { force: true });
      this.artifacts = this.artifacts.filter((entry) => entry.id !== artifact.id || entry.path !== artifact.path);
      totalBytes -= artifact.size;
      removed += 1;
    }
    return removed;
  }

  /** Save the manifest to disk. */
  saveManifest(): string {
    this.init();
    const manifestPath = path.join(this.baseDir, "..", "manifest.json");
    fs.writeFileSync(manifestPath, JSON.stringify(this.manifest(), null, 2), "utf-8");
    return manifestPath;
  }

  /** Get total artifact count. */
  get count(): number {
    return this.artifacts.length;
  }

  /** Get total size in bytes. */
  get totalSize(): number {
    return this.artifacts.reduce((sum, a) => sum + a.size, 0);
  }

  /** Rehydrate previously written artifact metadata for resumed runs. */
  private loadManifestFromDisk(): void {
    const manifestPath = path.join(this.baseDir, "..", "manifest.json");
    if (!fs.existsSync(manifestPath)) return;
    try {
      const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf-8")) as ArtifactManifest;
      if (manifest.runId !== this.runId || !Array.isArray(manifest.artifacts)) return;
      this.artifacts = manifest.artifacts.filter((artifact) => {
        if (!artifact || typeof artifact.path !== "string") return false;
        return fs.existsSync(path.join(this.baseDir, artifact.path));
      });
    } catch {
      // A corrupt manifest must not prevent a run from starting; new artifacts
      // will rebuild the manifest when the run reaches its finalization path.
    }
  }
}
