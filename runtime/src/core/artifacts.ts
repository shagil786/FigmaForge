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

export interface Artifact {
  /** Unique artifact ID (content hash). */
  id: string;
  /** Artifact kind. */
  kind: ArtifactKind;
  /** Pipeline stage that produced this artifact. */
  stage: PipelineStage;
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
  }

  /** Initialize the artifact directory. */
  init(): void {
    fs.mkdirSync(this.baseDir, { recursive: true });
  }

  /** Store an artifact from a JSON-serializable value. */
  storeJSON(
    kind: ArtifactKind,
    stage: PipelineStage,
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

  /** Store an artifact from a binary buffer (e.g. screenshot). */
  storeBuffer(
    kind: ArtifactKind,
    stage: PipelineStage,
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
  byStage(stage: PipelineStage): Artifact[] {
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
}
