/**
 * Replaceable model providers with no provider lock-in.
 *
 * Implements Anthropic and OpenAI providers using Node.js stdlib fetch.
 * Both implement the ModelProvider interface from types.ts.
 */

import type { ModelProvider, ModelOptions, ModelResult } from "./types.js";
import { NullModelProvider } from "./types.js";

// ---------------------------------------------------------------------------
// Provider factory
// ---------------------------------------------------------------------------

export type ProviderName = "null" | "anthropic" | "openai";

export interface ProviderConfig {
  name: ProviderName;
  apiKey?: string;          // Read from env if not provided
  defaultModel?: string;
  baseUrl?: string;         // Override API base URL
  defaultTimeout?: number;  // Default request timeout in ms
}

/**
 * Create a model provider from configuration.
 * Reads API keys from environment variables if not explicitly provided.
 */
export function createProvider(config: ProviderConfig): ModelProvider {
  switch (config.name) {
    case "null":
      return new NullModelProvider();
    case "anthropic":
      return new AnthropicProvider(
        config.apiKey ?? process.env.ANTHROPIC_API_KEY ?? "",
        config.defaultModel ?? "claude-sonnet-4-20250514",
        config.baseUrl ?? "https://api.anthropic.com",
        config.defaultTimeout ?? 30_000,
      );
    case "openai":
      return new OpenAIProvider(
        config.apiKey ?? process.env.OPENAI_API_KEY ?? "",
        config.defaultModel ?? "gpt-4o",
        config.baseUrl ?? "https://api.openai.com",
        config.defaultTimeout ?? 30_000,
      );
    default:
      throw new Error(`Unknown provider: ${config.name}`);
  }
}

// ---------------------------------------------------------------------------
// Anthropic provider
// ---------------------------------------------------------------------------

export class AnthropicProvider implements ModelProvider {
  readonly name = "anthropic";

  constructor(
    private readonly apiKey: string,
    private readonly model: string,
    private readonly baseUrl: string,
    private readonly defaultTimeout: number,
  ) {}

  async complete(prompt: string, options?: ModelOptions): Promise<ModelResult> {
    if (!this.apiKey) {
      throw new Error("Anthropic API key not configured. Set ANTHROPIC_API_KEY environment variable.");
    }

    const startMs = Date.now();
    const timeout = options?.timeout ?? this.defaultTimeout;
    const maxTokens = options?.maxTokens ?? 4096;
    const temperature = options?.temperature ?? 0;

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);

    try {
      const response = await fetch(`${this.baseUrl}/v1/messages`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "x-api-key": this.apiKey,
          "anthropic-version": "2023-06-01",
        },
        body: JSON.stringify({
          model: options?.model ?? this.model,
          max_tokens: maxTokens,
          temperature,
          messages: [{ role: "user", content: prompt }],
        }),
        signal: controller.signal,
      });

      if (!response.ok) {
        const errorBody = await response.text();
        throw new Error(`Anthropic API error ${response.status}: ${errorBody}`);
      }

      const data = await response.json() as {
        content: Array<{ type: string; text: string }>;
        usage: { input_tokens: number; output_tokens: number };
        model: string;
      };

      const text = data.content
        .filter((block) => block.type === "text")
        .map((block) => block.text)
        .join("\n");

      const tokensUsed = (data.usage?.input_tokens ?? 0) + (data.usage?.output_tokens ?? 0);

      return {
        text,
        tokensUsed,
        model: data.model ?? this.model,
        latencyMs: Date.now() - startMs,
      };
    } finally {
      clearTimeout(timer);
    }
  }
}

// ---------------------------------------------------------------------------
// OpenAI provider
// ---------------------------------------------------------------------------

export class OpenAIProvider implements ModelProvider {
  readonly name = "openai";

  constructor(
    private readonly apiKey: string,
    private readonly model: string,
    private readonly baseUrl: string,
    private readonly defaultTimeout: number,
  ) {}

  async complete(prompt: string, options?: ModelOptions): Promise<ModelResult> {
    if (!this.apiKey) {
      throw new Error("OpenAI API key not configured. Set OPENAI_API_KEY environment variable.");
    }

    const startMs = Date.now();
    const timeout = options?.timeout ?? this.defaultTimeout;
    const maxTokens = options?.maxTokens ?? 4096;
    const temperature = options?.temperature ?? 0;

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);

    try {
      const response = await fetch(`${this.baseUrl}/v1/chat/completions`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${this.apiKey}`,
        },
        body: JSON.stringify({
          model: options?.model ?? this.model,
          max_tokens: maxTokens,
          temperature,
          messages: [{ role: "user", content: prompt }],
        }),
        signal: controller.signal,
      });

      if (!response.ok) {
        const errorBody = await response.text();
        throw new Error(`OpenAI API error ${response.status}: ${errorBody}`);
      }

      const data = await response.json() as {
        choices: Array<{ message: { content: string } }>;
        usage: { prompt_tokens: number; completion_tokens: number };
        model: string;
      };

      const text = data.choices?.[0]?.message?.content ?? "";
      const tokensUsed = (data.usage?.prompt_tokens ?? 0) + (data.usage?.completion_tokens ?? 0);

      return {
        text,
        tokensUsed,
        model: data.model ?? this.model,
        latencyMs: Date.now() - startMs,
      };
    } finally {
      clearTimeout(timer);
    }
  }
}

// ---------------------------------------------------------------------------
// Model options extension (with model field)
// ---------------------------------------------------------------------------

declare module "./types.js" {
  interface ModelOptions {
    model?: string;
  }
}
