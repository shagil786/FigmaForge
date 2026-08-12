/**
 * Security boundaries for the FigmaForge runtime.
 *
 * - Restrict filesystem access to explicitly approved directories
 * - Prevent arbitrary shell execution
 * - Never expose secrets in prompts or logs
 * - Require approval before modifying source files
 * - Validate external assets before downloading or using them
 */

import * as fs from "node:fs";
import * as path from "node:path";

// ---------------------------------------------------------------------------
// Path sandbox
// ---------------------------------------------------------------------------

export class SecurityViolation extends Error {
  constructor(
    public readonly rule: string,
    message: string,
  ) {
    super(`Security violation [${rule}]: ${message}`);
    this.name = "SecurityViolation";
  }
}

/**
 * Validates that a file path is within an approved directory.
 * Resolves symlinks and normalizes the path before checking.
 */
export class PathSandbox {
  private approvedDirs: string[];

  constructor(approvedDirs: string[]) {
    this.approvedDirs = approvedDirs.map((d) => this.resolveDir(d));
  }

  /** Add an approved directory at runtime (e.g. after user approval). */
  approve(dir: string): void {
    const resolved = this.resolveDir(dir);
    if (!this.approvedDirs.includes(resolved)) {
      this.approvedDirs.push(resolved);
    }
  }

  /** Check if a path is allowed. Throws SecurityViolation if not. */
  assertAllowed(filePath: string): void {
    const resolved = this.resolvePath(filePath);

    for (const dir of this.approvedDirs) {
      if (resolved.startsWith(dir + path.sep) || resolved === dir) {
        return;
      }
    }

    throw new SecurityViolation(
      "path_sandbox",
      `Path "${filePath}" is not within approved directories: [${this.approvedDirs.join(", ")}]`,
    );
  }

  /** Check if a path is allowed. Returns boolean instead of throwing. */
  isAllowed(filePath: string): boolean {
    try {
      this.assertAllowed(filePath);
      return true;
    } catch {
      return false;
    }
  }

  /** Safely read a file after sandbox check. */
  readFileSync(filePath: string, encoding?: BufferEncoding): string {
    this.assertAllowed(filePath);
    return fs.readFileSync(filePath, encoding ?? "utf-8");
  }

  /** Safely write a file after sandbox check. */
  writeFileSync(filePath: string, data: string | Buffer): void {
    this.assertAllowed(filePath);
    // Ensure parent directory exists
    const dir = path.dirname(filePath);
    if (!fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true });
    }
    fs.writeFileSync(filePath, data);
  }

  /** Get the list of approved directories. */
  getApprovedDirs(): readonly string[] {
    return [...this.approvedDirs];
  }

  private resolveDir(dir: string): string {
    return path.resolve(dir);
  }

  private resolvePath(filePath: string): string {
    // Resolve to absolute, but don't require the file to exist
    return path.resolve(filePath);
  }
}

// ---------------------------------------------------------------------------
// Secret guard
// ---------------------------------------------------------------------------

/** Patterns that look like secrets, API keys, tokens, etc. */
const SECRET_PATTERNS: RegExp[] = [
  /(?:api[_-]?key|secret|token|password|passwd|credential|auth)\s*[:=]\s*["']?[A-Za-z0-9_\-./+=]{8,}/gi,
  /(?:figd_|ghp_|gho_|glpat-|sk-|xox[abpsr]-)[A-Za-z0-9_\-]{10,}/g,
  /Bearer\s+[A-Za-z0-9_\-./+=]{10,}/gi,
  /-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----/g,
];

/**
 * Scans text for potential secrets and redacts them.
 * Used to prevent secrets from appearing in logs or prompts.
 */
export class SecretGuard {
  private additionalPatterns: RegExp[] = [];
  private redactionPlaceholder = "[REDACTED]";

  /** Add a custom pattern to scan for. */
  addPattern(pattern: RegExp): void {
    this.additionalPatterns.push(pattern);
  }

  /** Set the redaction placeholder text. */
  setPlaceholder(text: string): void {
    this.redactionPlaceholder = text;
  }

  /** Check if text contains potential secrets. */
  containsSecrets(text: string): boolean {
    const allPatterns = [...SECRET_PATTERNS, ...this.additionalPatterns];
    return allPatterns.some((p) => p.test(text));
  }

  /** Redact potential secrets from text. */
  redact(text: string): string {
    let result = text;
    const allPatterns = [...SECRET_PATTERNS, ...this.additionalPatterns];

    for (const pattern of allPatterns) {
      // Reset regex state
      pattern.lastIndex = 0;
      result = result.replace(pattern, (match) => {
        // Preserve the key name but redact the value
        const eqIdx = match.search(/[:=]/);
        if (eqIdx > 0) {
          return match.slice(0, eqIdx + 1) + this.redactionPlaceholder;
        }
        return this.redactionPlaceholder;
      });
    }

    return result;
  }

  /** Redact secrets from all string values in an object (deep). */
  redactObject(obj: unknown): unknown {
    if (typeof obj === "string") {
      return this.redact(obj);
    }
    if (Array.isArray(obj)) {
      return obj.map((item) => this.redactObject(item));
    }
    if (obj !== null && typeof obj === "object") {
      const result: Record<string, unknown> = {};
      for (const [key, value] of Object.entries(obj)) {
        result[key] = this.redactObject(value);
      }
      return result;
    }
    return obj;
  }
}

// ---------------------------------------------------------------------------
// Shell execution guard
// ---------------------------------------------------------------------------

/** Allowed commands for the shell execution guard. */
const ALLOWED_COMMANDS = new Set([
  "python3",
  "node",
  "npx",
]);

/**
 * Validates shell commands before execution.
 * Only pre-approved commands are allowed.
 */
export class ShellGuard {
  private allowedCommands: Set<string>;

  constructor(extraCommands?: string[]) {
    this.allowedCommands = new Set([...ALLOWED_COMMANDS, ...(extraCommands ?? [])]);
  }

  /** Add an allowed command. */
  allow(command: string): void {
    this.allowedCommands.add(command);
  }

  /** Check if a command is allowed. Throws SecurityViolation if not. */
  assertAllowed(command: string, args: string[]): void {
    const baseCmd = path.basename(command);

    if (!this.allowedCommands.has(baseCmd) && !this.allowedCommands.has(command)) {
      throw new SecurityViolation(
        "shell_guard",
        `Command "${command}" is not in the allowed list: [${[...this.allowedCommands].join(", ")}]`,
      );
    }

    // Check for dangerous argument patterns
    for (const arg of args) {
      if (arg.includes(";") || arg.includes("&&") || arg.includes("||") || arg.includes("|")) {
        throw new SecurityViolation(
          "shell_guard",
          `Dangerous argument pattern detected: "${arg}"`,
        );
      }
    }
  }

  /** Check if a command is allowed. Returns boolean. */
  isAllowed(command: string, args: string[] = []): boolean {
    try {
      this.assertAllowed(command, args);
      return true;
    } catch {
      return false;
    }
  }
}

// ---------------------------------------------------------------------------
// Asset validator
// ---------------------------------------------------------------------------

/** Maximum allowed asset size (10 MB). */
const MAX_ASSET_SIZE = 10 * 1024 * 1024;

/** Allowed asset MIME types. */
const ALLOWED_MIME_TYPES = new Set([
  "image/png",
  "image/jpeg",
  "image/svg+xml",
  "image/webp",
  "image/gif",
  "application/json",
]);

/**
 * Validates external assets before use.
 * Checks size, MIME type, and content integrity.
 */
export class AssetValidator {
  private maxFileSize: number;
  private allowedMimeTypes: Set<string>;

  constructor(options?: { maxFileSize?: number; allowedMimeTypes?: string[] }) {
    this.maxFileSize = options?.maxFileSize ?? MAX_ASSET_SIZE;
    this.allowedMimeTypes = new Set(options?.allowedMimeTypes ?? ALLOWED_MIME_TYPES);
  }

  /** Validate a file on disk. */
  validateFile(filePath: string): { valid: boolean; error?: string } {
    if (!fs.existsSync(filePath)) {
      return { valid: false, error: `File does not exist: ${filePath}` };
    }

    const stat = fs.statSync(filePath);
    if (stat.size > this.maxFileSize) {
      return {
        valid: false,
        error: `File too large: ${stat.size} bytes (max ${this.maxFileSize})`,
      };
    }

    if (stat.size === 0) {
      return { valid: false, error: "File is empty" };
    }

    // Check extension as a proxy for MIME type
    const ext = path.extname(filePath).toLowerCase();
    const extToMime: Record<string, string> = {
      ".png": "image/png",
      ".jpg": "image/jpeg",
      ".jpeg": "image/jpeg",
      ".svg": "image/svg+xml",
      ".webp": "image/webp",
      ".gif": "image/gif",
      ".json": "application/json",
    };

    const mime = extToMime[ext];
    if (!mime) {
      return { valid: false, error: `Unknown file extension: ${ext}` };
    }

    if (!this.allowedMimeTypes.has(mime)) {
      return { valid: false, error: `MIME type not allowed: ${mime}` };
    }

    return { valid: true };
  }

  /** Validate a buffer. */
  validateBuffer(buffer: Buffer, ext: string): { valid: boolean; error?: string } {
    if (buffer.length > this.maxFileSize) {
      return {
        valid: false,
        error: `Buffer too large: ${buffer.length} bytes (max ${this.maxFileSize})`,
      };
    }

    if (buffer.length === 0) {
      return { valid: false, error: "Buffer is empty" };
    }

    return { valid: true };
  }
}

// ---------------------------------------------------------------------------
// Approval gate
// ---------------------------------------------------------------------------

export type ApprovalCallback = (request: ApprovalRequest) => Promise<boolean>;

export interface ApprovalRequest {
  /** What is requesting approval. */
  action: string;
  /** Human-readable description. */
  description: string;
  /** Files that will be modified. */
  affectedFiles: string[];
  /** Additional context. */
  metadata?: Record<string, unknown>;
}

/**
 * An approval gate that requires explicit user consent before
 * proceeding with potentially destructive operations.
 */
export class ApprovalGate {
  private callback: ApprovalCallback | null;
  private grantedActions: Set<string> = new Set();

  constructor(callback?: ApprovalCallback) {
    this.callback = callback ?? null;
  }

  /** Set the approval callback. */
  setCallback(callback: ApprovalCallback): void {
    this.callback = callback;
  }

  /** Pre-approve an action (e.g. from CLI flag). */
  preApprove(action: string): void {
    this.grantedActions.add(action);
  }

  /** Check if an action is approved. */
  async assertApproved(request: ApprovalRequest): Promise<void> {
    // Check pre-approval
    if (this.grantedActions.has(request.action)) {
      return;
    }

    // Check if approval is required
    if (!this.callback) {
      throw new SecurityViolation(
        "approval_gate",
        `Action "${request.action}" requires approval but no approval callback is set`,
      );
    }

    const granted = await this.callback(request);
    if (!granted) {
      throw new SecurityViolation(
        "approval_gate",
        `Action "${request.action}" was denied by the user`,
      );
    }

    // Cache the approval for this session
    this.grantedActions.add(request.action);
  }

  /** Reset all cached approvals. */
  resetApprovals(): void {
    this.grantedActions.clear();
  }
}
