/** Host-neutral JSON/HTTP model-provider protocol.
 *
 * Request:  { prompt, model?, maxTokens?, temperature? }
 * Response: { text, tokensUsed?, model? }
 *
 * This deliberately uses the platform fetch API so local gateways, OpenAI-
 * compatible proxies, and other LLM hosts can share the same contract.
 */

import type { ModelOptions, ModelProvider, ModelResult, ModelStreamChunk, ModelToolCall } from "../core/types.js";

export interface JsonProviderConfig {
  endpoint: string;
  name?: string;
  apiKey?: string;
  defaultModel?: string;
  defaultTimeout?: number;
}

function redactProviderError(value: string): string {
  return value
    .replace(/(?:api[_-]?key|token|secret|password)\s*[:=]\s*[^\s,;}]+/gi, "$1=[REDACTED]")
    .replace(/Bearer\s+[^\s]+/gi, "Bearer [REDACTED]");
}

export class JsonProtocolProvider implements ModelProvider {
  readonly name: string;

  constructor(private readonly config: JsonProviderConfig) {
    this.name = config.name ?? "json-http";
  }

  async complete(prompt: string, options?: ModelOptions): Promise<ModelResult> {
    const started = Date.now();
    const controller = new AbortController();
    const timeout = options?.timeout ?? this.config.defaultTimeout ?? 30_000;
    const timer = setTimeout(() => controller.abort(), timeout);
    const onAbort = () => controller.abort();
    options?.signal?.addEventListener("abort", onAbort, { once: true });

    try {
      const response = await fetch(this.config.endpoint, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(this.config.apiKey ? { Authorization: `Bearer ${this.config.apiKey}` } : {}),
        },
        body: JSON.stringify({
          prompt,
          model: options?.model ?? this.config.defaultModel,
          maxTokens: options?.maxTokens,
          temperature: options?.temperature,
          tools: options?.tools,
        }),
        signal: controller.signal,
      });

      const raw = await response.text();
      if (!response.ok) {
        throw new Error(`JSON provider HTTP ${response.status}: ${redactProviderError(raw)}`);
      }
      let data: unknown;
      try {
        data = JSON.parse(raw);
      } catch (error) {
        throw new Error(`JSON provider returned invalid JSON: ${redactProviderError(String(error))}`);
      }
      if (data === null || typeof data !== "object") {
        throw new Error("JSON provider response must be an object");
      }
      const result = data as { text?: unknown; tool_calls?: unknown; toolCalls?: unknown; tokensUsed?: unknown; model?: unknown };
      const rawCalls = result.tool_calls ?? result.toolCalls;
      const toolCalls: ModelToolCall[] | undefined = Array.isArray(rawCalls)
        ? rawCalls.flatMap((call): ModelToolCall[] => {
            if (call === null || typeof call !== "object") return [];
            const item = call as { id?: unknown; name?: unknown; function?: { name?: unknown; arguments?: unknown }; arguments?: unknown };
            const name = typeof item.name === "string" ? item.name : typeof item.function?.name === "string" ? item.function.name : null;
            if (!name) return [];
            const rawArguments = item.arguments ?? item.function?.arguments;
            let args: Record<string, unknown> = {};
            if (typeof rawArguments === "string") {
              try { args = JSON.parse(rawArguments) as Record<string, unknown>; } catch { return []; }
            } else if (rawArguments && typeof rawArguments === "object" && !Array.isArray(rawArguments)) {
              args = rawArguments as Record<string, unknown>;
            }
            return [{ id: typeof item.id === "string" ? item.id : undefined, name, arguments: args }];
          })
        : undefined;
      if (typeof result.text !== "string" && (!toolCalls || toolCalls.length === 0)) {
        throw new Error("JSON provider response must contain a string field: text or tool calls");
      }
      return {
        text: typeof result.text === "string" ? result.text : "",
        tokensUsed: typeof result.tokensUsed === "number" ? result.tokensUsed : 0,
        model: typeof result.model === "string" ? result.model : (options?.model ?? this.config.defaultModel ?? this.name),
        latencyMs: Date.now() - started,
        ...(toolCalls && toolCalls.length > 0 ? { toolCalls } : {}),
      };
    } finally {
      clearTimeout(timer);
      options?.signal?.removeEventListener("abort", onAbort);
    }
  }

  /** Stream newline-delimited JSON or SSE `data:` chunks from a gateway. */
  async *stream(prompt: string, options?: ModelOptions): AsyncGenerator<ModelStreamChunk> {
    const controller = new AbortController();
    const timeout = options?.timeout ?? this.config.defaultTimeout ?? 30_000;
    const timer = setTimeout(() => controller.abort(), timeout);
    const onAbort = () => controller.abort();
    options?.signal?.addEventListener("abort", onAbort, { once: true });
    try {
      const response = await fetch(this.config.endpoint, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(this.config.apiKey ? { Authorization: `Bearer ${this.config.apiKey}` } : {}),
        },
        body: JSON.stringify({
          prompt,
          model: options?.model ?? this.config.defaultModel,
          maxTokens: options?.maxTokens,
          temperature: options?.temperature,
          tools: options?.tools,
          stream: true,
        }),
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(`JSON provider HTTP ${response.status}`);
      if (!response.body) throw new Error("JSON provider streaming response has no body");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { value, done } = await reader.read();
        buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });
        const lines = buffer.split(/\r?\n/);
        buffer = lines.pop() ?? "";
        for (const rawLine of lines) {
          const line = rawLine.trim().replace(/^data:\s*/, "");
          if (!line) continue;
          if (line === "[DONE]") {
            yield { text: "", done: true };
            return;
          }
          try {
            const item = JSON.parse(line) as { text?: unknown; done?: unknown };
            if (typeof item.text === "string") {
              yield { text: item.text, ...(item.done === true ? { done: true } : {}) };
            }
          } catch { /* Ignore malformed/incomplete stream records. */ }
        }
        if (done) break;
      }
      if (buffer.trim()) {
        const line = buffer.trim().replace(/^data:\s*/, "");
        if (line !== "[DONE]") {
          try {
            const item = JSON.parse(line) as { text?: unknown; done?: unknown };
            if (typeof item.text === "string") yield { text: item.text, done: item.done === true };
          } catch { /* Ignore an incomplete terminal fragment. */ }
        }
      }
      yield { text: "", done: true };
    } finally {
      clearTimeout(timer);
      options?.signal?.removeEventListener("abort", onAbort);
    }
  }
}
