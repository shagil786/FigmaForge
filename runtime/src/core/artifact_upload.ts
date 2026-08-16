/** Optional, explicit remote delivery for a completed run's artifacts. */

import * as fs from "node:fs";
import * as path from "node:path";

export interface ArtifactUploadResult {
  uploaded: number;
  bytes: number;
  endpoint: string;
}

export interface ArtifactUploaderOptions {
  endpoint: string;
  runId: string;
  runDirectory: string;
  token?: string;
  timeoutMs?: number;
  fetchImpl?: typeof fetch;
}

function safeEndpoint(value: string): URL {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new Error("artifact upload endpoint must be a valid http(s) URL");
  }
  if (url.protocol !== "https:" && url.protocol !== "http:") {
    throw new Error("artifact upload endpoint must use http or https");
  }
  return url;
}

function relativeFiles(root: string): string[] {
  if (!fs.existsSync(root)) return [];
  const result: string[] = [];
  const visit = (current: string): void => {
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const full = path.join(current, entry.name);
      if (entry.isDirectory()) visit(full);
      else if (entry.isFile()) result.push(path.relative(root, full));
    }
  };
  visit(root);
  return result.sort();
}

/** Uploads a run directory one file at a time using PUT. This is opt-in. */
export class ArtifactUploader {
  async upload(options: ArtifactUploaderOptions): Promise<ArtifactUploadResult> {
    const endpoint = safeEndpoint(options.endpoint);
    const fetchImpl = options.fetchImpl ?? fetch;
    const timeoutMs = options.timeoutMs ?? 30_000;
    if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
      throw new Error("artifact upload timeout must be positive");
    }
    const files = relativeFiles(options.runDirectory);
    if (files.length === 0) throw new Error("artifact upload run directory is empty");
    let bytes = 0;
    for (const relative of files) {
      const body = fs.readFileSync(path.join(options.runDirectory, relative));
      bytes += body.length;
      const target = new URL(
        `runs/${encodeURIComponent(options.runId)}/${relative.split(path.sep).map(encodeURIComponent).join("/")}`,
        endpoint.toString().endsWith("/") ? endpoint : `${endpoint.toString()}/`,
      );
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), timeoutMs);
      try {
        const response = await fetchImpl(target, {
          method: "PUT",
          headers: {
            "Content-Type": "application/octet-stream",
            ...(options.token ? { Authorization: `Bearer ${options.token}` } : {}),
          },
          body,
          signal: controller.signal,
        });
        if (!response.ok) throw new Error(`artifact upload failed with HTTP ${response.status}`);
      } catch (error) {
        if (error instanceof Error && error.name === "AbortError") {
          throw new Error(`artifact upload timed out after ${timeoutMs}ms`);
        }
        throw new Error(`artifact upload failed: ${error instanceof Error ? error.message : String(error)}`);
      } finally {
        clearTimeout(timer);
      }
    }
    return { uploaded: files.length, bytes, endpoint: endpoint.origin + endpoint.pathname };
  }
}
