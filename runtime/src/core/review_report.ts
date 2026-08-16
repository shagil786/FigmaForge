/** Self-contained HTML export for human review of a completed baseline run. */

export interface ReviewReportInput {
  summary?: Record<string, unknown>;
  manifest?: { runId?: string; artifacts?: Array<Record<string, unknown>> };
  diffReport?: Record<string, unknown>;
}

function escapeHtml(value: unknown): string {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function buildReviewReportHtml(input: ReviewReportInput): string {
  const diff = input.diffReport ?? {};
  const summaryResult = (input.summary?.result ?? {}) as Record<string, unknown>;
  const score = diff.similarity_score ?? summaryResult.similarityScore ?? "N/A";
  const mismatches = Array.isArray(diff.mismatches) ? diff.mismatches as Array<Record<string, unknown>> : [];
  const categories = new Map<string, number>();
  for (const mismatch of mismatches) {
    const category = typeof mismatch.type === "string" ? mismatch.type : "unknown";
    categories.set(category, (categories.get(category) ?? 0) + 1);
  }
  const categoryRows = [...categories.entries()]
    .map(([category, count]) => `<tr><td>${escapeHtml(category)}</td><td>${count}</td></tr>`)
    .join("");
  const artifacts = input.manifest?.artifacts ?? [];
  const artifactRows = artifacts
    .map((artifact) => `<tr><td>${escapeHtml(artifact.kind)}</td><td>${escapeHtml(artifact.path)}</td><td>${escapeHtml(artifact.size)}</td></tr>`)
    .join("");
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>FigmaForge Baseline Review</title>
<style>body{font:15px system-ui,sans-serif;max-width:960px;margin:32px auto;padding:0 20px;color:#202124}table{border-collapse:collapse;width:100%;margin:12px 0 28px}th,td{border:1px solid #d9d9d9;padding:8px;text-align:left}th{background:#f3f4f6}.score{font-size:2.2rem;font-weight:700}</style>
</head><body><h1>FigmaForge Baseline Review</h1>
<p>Run: <code>${escapeHtml(input.manifest?.runId ?? "unknown")}</code></p>
<p class="score">Similarity: ${escapeHtml(score)}</p>
<h2>Mismatch categories</h2><table><thead><tr><th>Category</th><th>Count</th></tr></thead><tbody>${categoryRows || "<tr><td colspan=2>No mismatches</td></tr>"}</tbody></table>
<h2>Artifacts</h2><table><thead><tr><th>Kind</th><th>Path</th><th>Bytes</th></tr></thead><tbody>${artifactRows || "<tr><td colspan=3>No artifacts</td></tr>"}</tbody></table>
</body></html>`;
}
