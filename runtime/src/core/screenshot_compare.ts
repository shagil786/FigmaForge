/**
 * Pixel-level screenshot comparison (Part 12).
 *
 * One real implementation, two entry points: the pixel math lives in Python
 * (`core.pixel_diff`); this module shells out to it for non-identical
 * buffers. The SHA-256 hash fast-path detects identical images without
 * spawning anything. Garbage output or a missing python interpreter produce
 * a clean typed failure (similarity 0, −1 sentinels) — never a throw.
 */

import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import * as crypto from "node:crypto";
import { spawnSync } from "node:child_process";

// ---------------------------------------------------------------------------
// Comparison result
// ---------------------------------------------------------------------------

export interface ScreenshotComparison {
  /** Overall similarity score (0–1). 1.0 = identical. */
  similarity: number;
  /** Number of pixels that differ beyond threshold. */
  diffPixelCount: number;
  /** Percentage of pixels that differ (0–1). */
  diffPercentage: number;
  /** Total pixel count. */
  totalPixels: number;
  /** Image dimensions. */
  width: number;
  height: number;
  /** Content hash of each image. */
  hashA: string;
  hashB: string;
  /** Whether the images are identical (hash match). */
  identical: boolean;
  /** Per-channel mean absolute error. */
  meanAbsoluteError: { r: number; g: number; b: number };
  /** Global SSIM (0–1), null when unmeasurable (Part 13). */
  ssim?: number | null;
  /** Lowest per-region SSIM, null when no regions/verdicts (Part 13). */
  minRegionSsim?: number | null;
  /** Perceptual verdict: true = clean (identical or visually identical),
   *  false = real change, null = unavailable (Part 13). */
  ssimClean?: boolean | null;
  heatmapPath?: string;
}

export interface ComparisonOptions {
  /** Per-pixel color distance threshold (0–255). Default: 16. */
  colorThreshold?: number;
  /** Resize larger image to match smaller? Default: false. */
  resize?: boolean;
  /** Optional path for a generated PNG diff heatmap. */
  heatmapPath?: string;
}

/** Fields parsed from the python CLI's JSON line. */
export interface PixelDiffResult {
  similarity: number;
  diffPixelCount: number;
  diffPercentage: number;
  totalPixels: number;
  width: number;
  height: number;
  identical: boolean;
  meanAbsoluteError: { r: number; g: number; b: number };
  ssim?: number | null;
  minRegionSsim?: number | null;
  ssimClean?: boolean | null;
  heatmapPath?: string;
}

const DEFAULT_PLUGIN_DIR = "./plugin/figmaforge";

// ---------------------------------------------------------------------------
// Python CLI output parsing (exported for tests)
// ---------------------------------------------------------------------------

/**
 * Parse the last non-empty line of `core.pixel_diff` stdout as the result
 * JSON. Returns null for garbage, empty output, or error payloads
 * (`{"error": ...}` lacks the required numeric fields).
 */
export function parsePixelDiffOutput(stdout: string): PixelDiffResult | null {
  const lines = stdout.split("\n").map((l) => l.trim()).filter((l) => l.length > 0);
  if (lines.length === 0) return null;
  const last = lines[lines.length - 1];
  try {
    const obj = JSON.parse(last);
    if (
      obj && typeof obj === "object"
      && typeof obj.similarity === "number"
      && typeof obj.diffPixelCount === "number"
      && typeof obj.diffPercentage === "number"
      && typeof obj.totalPixels === "number"
      && typeof obj.width === "number"
      && typeof obj.height === "number"
      && typeof obj.identical === "boolean"
      && obj.meanAbsoluteError
      && typeof obj.meanAbsoluteError.r === "number"
      && typeof obj.meanAbsoluteError.g === "number"
      && typeof obj.meanAbsoluteError.b === "number"
    ) {
      return {
        similarity: obj.similarity,
        diffPixelCount: obj.diffPixelCount,
        diffPercentage: obj.diffPercentage,
        totalPixels: obj.totalPixels,
        width: obj.width,
        height: obj.height,
        identical: obj.identical,
        meanAbsoluteError: {
          r: obj.meanAbsoluteError.r,
          g: obj.meanAbsoluteError.g,
          b: obj.meanAbsoluteError.b,
        },
        // Part 13 keys are optional in old output — missing → null.
        ssim: typeof obj.ssim === "number" ? obj.ssim : null,
        minRegionSsim:
          typeof obj.min_region_ssim === "number" ? obj.min_region_ssim : null,
        ssimClean: typeof obj.ssim_clean === "boolean" ? obj.ssim_clean : null,
        heatmapPath: typeof obj.heatmap_path === "string" ? obj.heatmap_path : undefined,
      };
    }
  } catch {
    // not JSON — fall through
  }
  return null;
}

// ---------------------------------------------------------------------------
// PNG dimension probe (IHDR only — full decode lives in Python)
// ---------------------------------------------------------------------------

function pngDimensions(buffer: Buffer): { width: number; height: number } | null {
  const PNG_SIG = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
  if (buffer.length < 24 || !buffer.subarray(0, 8).equals(PNG_SIG)) return null;
  return { width: buffer.readUInt32BE(16), height: buffer.readUInt32BE(20) };
}

// ---------------------------------------------------------------------------
// Screenshot comparator
// ---------------------------------------------------------------------------

export class ScreenshotComparator {
  private options: Required<ComparisonOptions>;
  private pythonBin: string;
  private pluginDir: string;

  constructor(
    options?: ComparisonOptions,
    runtime?: { pythonBin?: string; pluginDir?: string },
  ) {
    this.options = {
      colorThreshold: options?.colorThreshold ?? 16,
      resize: options?.resize ?? false,
      heatmapPath: options?.heatmapPath ?? "",
    };
    // Same resolution pattern as ctx_pythonBin() in render_handler.ts.
    this.pythonBin = runtime?.pythonBin ?? (process.env.PYTHON_BIN ?? "python3");
    this.pluginDir = runtime?.pluginDir ?? DEFAULT_PLUGIN_DIR;
  }

  /**
   * Compare two screenshot files.
   * Returns a detailed comparison result.
   */
  compare(fileA: string, fileB: string): ScreenshotComparison {
    const bufA = fs.readFileSync(fileA);
    const bufB = fs.readFileSync(fileB);

    return this.compareBuffers(bufA, bufB);
  }

  /**
   * Compare two screenshot buffers.
   */
  compareBuffers(bufA: Buffer, bufB: Buffer): ScreenshotComparison {
    const hashA = crypto.createHash("sha256").update(bufA).digest("hex").slice(0, 16);
    const hashB = crypto.createHash("sha256").update(bufB).digest("hex").slice(0, 16);

    // Fast path: identical content — no python spawn needed.
    if (hashA === hashB) {
      const dims = pngDimensions(bufA);
      return {
        similarity: 1.0,
        diffPixelCount: 0,
        diffPercentage: 0,
        totalPixels: dims ? dims.width * dims.height : 0,
        width: dims?.width ?? 0,
        height: dims?.height ?? 0,
        hashA,
        hashB,
        identical: true,
        meanAbsoluteError: { r: 0, g: 0, b: 0 },
        ssim: 1.0,
        minRegionSsim: null,
        ssimClean: true,
      };
    }

    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "figmaforge-diff-"));
    const fileA = path.join(dir, "a.png");
    const fileB = path.join(dir, "b.png");
    try {
      fs.writeFileSync(fileA, bufA);
      fs.writeFileSync(fileB, bufB);
      return this.diffViaPython(fileA, fileB, hashA, hashB);
    } finally {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  }

  private diffViaPython(
    fileA: string,
    fileB: string,
    hashA: string,
    hashB: string,
  ): ScreenshotComparison {
    const failure: ScreenshotComparison = {
      similarity: 0.0,
      diffPixelCount: -1,
      diffPercentage: -1,
      totalPixels: 0,
      width: 0,
      height: 0,
      hashA,
      hashB,
      identical: false,
      meanAbsoluteError: { r: -1, g: -1, b: -1 },
      ssim: null,
      minRegionSsim: null,
      ssimClean: null,
    };

    try {
      const result = spawnSync(
        this.pythonBin,
        [
          "-m", "core.pixel_diff",
          "--a", fileA,
          "--b", fileB,
          "--threshold", String(this.options.colorThreshold),
          ...(this.options.resize ? ["--resize"] : []),
          ...(this.options.heatmapPath ? ["--heatmap-out", this.options.heatmapPath] : []),
        ],
        { cwd: this.pluginDir, encoding: "utf-8", timeout: 30_000 },
      );
      if (result.error || result.status !== 0) return failure;

      const parsed = parsePixelDiffOutput(result.stdout ?? "");
      if (!parsed) return failure;

      return { ...parsed, hashA, hashB };
    } catch {
      return failure;
    }
  }

  /**
   * Compare two screenshots with a similarity threshold.
   * Returns whether they pass the threshold.
   */
  passesThreshold(fileA: string, fileB: string, threshold: number): boolean {
    const result = this.compare(fileA, fileB);
    return result.similarity >= threshold;
  }

  /**
   * Generate a visual diff buffer highlighting differences.
   * Returns a simple diff representation (not a full image).
   */
  generateDiffReport(fileA: string, fileB: string): {
    summary: string;
    regions: Array<{ x: number; y: number; width: number; height: number; severity: string }>;
  } {
    const result = this.compare(fileA, fileB);

    if (result.identical) {
      return { summary: "Images are identical", regions: [] };
    }

    const severity = result.diffPercentage > 0.1 ? "high"
      : result.diffPercentage > 0.01 ? "medium"
      : "low";

    return {
      summary: `${(result.diffPercentage * 100).toFixed(2)}% pixels differ (similarity: ${result.similarity.toFixed(4)})`,
      regions: [
        {
          x: 0,
          y: 0,
          width: result.width,
          height: result.height,
          severity,
        },
      ],
    };
  }
}
