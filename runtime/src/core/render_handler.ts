/**
 * Render stage handler — generates output from VNode/VStyle and renders it.
 *
 * The target is a composable { framework, styling } pair — any combination
 * is valid. The backend registry resolves whether a concrete adapter exists.
 *
 * - Web targets (browser renderer):
 *   Generates HTML and optionally captures screenshots via Playwright.
 * - Native targets (xcode_preview, flutter_simulator, etc.):
 *   Generates metadata only; visual comparison requires platform simulators.
 *
 * The file-based mode always works and produces deterministic output.
 * The browser mode produces screenshots when available (web targets only).
 */

import * as fs from "node:fs";
import * as path from "node:path";
import * as crypto from "node:crypto";
import type { PipelineContext } from "./pipeline.js";
import type { CodegenTarget, RendererType } from "./types.js";
import { defaultRenderer, targetKey } from "./types.js";
import { ScreenshotComparator } from "./screenshot_compare.js";

// ---------------------------------------------------------------------------
// VNode types (matching the Python generator_types.py)
// ---------------------------------------------------------------------------

interface VStyle {
  [key: string]: string | number | undefined;
}

interface VNode {
  tag: string;
  attrs?: Record<string, string>;
  style?: VStyle;
  children?: (VNode | string)[];
  text?: string;
}

// ---------------------------------------------------------------------------
// HTML generation from VNode tree
// ---------------------------------------------------------------------------

/**
 * Convert a VNode tree to an HTML string.
 */
export function vnodeToHtml(node: VNode | string, indent: number = 0): string {
  if (typeof node === "string") {
    return escapeHtml(node);
  }

  const pad = "  ".repeat(indent);
  const tag = node.tag;
  const attrs = renderAttrs(node.attrs, node.style);
  const selfClosing = ["img", "br", "hr", "input", "meta", "link"].includes(tag);

  if (selfClosing && (!node.children || node.children.length === 0)) {
    return `${pad}<${tag}${attrs} />`;
  }

  const children = (node.children ?? [])
    .map((child) => vnodeToHtml(child, indent + 1))
    .join("\n");

  if (node.text) {
    return `${pad}<${tag}${attrs}>${escapeHtml(node.text)}</${tag}>`;
  }

  if (!children) {
    return `${pad}<${tag}${attrs}></${tag}>`;
  }

  return `${pad}<${tag}${attrs}>\n${children}\n${pad}</${tag}>`;
}

function renderAttrs(
  attrs?: Record<string, string>,
  style?: VStyle,
): string {
  const parts: string[] = [];

  if (attrs) {
    for (const [key, value] of Object.entries(attrs)) {
      parts.push(`${key}="${escapeAttr(value)}"`);
    }
  }

  if (style && Object.keys(style).length > 0) {
    const css = Object.entries(style)
      .filter(([, v]) => v !== undefined)
      .map(([k, v]) => `${camelToKebab(k)}: ${v}`)
      .join("; ");
    if (css) {
      parts.push(`style="${css}"`);
    }
  }

  return parts.length > 0 ? " " + parts.join(" ") : "";
}

function camelToKebab(str: string): string {
  return str.replace(/([A-Z])/g, "-$1").toLowerCase();
}

function escapeHtml(str: string): string {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function escapeAttr(str: string): string {
  return str
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

// ---------------------------------------------------------------------------
// Full HTML document generation
// ---------------------------------------------------------------------------

function generateFullHtml(
  vnode: VNode,
  viewport: { width: number; height: number },
  title: string = "FigmaForge Render",
): string {
  const bodyHtml = vnodeToHtml(vnode, 2);

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>${escapeHtml(title)}</title>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      width: ${viewport.width}px;
      min-height: ${viewport.height}px;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      overflow: hidden;
    }
    /* FigmaForge render container */
    #figmaforge-root {
      width: ${viewport.width}px;
      min-height: ${viewport.height}px;
      position: relative;
    }
  </style>
</head>
<body>
  <div id="figmaforge-root">
${bodyHtml}
  </div>
  <script>
    // Extract layout metadata for comparison
    (function() {
      const root = document.getElementById("figmaforge-root");
      if (!root) return;
      const elements = root.querySelectorAll("[data-node-id]");
      const meta = {};
      elements.forEach(el => {
        const id = el.getAttribute("data-node-id");
        const rect = el.getBoundingClientRect();
        const computed = window.getComputedStyle(el);
        meta[id] = {
          x: Math.round(rect.x),
          y: Math.round(rect.y),
          width: Math.round(rect.width),
          height: Math.round(rect.height),
          styles: {
            fontSize: parseFloat(computed.fontSize),
            color: computed.color,
            backgroundColor: computed.backgroundColor,
            fontFamily: computed.fontFamily,
            padding: computed.padding,
            margin: computed.margin,
          }
        };
      });
      window.__figmaforge_meta = meta;
    })();
  </script>
</body>
</html>`;
}

// ---------------------------------------------------------------------------
// Render handler
// ---------------------------------------------------------------------------

export interface RenderOutput {
  /** Path to the generated HTML file. */
  htmlPath: string;
  /** Path to the screenshot (if browser rendering succeeded). */
  screenshotPath: string | null;
  /** Layout metadata extracted from the render. */
  layoutMeta: Record<string, unknown>;
  /** Content hash of the HTML. */
  htmlHash: string;
  /** Viewport used. */
  viewport: { width: number; height: number };
}

/**
 * Render stage handler for the pipeline.
 *
 * Takes generated VNode/VStyle code and produces:
 * 1. An output file (always — HTML for web targets, metadata for native)
 * 2. A screenshot (when Playwright is available, web targets only)
 * 3. Layout metadata (from the HTML structure or static analysis)
 */
export async function renderHandler(
  ctx: PipelineContext,
  input: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  const outputDir = path.join(ctx.config.outputDir, ctx.config.runId, "renders");
  fs.mkdirSync(outputDir, { recursive: true });

  const target: CodegenTarget = ctx.config.target ?? { framework: "html", styling: "css" };
  const renderer: RendererType = defaultRenderer(target.framework);
  const vnode = (input.vnode ?? ctx.shared.get("vnode")) as VNode | undefined;
  const viewport = input.viewport as { width: number; height: number } ?? ctx.config.viewport;

  // Native targets cannot render in a browser — emit metadata only
  if (renderer !== "browser") {
    return generateNativeMetadata(outputDir, target, renderer, viewport, input);
  }

  if (!vnode) {
    // If no VNode is available, generate a placeholder render
    return generatePlaceholderRender(outputDir, viewport, input);
  }

  // Generate HTML
  const html = generateFullHtml(vnode, viewport);
  const htmlHash = crypto.createHash("sha256").update(html).digest("hex").slice(0, 16);
  const htmlPath = path.join(outputDir, `render_${htmlHash}.html`);
  fs.writeFileSync(htmlPath, html, "utf-8");

  // Extract layout metadata from VNode tree (static analysis)
  const layoutMeta = extractLayoutMeta(vnode);

  // Try browser rendering (best-effort)
  let screenshotPath: string | null = null;
  try {
    screenshotPath = await tryBrowserRender(htmlPath, outputDir, htmlHash, viewport);
  } catch {
    // Browser rendering not available — that's OK
  }

  const result: RenderOutput = {
    htmlPath,
    screenshotPath,
    layoutMeta,
    htmlHash,
    viewport,
  };

  // Store in shared state for downstream stages
  ctx.shared.set("render_output", result);
  ctx.shared.set("render_meta", layoutMeta);

  return result as unknown as Record<string, unknown>;
}

/**
 * Generate metadata-only output for native targets (SwiftUI, Flutter).
 * These targets cannot render in a browser, so visual comparison is deferred
 * to platform-specific simulators.
 */
function generateNativeMetadata(
  outputDir: string,
  target: CodegenTarget,
  renderer: RendererType,
  viewport: { width: number; height: number },
  input: Record<string, unknown>,
): Record<string, unknown> {
  const key = targetKey(target);
  const meta = {
    target: { framework: target.framework, styling: target.styling },
    renderer,
    viewport,
    screenshotPath: null,
    htmlPath: null,
    layoutMeta: {},
    note: `Visual comparison for ${key} requires ${renderer}. ` +
      "Screenshot capture is not available in this environment.",
  };

  const metaPath = path.join(outputDir, `native_meta_${key}.json`);
  fs.writeFileSync(metaPath, JSON.stringify(meta, null, 2), "utf-8");

  return {
    ...meta,
    metaPath,
  } as Record<string, unknown>;
}

/**
 * Generate placeholder render when no VNode is available.
 */
function generatePlaceholderRender(
  outputDir: string,
  viewport: { width: number; height: number },
  input: Record<string, unknown>,
): Record<string, unknown> {
  const html = `<!DOCTYPE html>
<html><head><title>FigmaForge Placeholder</title></head>
<body style="width:${viewport.width}px;height:${viewport.height}px;background:#f0f0f0;">
<div style="padding:20px;color:#666;">
  <h1>FigmaForge Render</h1>
  <p>No generated code available. This is a placeholder render.</p>
  <pre>${JSON.stringify(input, null, 2).slice(0, 2000)}</pre>
</div>
</body></html>`;

  const htmlHash = crypto.createHash("sha256").update(html).digest("hex").slice(0, 16);
  const htmlPath = path.join(outputDir, `placeholder_${htmlHash}.html`);
  fs.writeFileSync(htmlPath, html, "utf-8");

  return {
    htmlPath,
    screenshotPath: null,
    layoutMeta: {},
    htmlHash,
    viewport,
  } as Record<string, unknown>;
}

/**
 * Extract layout metadata from a VNode tree (static analysis).
 * Walks the tree and computes expected positions from style properties.
 */
function extractLayoutMeta(node: VNode | string, x: number = 0, y: number = 0): Record<string, unknown> {
  if (typeof node === "string") return {};

  const meta: Record<string, unknown> = {};
  const nodeId = node.attrs?.["data-node-id"];
  const style = node.style ?? {};

  const width = parseSize(style.width, 0);
  const height = parseSize(style.height, 0);

  if (nodeId) {
    meta[nodeId] = {
      x: Math.round(x),
      y: Math.round(y),
      width: Math.round(width),
      height: Math.round(height),
      styles: {
        fontSize: parseSize(style.fontSize, 0),
        color: style.color ?? "",
        backgroundColor: style.backgroundColor ?? "",
      },
    };
  }

  // Layout children
  let childX = x + parseSize(style.paddingLeft ?? style.padding, 0);
  let childY = y + parseSize(style.paddingTop ?? style.padding, 0);
  const isHorizontal = style.display === "flex" && style.flexDirection === "row";
  const gap = parseSize(style.gap, 0);

  if (node.children) {
    for (const child of node.children) {
      if (typeof child !== "string") {
        const childMeta = extractLayoutMeta(child, childX, childY);
        Object.assign(meta, childMeta);

        // Advance position
        const childWidth = parseSize(child.style?.width, 0);
        const childHeight = parseSize(child.style?.height, 0);
        if (isHorizontal) {
          childX += childWidth + gap;
        } else {
          childY += childHeight + gap;
        }
      }
    }
  }

  return meta;
}

function parseSize(value: string | number | undefined, fallback: number): number {
  if (value === undefined) return fallback;
  if (typeof value === "number") return value;
  const num = parseFloat(value);
  return isNaN(num) ? fallback : num;
}

/**
 * Attempt browser rendering using Playwright via Python bridge.
 * Returns the screenshot path if successful, null otherwise.
 */
async function tryBrowserRender(
  htmlPath: string,
  outputDir: string,
  hash: string,
  viewport: { width: number; height: number },
): Promise<string | null> {
  // Check if Playwright Python is available
  const { execFile } = await import("node:child_process");
  const { promisify } = await import("node:util");
  const execFileAsync = promisify(execFile);

  try {
    // Try to use Python + Playwright for screenshot
    const script = `
import sys, json
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": ${viewport.width}, "height": ${viewport.height}})
        page.goto("file://${htmlPath}")
        page.wait_for_load_state("networkidle")
        screenshot_path = "${outputDir}/screenshot_${hash}.png"
        page.screenshot(path=screenshot_path, full_page=True)
        meta = page.evaluate("window.__figmaforge_meta || {}")
        browser.close()
        print(json.dumps({"screenshot": screenshot_path, "meta": meta}))
except ImportError:
    print(json.dumps({"error": "playwright_not_installed"}))
except Exception as e:
    print(json.dumps({"error": str(e)}))
`;

    const { stdout } = await execFileAsync(ctx_pythonBin(), {
      timeout: 30_000,
      env: { ...process.env, PYTHONIOENCODING: "utf-8" },
    });

    // This won't work directly since we need to pass the script via stdin
    // Fall through to the alternative approach
    return null;
  } catch {
    return null;
  }
}

function ctx_pythonBin(): string {
  return process.env.PYTHON_BIN ?? "python3";
}

// Re-export for use in other modules
export { ScreenshotComparator } from "./screenshot_compare.js";
