/**
 * Typed tool registry and tool protocol.
 *
 * Tools are the atomic operations of the pipeline. Each tool has:
 * - A unique name
 * - Typed input/output schemas
 * - A deterministic execute function
 * - Optional model-assisted decision points (clearly separated)
 *
 * The registry provides lookup, validation, and invocation tracking.
 */

import type { PipelineStage } from "./types.js";

// ---------------------------------------------------------------------------
// Tool protocol
// ---------------------------------------------------------------------------

export interface ToolInput {
  [key: string]: unknown;
}

export interface ToolOutput {
  [key: string]: unknown;
}

export interface ToolContext {
  /** Run ID for this invocation. */
  runId: string;
  /** Absolute path to the output directory. */
  outputDir: string;
  /** Absolute path to the plugin directory. */
  pluginDir: string;
  /** Path to python3 binary. */
  pythonBin: string;
  /** Abort signal for cancellation. */
  signal?: AbortSignal;
}

export interface Tool<I extends ToolInput = ToolInput, O extends ToolOutput = ToolOutput> {
  /** Unique tool name (e.g. "ingest.figma_file"). */
  name: string;
  /** Description for documentation. */
  description: string;
  /** Pipeline stage this tool belongs to. */
  stage: PipelineStage;
  /** Whether this tool may invoke a model (non-deterministic). */
  isModelAssisted: boolean;
  /** Execute the tool. */
  execute(input: I, ctx: ToolContext): Promise<O>;
}

export interface ToolInvocation {
  toolName: string;
  input: ToolInput;
  output: ToolOutput | null;
  error: string | null;
  durationMs: number;
  timestamp: string;
}

// ---------------------------------------------------------------------------
// Tool registry
// ---------------------------------------------------------------------------

export class ToolRegistry {
  private tools = new Map<string, Tool>();
  private invocations: ToolInvocation[] = [];

  /** Register a tool. */
  register<I extends ToolInput, O extends ToolOutput>(tool: Tool<I, O>): void {
    if (this.tools.has(tool.name)) {
      throw new Error(`Tool already registered: ${tool.name}`);
    }
    this.tools.set(tool.name, tool as Tool);
  }

  /** Look up a tool by name. */
  get(name: string): Tool | undefined {
    return this.tools.get(name);
  }

  /** List all registered tools. */
  list(): Tool[] {
    return [...this.tools.values()];
  }

  /** List tools for a specific stage. */
  listByStage(stage: PipelineStage): Tool[] {
    return this.list().filter((t) => t.stage === stage);
  }

  /** Invoke a tool by name with timing and error handling. */
  async invoke(
    name: string,
    input: ToolInput,
    ctx: ToolContext,
  ): Promise<ToolOutput> {
    const tool = this.tools.get(name);
    if (!tool) throw new Error(`Unknown tool: ${name}`);

    const start = Date.now();
    const invocation: ToolInvocation = {
      toolName: name,
      input,
      output: null,
      error: null,
      durationMs: 0,
      timestamp: new Date().toISOString(),
    };

    try {
      const output = await tool.execute(input, ctx);
      invocation.output = output;
      invocation.durationMs = Date.now() - start;
      this.invocations.push(invocation);
      return output;
    } catch (err) {
      invocation.error = err instanceof Error ? err.message : String(err);
      invocation.durationMs = Date.now() - start;
      this.invocations.push(invocation);
      throw err;
    }
  }

  /** Get all invocations (for event log / replay). */
  getInvocations(): readonly ToolInvocation[] {
    return this.invocations;
  }
}

// ---------------------------------------------------------------------------
// Python bridge tool (spawns python3 for pipeline steps)
// ---------------------------------------------------------------------------

import { spawn } from "node:child_process";
import * as path from "node:path";

export interface PythonToolInput {
  script: string;     // Relative path to Python script (from plugin dir)
  args?: string[];    // Command-line arguments
  stdin?: string;     // Optional JSON stdin
  [key: string]: unknown;
}

export interface PythonToolOutput {
  exitCode: number;
  stdout: string;
  stderr: string;
  data?: unknown;     // Parsed JSON from stdout, if valid
  [key: string]: unknown;
}

/**
 * A tool that executes a Python script from the plugin directory.
 * This bridges the TypeScript runtime with the existing Python pipeline.
 */
export function createPythonTool(
  name: string,
  stage: PipelineStage,
  script: string,
  description: string = "",
): Tool<PythonToolInput, PythonToolOutput> {
  return {
    name,
    description: description || `Python tool: ${script}`,
    stage,
    isModelAssisted: false,
    async execute(input: PythonToolInput, ctx: ToolContext): Promise<PythonToolOutput> {
      const scriptPath = path.join(ctx.pluginDir, input.script);
      const args = input.args ?? [];

      return new Promise((resolve, reject) => {
        const proc = spawn(ctx.pythonBin, [scriptPath, ...args], {
          cwd: ctx.pluginDir,
          env: { ...process.env, PYTHONIOENCODING: "utf-8" },
          signal: ctx.signal,
        });

        let stdout = "";
        let stderr = "";

        proc.stdout.on("data", (chunk: Buffer) => { stdout += chunk.toString(); });
        proc.stderr.on("data", (chunk: Buffer) => { stderr += chunk.toString(); });

        if (input.stdin) {
          proc.stdin.write(input.stdin);
          proc.stdin.end();
        }

        proc.on("close", (code: number | null) => {
          const exitCode = code ?? 1;
          let data: unknown;
          try { data = JSON.parse(stdout); } catch { /* not JSON */ }
          resolve({ exitCode, stdout, stderr, data });
        });

        proc.on("error", (err: Error) => reject(err));
      });
    },
  };
}
