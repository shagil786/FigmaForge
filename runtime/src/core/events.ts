/**
 * Structured event log for every action in the pipeline.
 *
 * Events are append-only and JSON-serializable. They form the complete
 * audit trail for a run and support replay/debugging.
 */

import type { PipelineStage, RunId, TaskId } from "./types.js";

// ---------------------------------------------------------------------------
// Event types
// ---------------------------------------------------------------------------

export type EventLevel = "info" | "warn" | "error" | "debug";

export type EventKind =
  | "adaptive_plan_created"
  | "run_started"
  | "run_completed"
  | "run_failed"
  | "run_cancelled"
  | "stage_started"
  | "stage_completed"
  | "stage_failed"
  | "stage_skipped"
  | "checkpoint_saved"
  | "checkpoint_loaded"
  | "retry_attempt"
  | "budget_exceeded"
  | "approval_requested"
  | "approval_granted"
  | "approval_denied"
  | "repair_iteration"
  | "repair_rollback"
  | "tool_invoked"
  | "tool_completed"
  | "tool_failed"
  | "artifact_stored"
  | "model_invoked"
  | "security_violation";

export interface PipelineEvent {
  /** Monotonically increasing sequence number within the run. */
  seq: number;
  /** ISO-8601 timestamp. */
  timestamp: string;
  /** Event severity. */
  level: EventLevel;
  /** Event category. */
  kind: EventKind;
  /** Run this event belongs to. */
  runId: RunId;
  /** Task (stage attempt) this event belongs to, if applicable. */
  taskId?: TaskId;
  /** Pipeline stage, if applicable. */
  stage?: PipelineStage;
  /** Human-readable message. */
  message: string;
  /** Structured data payload. */
  data?: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Event log
// ---------------------------------------------------------------------------

export class EventLog {
  private events: PipelineEvent[] = [];
  private seq: number = 0;

  constructor(public readonly runId: RunId) {}

  /** Append an event to the log. */
  emit(
    kind: EventKind,
    message: string,
    options: {
      level?: EventLevel;
      taskId?: TaskId;
      stage?: PipelineStage;
      data?: Record<string, unknown>;
    } = {},
  ): PipelineEvent {
    const event: PipelineEvent = {
      seq: this.seq++,
      timestamp: new Date().toISOString(),
      level: options.level ?? "info",
      kind,
      runId: this.runId,
      taskId: options.taskId,
      stage: options.stage,
      message,
      data: options.data,
    };
    this.events.push(event);
    return event;
  }

  /** Get all events. */
  all(): readonly PipelineEvent[] {
    return this.events;
  }

  /** Get events filtered by kind. */
  byKind(kind: EventKind): PipelineEvent[] {
    return this.events.filter((e) => e.kind === kind);
  }

  /** Get events filtered by stage. */
  byStage(stage: PipelineStage): PipelineEvent[] {
    return this.events.filter((e) => e.stage === stage);
  }

  /** Get events at or above a severity level. */
  byLevel(minLevel: EventLevel): PipelineEvent[] {
    const levels: EventLevel[] = ["debug", "info", "warn", "error"];
    const minIdx = levels.indexOf(minLevel);
    return this.events.filter((e) => levels.indexOf(e.level) >= minIdx);
  }

  /** Number of events. */
  get length(): number {
    return this.events.length;
  }

  /** Serialize to JSON array. */
  toJSON(): PipelineEvent[] {
    return [...this.events];
  }

  /** Restore from a JSON array (for replay). */
  static fromJSON(runId: RunId, events: PipelineEvent[]): EventLog {
    const log = new EventLog(runId);
    log.events = [...events];
    log.seq = events.length > 0 ? Math.max(...events.map((e) => e.seq)) + 1 : 0;
    return log;
  }
}
