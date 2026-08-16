/** Dependency-free HTTP receiver for FigmaForge run artifacts. */

import * as http from "node:http";
import * as fs from "node:fs";
import * as path from "node:path";
import * as crypto from "node:crypto";

const DEFAULT_MAX_ROOT_FILES = 10_000;
const DEFAULT_MAX_ROOT_BYTES = 10 * 1024 * 1024 * 1024;

export interface ArtifactServerOptions {
  rootDir: string;
  token?: string;
  maxFiles?: number;
  maxBytes?: number;
  /** Global retention across all run directories. */
  maxRootFiles?: number;
  maxRootBytes?: number;
  maxFileBytes?: number;
}

export class ArtifactServer {
  private server: http.Server | undefined;
  constructor(private readonly options: ArtifactServerOptions) {}

  listen(port = 8787, host = "127.0.0.1"): Promise<string> {
    this.server = http.createServer((request, response) => { void this.handle(request, response); });
    return new Promise((resolve, reject) => {
      this.server!.once("error", reject);
      this.server!.listen(port, host, () => {
        const address = this.server!.address() as { address: string; port: number };
        resolve(`http://${address.address === "::" ? "127.0.0.1" : address.address}:${address.port}`);
      });
    });
  }

  close(): Promise<void> {
    if (!this.server || !this.server.listening) return Promise.resolve();
    return new Promise((resolve, reject) => this.server!.close((error) => error ? reject(error) : resolve()));
  }

  private async handle(request: http.IncomingMessage, response: http.ServerResponse): Promise<void> {
    if (request.method === "GET" && request.url === "/health") {
      response.writeHead(200, { "content-type": "application/json" });
      response.end('{"status":"ok"}');
      return;
    }
    if (request.method !== "PUT") return this.finish(response, 404, "not found");
    if (this.options.token && !this.authorized(request.headers.authorization)) {
      return this.finish(response, 401, "unauthorized");
    }
    let segments: string[];
    try { segments = new URL(request.url ?? "/", "http://localhost").pathname.split("/").filter(Boolean).map(decodeURIComponent); }
    catch { return this.finish(response, 400, "invalid path"); }
    if (segments.length < 3 || segments[0] !== "runs" || segments.some((segment) => segment === ".." || segment === "." || segment.includes("\\"))) {
      return this.finish(response, 400, "invalid artifact path");
    }
    const runId = segments[1];
    const relative = segments.slice(2).join(path.sep);
    const runDir = path.join(this.options.rootDir, runId);
    const destination = path.join(runDir, relative);
    if (!destination.startsWith(`${path.resolve(runDir)}${path.sep}`)) return this.finish(response, 400, "invalid artifact path");
    const chunks: Buffer[] = [];
    let size = 0;
    const maxFileBytes = this.options.maxFileBytes ?? 50 * 1024 * 1024;
    try {
      for await (const chunk of request) {
        const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
        size += buffer.length;
        if (size > maxFileBytes) { request.destroy(); return this.finish(response, 413, "file too large"); }
        chunks.push(buffer);
      }
      fs.mkdirSync(path.dirname(destination), { recursive: true });
      const temporary = `${destination}.${crypto.randomUUID()}.tmp`;
      fs.writeFileSync(temporary, Buffer.concat(chunks));
      fs.renameSync(temporary, destination);
      this.enforceRetention(runDir);
      this.enforceRootRetention();
      this.finish(response, 201, "stored");
    } catch {
      this.finish(response, 500, "storage failure");
    }
  }

  private authorized(header: string | undefined): boolean {
    const expected = Buffer.from(`Bearer ${this.options.token}`);
    const actual = Buffer.from(header ?? "");
    return actual.length === expected.length && crypto.timingSafeEqual(actual, expected);
  }

  private enforceRetention(runDir: string): void {
    const files: Array<{ file: string; mtime: number; size: number }> = [];
    const visit = (directory: string): void => {
      if (!fs.existsSync(directory)) return;
      for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
        const file = path.join(directory, entry.name);
        if (entry.isDirectory()) visit(file);
        else if (entry.isFile() && !entry.name.endsWith(".tmp")) {
          const stat = fs.statSync(file); files.push({ file, mtime: stat.mtimeMs, size: stat.size });
        }
      }
    };
    visit(runDir);
    files.sort((a, b) => a.mtime - b.mtime);
    let bytes = files.reduce((sum, file) => sum + file.size, 0);
    while ((this.options.maxFiles !== undefined && files.length > this.options.maxFiles) ||
      (this.options.maxBytes !== undefined && bytes > this.options.maxBytes)) {
      const oldest = files.shift();
      if (!oldest) break;
      fs.rmSync(oldest.file, { force: true }); bytes -= oldest.size;
    }
  }

  private enforceRootRetention(): void {
    const maxRootFiles = this.options.maxRootFiles ?? DEFAULT_MAX_ROOT_FILES;
    const maxRootBytes = this.options.maxRootBytes ?? DEFAULT_MAX_ROOT_BYTES;
    const files: Array<{ file: string; mtime: number; size: number }> = [];
    const visit = (directory: string): void => {
      if (!fs.existsSync(directory)) return;
      for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
        const file = path.join(directory, entry.name);
        if (entry.isDirectory()) visit(file);
        else if (entry.isFile() && !entry.name.endsWith(".tmp")) {
          const stat = fs.statSync(file);
          files.push({ file, mtime: stat.mtimeMs, size: stat.size });
        }
      }
    };
    visit(this.options.rootDir);
    files.sort((a, b) => a.mtime - b.mtime);
    let bytes = files.reduce((sum, file) => sum + file.size, 0);
    while (files.length > maxRootFiles || bytes > maxRootBytes) {
      const oldest = files.shift();
      if (!oldest) break;
      fs.rmSync(oldest.file, { force: true });
      bytes -= oldest.size;
    }
  }

  private finish(response: http.ServerResponse, status: number, message: string): void {
    response.writeHead(status, { "content-type": "text/plain" }); response.end(message);
  }
}
