#!/usr/bin/env python3
"""Vision-driven iteration loop with real LLM fixes.

Generates HTML → renders → vision-compares → LLM fixes → repeats until
the design-intent score exceeds the target.

Fix strategies:
  - LLM: sends HTML + vision feedback to a vision LLM for intelligent rewriting
  - Template: deterministic corrections (gradient alpha, object-fit, spacing)
  - Hybrid: LLM first, template fallback
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class VisionIterationPlan:
    file_path: str
    baseline_path: str
    target_score: float = 0.90
    max_iterations: int = 10
    viewport_width: int = 1920
    viewport_height: int = 900
    out_dir: str = "vision_iteration"
    api_provider: Optional[str] = None
    api_key: Optional[str] = None
    api_url: Optional[str] = None
    model: Optional[str] = None


@dataclass
class VisionIterationResult:
    iteration: int
    vision_score: float
    html_path: str
    screenshot_path: str
    feedback: Dict[str, Any]
    fixes_applied: List[str]
    latency_ms: int = 0


@dataclass
class VisionIterationFinal:
    iterations: List[VisionIterationResult]
    best_score: float
    best_iteration: int
    target_reached: bool
    plateau_detected: bool
    final_html_path: str
    final_screenshot_path: str


# ---------------------------------------------------------------------------
# LLM-based HTML fix
# ---------------------------------------------------------------------------

_FIX_SYSTEM_PROMPT = """\
You are an expert frontend engineer. You generated HTML for a web page but
a vision comparison found issues. Fix the HTML to match the design better.

Return ONLY the fixed HTML code. No explanation, no markdown fences.
Keep the same structure but adjust styles, positions, colors, and sizing.
"""


def _call_llm_fix(
    html: str,
    feedback: Dict[str, Any],
    provider: str,
    api_key: str,
    model: str,
    api_url: str = "",
) -> str:
    """Send HTML + feedback to an LLM and get fixed HTML back."""
    fix_prompt = (
        f"Fix this HTML to better match the original design.\n\n"
        f"Issues found: {json.dumps(feedback.get('issues', []))}\n"
        f"Suggestions: {json.dumps(feedback.get('suggestions', []))}\n"
        f"Scores: layout={feedback.get('layout', 0):.0%} "
        f"color={feedback.get('color', 0):.0%} "
        f"typography={feedback.get('typography', 0):.0%} "
        f"spacing={feedback.get('spacing', 0):.0%}\n\n"
        f"HTML to fix:\n```html\n{html}\n```"
    )

    messages = [
        {"role": "system", "content": _FIX_SYSTEM_PROMPT},
        {"role": "user", "content": fix_prompt},
    ]

    if provider == "openai":
        url = "https://api.openai.com/v1/chat/completions"
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
        payload = {"model": model, "messages": messages, "max_tokens": 8192, "temperature": 0.2}
    elif provider == "anthropic":
        url = "https://api.anthropic.com/v1/messages"
        headers = {"Content-Type": "application/json", "x-api-key": api_key, "anthropic-version": "2023-06-01"}
        payload = {
            "model": model, "max_tokens": 8192, "system": _FIX_SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": fix_prompt}],
        }
    elif provider == "gemini":
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        headers = {"Content-Type": "application/json"}
        payload = {
            "contents": [{"parts": [{"text": _FIX_SYSTEM_PROMPT + "\n\n" + fix_prompt}]}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 8192},
        }
    elif provider == "custom" and api_url:
        url = api_url
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        payload = {"model": model, "messages": messages, "max_tokens": 8192, "temperature": 0.2}
    else:
        raise ValueError(f"Unsupported LLM fix provider: {provider}")

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = json.loads(resp.read().decode("utf-8"))

    # Extract response text
    if provider == "openai" or provider == "custom":
        return body["choices"][0]["message"]["content"]
    elif provider == "anthropic":
        return body["content"][0]["text"]
    elif provider == "gemini":
        return body["candidates"][0]["content"]["parts"][0]["text"]
    raise ValueError(f"Unknown provider: {provider}")


def _extract_html_from_llm_response(response: str) -> str:
    """Extract HTML from LLM response (may contain markdown fences)."""
    text = response.strip()
    # Strip markdown fences
    text = re.sub(r"```(?:html)?\s*", "", text)
    text = re.sub(r"```\s*$", "", text)
    text = text.strip()
    # If it starts with <!DOCTYPE or <html, it's raw HTML
    if text.lower().startswith("<!doctype") or text.lower().startswith("<html"):
        return text
    # Try to find an HTML block
    match = re.search(r"(<!DOCTYPE.*?</html>)", text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1)
    return text


# ---------------------------------------------------------------------------
# Template-based fixes
# ---------------------------------------------------------------------------

def apply_template_fixes(html: str, feedback: Dict[str, Any]) -> Tuple[str, List[str]]:
    """Deterministic fixes for known issues."""
    fixes = []
    all_hints = " ".join(feedback.get("issues", []) + feedback.get("suggestions", [])).lower()

    # Gradient alpha
    if "gradient" in all_hints and ("dark" in all_hints or "opacity" in all_hints):
        def adjust_alpha(m):
            r, g, b, a = m.groups()
            a_val = float(a)
            a_val = max(0.1, a_val * 0.85) if "dark" in all_hints else min(1.0, a_val * 1.15)
            return f"rgba({r},{g},{b},{a_val:.3f})"
        new = re.sub(r"rgba\((\d+),(\d+),(\d+),([\d.]+)\)", adjust_alpha, html)
        if new != html:
            fixes.append("Adjusted gradient alpha")
            html = new

    # Object-fit
    if "image" in all_hints and ("stretch" in all_hints or "distort" in all_hints or "crop" in all_hints):
        if "object-fit:fill" in html:
            html = html.replace("object-fit:fill", "object-fit:cover")
            fixes.append("Changed object-fit to cover")

    # Spacing
    if "spacing" in all_hints or "gap" in all_hints:
        if "too much" in all_hints or "too wide" in all_hints:
            def reduce_gap(m):
                prop, val = m.groups()
                return f"{prop}:{float(val) * 0.9:.1f}px"
            new = re.sub(r"(gap|margin|padding):(\d+(?:\.\d+)?)px", reduce_gap, html)
            if new != html:
                fixes.append("Reduced spacing by 10%")
                html = new

    return html, fixes


# ---------------------------------------------------------------------------
# Vision Iterator
# ---------------------------------------------------------------------------

class VisionIterator:
    def __init__(self, plan: VisionIterationPlan):
        self.plan = plan
        self.results: List[VisionIterationResult] = []
        self.best_score = 0.0
        self.best_iteration = 0
        self.plateau_count = 0
        self.last_score = 0.0
        self._out_dir = Path(plan.out_dir)
        self._out_dir.mkdir(parents=True, exist_ok=True)

    def run(self) -> VisionIterationFinal:
        logger.info(f"Starting vision iteration: target={self.plan.target_score:.0%}, max={self.plan.max_iterations}")

        html_path = self._generate_initial()
        if html_path is None:
            raise RuntimeError("Failed to generate initial HTML")

        for i in range(1, self.plan.max_iterations + 1):
            logger.info(f"\n{'='*50} Iteration {i}/{self.plan.max_iterations} {'='*50}")
            start = time.monotonic()

            # Render
            screenshot_path = self._render(html_path, i)
            if screenshot_path is None:
                continue

            # Vision compare
            feedback = self._vision_compare(screenshot_path)
            vision_score = feedback.get("overall", 0.0)
            latency = int((time.monotonic() - start) * 1000)

            result = VisionIterationResult(
                iteration=i, vision_score=vision_score,
                html_path=str(html_path), screenshot_path=str(screenshot_path),
                feedback=feedback, fixes_applied=[], latency_ms=latency,
            )
            self.results.append(result)

            logger.info(f"Score: {vision_score:.1%} (target: {self.plan.target_score:.0%})")
            if feedback.get("issues"):
                for issue in feedback["issues"][:3]:
                    logger.info(f"  Issue: {issue}")

            # Check target
            if vision_score >= self.plan.target_score:
                logger.info(f"🎯 Target reached!")
                break

            # Plateau
            improvement = vision_score - self.last_score
            if improvement < 0.01:
                self.plateau_count += 1
                if self.plateau_count >= 3:
                    logger.info(f"⚠️ Plateau detected")
                    break
            else:
                self.plateau_count = 0

            if vision_score > self.best_score:
                self.best_score = vision_score
                self.best_iteration = i
            self.last_score = vision_score

            # Fix
            html_path, fixes = self._apply_fixes(html_path, feedback)
            result.fixes_applied = fixes
            for fix in fixes:
                logger.info(f"  Applied: {fix}")

        if self.results:
            best = max(self.results, key=lambda r: r.vision_score)
            self.best_score = best.vision_score
            self.best_iteration = best.iteration

        return VisionIterationFinal(
            iterations=self.results, best_score=self.best_score,
            best_iteration=self.best_iteration,
            target_reached=self.best_score >= self.plan.target_score,
            plateau_detected=self.plateau_count >= 3,
            final_html_path=self.results[-1].html_path if self.results else "",
            final_screenshot_path=self.results[-1].screenshot_path if self.results else "",
        )

    def _generate_initial(self) -> Optional[Path]:
        from core.reference_renderer import render_reference
        from core.ir_builder import IRBuilder, FigmaFile

        raw = json.loads(Path(self.plan.file_path).read_text())
        if "schema_version" in raw and "root" in raw:
            from core.ir_types import IRDocument
            doc = IRDocument.from_dict(raw)
        else:
            file_key = raw.get("file_key") or Path(self.plan.file_path).stem
            doc = IRBuilder(images=raw.get("assets") or {}).build(FigmaFile.from_dict(file_key, raw))

        ir_dict = doc.to_dict()
        assets_lookup = {}
        try:
            from core.semantic_comparator import IMAGE_REF_MAP
            manifest_path = Path("assets/manifest.json")
            if manifest_path.exists():
                manifest = json.loads(manifest_path.read_text())
                for image_ref, content_hash in IMAGE_REF_MAP.items():
                    prefix = content_hash[:2]
                    p = Path("assets") / prefix / content_hash
                    if p.exists():
                        assets_lookup[image_ref] = str(p.resolve())
        except Exception:
            pass

        html = render_reference(ir_dict, assets=assets_lookup, viewport_height=self.plan.viewport_height)
        html_path = self._out_dir / "iteration_0.html"
        html_path.write_text(html, encoding="utf-8")
        logger.info(f"Generated: {html_path} ({len(html)} bytes)")
        return html_path

    def _render(self, html_path: Path, iteration: int) -> Optional[Path]:
        try:
            from core.render_harness import RenderHarness
            out_dir = self._out_dir / f"render_{iteration}"
            out_dir.mkdir(exist_ok=True)
            harness = RenderHarness(output_dir=out_dir)
            result = harness.render(
                content_html=html_path.read_text(encoding="utf-8"),
                viewport_spec={"width": self.plan.viewport_width, "height": self.plan.viewport_height},
                build_id=f"viter_{iteration}", full_page=False, tiled=False,
            )
            return Path(result.screenshot_path)
        except Exception as exc:
            logger.error(f"Render failed: {exc}")
            return None

    def _vision_compare(self, screenshot_path: Path) -> Dict[str, Any]:
        from core.vision_comparator import VisionComparator
        try:
            comp = VisionComparator(
                provider=self.plan.api_provider or "auto",
                api_key=self.plan.api_key,
                model=self.plan.model,
                api_url=self.plan.api_url,
            )
        except ValueError:
            comp = VisionComparator(provider="mock")
        return comp.compare(self.plan.baseline_path, screenshot_path).to_dict()

    def _apply_fixes(self, html_path: Path, feedback: Dict[str, Any]) -> Tuple[Path, List[str]]:
        html = html_path.read_text(encoding="utf-8")
        fixes = []

        # Try LLM fix first if provider is available
        if self.plan.api_provider and self.plan.api_key and self.plan.api_provider != "mock":
            try:
                fixed_html = _call_llm_fix(
                    html, feedback, self.plan.api_provider,
                    self.plan.api_key, self.plan.model or "gpt-4o",
                    self.plan.api_url or "",
                )
                fixed_html = _extract_html_from_llm_response(fixed_html)
                if fixed_html and len(fixed_html) > 100:
                    html = fixed_html
                    fixes.append(f"LLM fix via {self.plan.api_provider}")
                else:
                    logger.warning("LLM returned empty/short response, falling back to template")
                    html, template_fixes = apply_template_fixes(html, feedback)
                    fixes.extend(template_fixes)
            except Exception as exc:
                logger.warning(f"LLM fix failed ({exc}), falling back to template")
                html, template_fixes = apply_template_fixes(html, feedback)
                fixes.extend(template_fixes)
        else:
            html, fixes = apply_template_fixes(html, feedback)

        iteration = len(self.results) + 1
        new_path = self._out_dir / f"iteration_{iteration}.html"
        new_path.write_text(html, encoding="utf-8")
        return new_path, fixes
