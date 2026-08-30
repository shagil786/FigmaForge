#!/usr/bin/env python3
"""Agent iteration engine for FigmaForge.

Feeds the design spec, current code, and pixel-diff feedback to a vision LLM,
then re-generates, re-renders, and re-compares until the SSIM score exceeds
the threshold or max iterations are exhausted.

This is the core of the "screenshot-to-code" loop — the LLM sees what it
generated, sees what's wrong, and fixes it.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class IterationResult:
    """Result of a single iteration."""
    iteration: int
    ssim_score: float
    verdict: str
    html_path: str
    screenshot_path: str
    diff_path: Optional[str] = None
    mismatch_count: int = 0
    fidelity_losses: List[Dict[str, str]] = field(default_factory=list)
    llm_response: Optional[str] = None
    generation_time_ms: int = 0
    render_time_ms: int = 0
    compare_time_ms: int = 0


@dataclass
class IterationPlan:
    """Full iteration plan with all settings."""
    file_path: str
    backend: str
    baseline_path: str
    max_iterations: int = 10
    target_ssim: float = 0.95
    viewport: int = 1440
    out_dir: str = "iteration_output"
    api_provider: Optional[str] = None
    api_key: Optional[str] = None
    stop_on_plateau: bool = True
    plateau_threshold: float = 0.001  # Stop if score improves < 0.1%


# ---------------------------------------------------------------------------
# Vision LLM integration
# ---------------------------------------------------------------------------

def _call_vision_llm(
    images: List[bytes],
    prompt: str,
    *,
    api_provider: Optional[str] = None,
    api_key: Optional[str] = None,
    max_tokens: int = 8192,
) -> str:
    """Call a vision LLM with images and a prompt.

    Supports Anthropic (Claude), OpenAI (GPT-4V), and NVIDIA (Kimi/Llama).
    Returns the text response from the model.
    """
    import urllib.request

    # Determine provider
    provider = api_provider or os.environ.get("FIGMAFORGE_LLM_PROVIDER", "")
    if not provider:
        if os.environ.get("ANTHROPIC_API_KEY"):
            provider = "anthropic"
        elif os.environ.get("OPENAI_API_KEY"):
            provider = "openai"
        elif os.environ.get("NVIDIA_API_KEY"):
            provider = "nvidia"
        else:
            raise ValueError(
                "No LLM provider configured. Set ANTHROPIC_API_KEY, "
                "OPENAI_API_KEY, or NVIDIA_API_KEY, or pass --api-provider."
            )

    # Get API key
    key = api_key
    if not key:
        if provider == "anthropic":
            key = os.environ.get("ANTHROPIC_API_KEY", "")
        elif provider == "openai":
            key = os.environ.get("OPENAI_API_KEY", "")
        elif provider == "nvidia":
            key = os.environ.get("NVIDIA_API_KEY", "")
    if not key:
        raise ValueError(f"No API key for {provider}. Set the appropriate env var.")

    # Build image content blocks
    image_blocks = []
    for img_bytes in images:
        b64 = base64.standard_b64encode(img_bytes).decode("utf-8")
        # Detect format from magic bytes
        if img_bytes[:8] == b"\x89PNG\r\n\x1a\n":
            media = "image/png"
        elif img_bytes[:3] == b"\xff\xd8\xff":
            media = "image/jpeg"
        else:
            media = "image/png"

        if provider in ("anthropic", "nvidia"):
            image_blocks.append({
                "type": "image",
                "source": {"type": "base64", "media_type": media, "data": b64},
            })
        else:  # openai
            image_blocks.append({
                "type": "image_url",
                "image_url": {"url": f"data:{media};base64,{b64}"},
            })

    # Build messages
    content = image_blocks + [{"type": "text", "text": prompt}]
    messages = [{"role": "user", "content": content}]

    # Build payload
    if provider == "anthropic":
        model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
        payload = json.dumps({
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
        }).encode("utf-8")
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "Content-Type": "application/json",
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        }
    elif provider == "openai":
        model = os.environ.get("OPENAI_MODEL", "gpt-4o")
        payload = json.dumps({
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
        }).encode("utf-8")
        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        }
    elif provider == "nvidia":
        model = os.environ.get("NVIDIA_MODEL", "moonshotai/kimi-k3")
        payload = json.dumps({
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
        }).encode("utf-8")
        url = "https://integrate.api.nvidia.com/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        }
    else:
        raise ValueError(f"Unknown provider: {provider}")

    # Make request
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        result = json.loads(resp.read().decode("utf-8"))

    # Extract response
    if provider == "anthropic":
        for block in result.get("content", []):
            if block.get("type") == "text":
                return block.get("text", "")
        return ""
    else:
        return result.get("choices", [{}])[0].get("message", {}).get("content", "")


def _extract_code_from_llm_response(response: str) -> str:
    """Extract HTML/code from LLM response, handling markdown code blocks."""
    # Try to extract from ```html ... ``` blocks
    if "```html" in response:
        start = response.index("```html") + 7
        end = response.index("```", start)
        return response[start:end].strip()
    if "```tsx" in response:
        start = response.index("```tsx") + 5
        end = response.index("```", start)
        return response[start:end].strip()
    if "```jsx" in response:
        start = response.index("```jsx") + 5
        end = response.index("```", start)
        return response[start:end].strip()
    if "```css" in response:
        start = response.index("```css") + 5
        end = response.index("```", start)
        return response[start:end].strip()
    # Try generic code block
    if "```" in response:
        start = response.index("```") + 3
        # Skip language identifier on same line
        newline = response.index("\n", start)
        start = newline + 1
        end = response.index("```", start)
        return response[start:end].strip()
    # Return as-is
    return response.strip()


# ---------------------------------------------------------------------------
# Feedback formatter
# ---------------------------------------------------------------------------

def _build_iteration_prompt(
    spec: Dict[str, Any],
    current_html: str,
    baseline_screenshot: bytes,
    generated_screenshot: bytes,
    diff_screenshot: Optional[bytes],
    ssim_score: float,
    mismatch_regions: List[Dict[str, Any]],
    iteration: int,
    max_iterations: int,
    backend: str,
) -> str:
    """Build the prompt for the LLM to fix the generated code."""

    mismatch_summary = ""
    if mismatch_regions:
        # Group by type
        color_mismatches = [m for m in mismatch_regions if m.get("kind") == "color"]
        layout_mismatches = [m for m in mismatch_regions if m.get("kind") == "layout"]
        missing_mismatches = [m for m in mismatch_regions if m.get("kind") == "missing"]

        mismatch_summary = f"""
MISMATCH ANALYSIS ({len(mismatch_regions)} regions):
- Color mismatches: {len(color_mismatches)} (wrong colors, gradients, opacity)
- Layout mismatches: {len(layout_mismatches)} (wrong positioning, sizing, spacing)
- Missing elements: {len(missing_mismatches)} (elements present in baseline but not in generated)
"""
        # Add specific region details
        for i, m in enumerate(mismatch_regions[:10]):  # Top 10
            mismatch_summary += f"""
  Region {i+1}: {m.get('kind', 'unknown')} at ({m.get('x', 0)}, {m.get('y', 0)}) 
    Size: {m.get('width', 0)}x{m.get('height', 0)}px
    Description: {m.get('description', 'N/A')}
"""

    return f"""You are an expert frontend developer. Your task is to fix the generated HTML/CSS code
to match the original Figma design as closely as possible (pixel-perfect).

ITERATION {iteration}/{max_iterations}
CURRENT SSIM SCORE: {ssim_score:.1%} (target: 95%+)

The images show:
1. LEFT: The original Figma design (baseline)
2. CENTER: Your current generated output
3. RIGHT: The diff (red = mismatched pixels)

{mismatch_summary}

CURRENT GENERATED CODE:
```html
{current_html}
```

DESIGN SPEC (from Figma):
- Colors: {json.dumps(spec.get('colors', []), indent=2)[:500]}
- Typography: {json.dumps(spec.get('typography', []), indent=2)[:500]}

INSTRUCTIONS:
1. Analyze the diff image carefully — red areas show where your code doesn't match
2. Fix ALL mismatches: colors, typography, spacing, layout, images, gradients
3. Use exact colors from the design spec (not approximations)
4. Match font sizes, weights, and letter-spacing exactly
5. Ensure all elements are present and correctly positioned
6. Output ONLY the fixed HTML code — no explanations, no markdown, just the code

Output the complete fixed HTML file:"""


# ---------------------------------------------------------------------------
# Main iteration engine
# ---------------------------------------------------------------------------

class AgentIterator:
    """Runs the agent iteration loop: generate → render → compare → fix → repeat."""

    def __init__(self, plan: IterationPlan):
        self.plan = plan
        self.results: List[IterationResult] = []
        self._init_modules()

    def _init_modules(self):
        """Lazy-import heavy modules."""
        from core.render_harness import RenderHarness
        from core.pixel_diff import compare_images, resize_nearest
        from core.ssim import ssim
        from core.png_codec import decode_png, encode_png

        self.RenderHarness = RenderHarness
        self.compare_images = compare_images
        self.resize_nearest = resize_nearest
        self.ssim = ssim
        self.decode_png = decode_png
        self.encode_png = encode_png

    def run(self) -> IterationResult:
        """Run the full iteration loop and return the best result."""
        plan = self.plan
        out_dir = Path(plan.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        # Load baseline
        baseline_path = Path(plan.baseline_path)
        if not baseline_path.exists():
            raise FileNotFoundError(f"Baseline not found: {baseline_path}")
        baseline_bytes = baseline_path.read_bytes()
        baseline_img = self.decode_png(baseline_bytes)
        baseline_width, baseline_height = baseline_img.size

        # Load and parse the Figma file
        from core.ir_types import IRDocument, IRBuilder, FigmaFile
        from core.layout_engine import LayoutAnalyzer
        from core.resolver import Resolver
        from core.library import LibraryLoader
        from backends import get_registry

        file_path = Path(plan.file_path)
        raw = json.loads(file_path.read_text(encoding="utf-8"))

        if "schema_version" in raw and "root" in raw:
            doc = IRDocument.from_dict(raw)
        else:
            file_key = raw.get("file_key") or file_path.stem
            doc = IRBuilder(images=raw.get("assets") or {}).build(
                FigmaFile.from_dict(file_key, raw)
            )

        plan_analyzer = LayoutAnalyzer()
        layout = plan_analyzer.analyze(
            doc, library=LibraryLoader().load(), viewport=plan.viewport
        )
        report = Resolver(doc).resolve()
        registry = get_registry()
        backend = registry.require(plan.backend)

        # Generate initial code
        output = backend.generate(doc, layout, report, options={"viewport": plan.viewport})

        # Find HTML file
        html_files = [f for f in output.files if f.path.endswith(".html")]
        if not html_files:
            raise ValueError(f"Backend {plan.backend} did not produce an HTML file")

        current_html = html_files[0].content
        html_path = out_dir / html_files[0].path
        html_path.parent.mkdir(parents=True, exist_ok=True)

        # Build spec for the LLM
        spec = {
            "colors": self._extract_colors(doc),
            "typography": self._extract_typography(doc),
            "sections": self._extract_sections(doc),
        }

        # Iteration loop
        best_score = 0.0
        best_html = current_html
        plateau_count = 0

        for iteration in range(1, plan.max_iterations + 1):
            logger.info(f"=== Iteration {iteration}/{plan.max_iterations} ===")

            # Write current HTML
            html_path.write_text(current_html, encoding="utf-8")
            t_gen = int(time.time() * 1000)

            # Render
            screenshot_path = out_dir / f"iteration_{iteration:02d}.png"
            harness = self.RenderHarness()
            try:
                harness.render(str(html_path), str(screenshot_path), viewport=plan.viewport)
            except Exception as e:
                logger.error(f"Render failed: {e}")
                continue
            t_render = int(time.time() * 1000)

            # Compare
            generated_img = self.decode_png(screenshot_path.read_bytes())
            if (baseline_width, baseline_height) != generated_img.size:
                generated_img = self.resize_nearest(generated_img, baseline_width, baseline_height)

            _, mask = self.compare_images(baseline_img, generated_img)
            ssim_score = self.ssim(baseline_img, generated_img)
            similarity = max(0.0, min(1.0, ssim_score))
            t_compare = int(time.time() * 1000)

            # Extract mismatch regions
            from scripts.pipeline import _extract_mismatch_regions
            mismatch_regions = _extract_mismatch_regions(mask, baseline_width, baseline_height)

            # Save diff image
            diff_path = out_dir / f"diff_{iteration:02d}.png"
            # Create diff visualization
            diff_img = self._create_diff_viz(baseline_img, generated_img, mask)
            diff_path.write_bytes(self.encode_png(diff_img))

            result = IterationResult(
                iteration=iteration,
                ssim_score=similarity,
                verdict="identical" if similarity >= 0.99 else "changed",
                html_path=str(html_path),
                screenshot_path=str(screenshot_path),
                diff_path=str(diff_path),
                mismatch_count=len(mismatch_regions),
                generation_time_ms=t_gen,
                render_time_ms=t_render,
                compare_time_ms=t_compare,
            )
            self.results.append(result)

            logger.info(
                f"  SSIM: {similarity:.1%} | Mismatches: {len(mismatch_regions)} | "
                f"Render: {t_render - t_gen}ms | Compare: {t_compare - t_render}ms"
            )

            # Check if we've reached the target
            if similarity >= plan.target_ssim:
                logger.info(f"  ✓ Target reached! SSIM {similarity:.1%} >= {plan.target_ssim:.1%}")
                best_score = similarity
                best_html = current_html
                break

            # Check for plateau
            if plan.stop_on_plateau and len(self.results) >= 2:
                prev_score = self.results[-2].ssim_score
                improvement = similarity - prev_score
                if improvement < plan.plateau_threshold:
                    plateau_count += 1
                    if plateau_count >= 3:
                        logger.info(f"  ⚠ Plateau detected ({plateau_count} rounds). Stopping.")
                        break
                else:
                    plateau_count = 0

            # Track best
            if similarity > best_score:
                best_score = similarity
                best_html = current_html

            # Call LLM for feedback and fix
            logger.info("  Calling LLM for fix suggestions...")
            try:
                # Build images for LLM
                llm_images = [
                    baseline_bytes,  # Original
                    screenshot_path.read_bytes(),  # Current output
                    diff_path.read_bytes(),  # Diff
                ]

                prompt = _build_iteration_prompt(
                    spec=spec,
                    current_html=current_html,
                    baseline_screenshot=baseline_bytes,
                    generated_screenshot=screenshot_path.read_bytes(),
                    diff_screenshot=diff_path.read_bytes(),
                    ssim_score=similarity,
                    mismatch_regions=mismatch_regions,
                    iteration=iteration,
                    max_iterations=plan.max_iterations,
                    backend=plan.backend,
                )

                response = _call_vision_llm(
                    images=llm_images,
                    prompt=prompt,
                    api_provider=plan.api_provider,
                    api_key=plan.api_key,
                )

                result.llm_response = response[:500]  # Store truncated

                # Extract code from response
                new_html = _extract_code_from_llm_response(response)
                if new_html and len(new_html) > 100:
                    current_html = new_html
                    logger.info(f"  LLM returned {len(new_html)} chars of code")
                else:
                    logger.warning("  LLM response too short, keeping current code")

            except Exception as e:
                logger.error(f"  LLM call failed: {e}")
                # Continue with current code

        # Write best output
        best_path = out_dir / "best_output.html"
        best_path.write_text(best_html, encoding="utf-8")

        # Write iteration report
        report_path = out_dir / "iteration_report.json"
        report_data = {
            "plan": {
                "file": plan.file_path,
                "backend": plan.backend,
                "baseline": plan.baseline_path,
                "max_iterations": plan.max_iterations,
                "target_ssim": plan.target_ssim,
            },
            "iterations": [
                {
                    "iteration": r.iteration,
                    "ssim_score": r.ssim_score,
                    "verdict": r.verdict,
                    "mismatch_count": r.mismatch_count,
                    "generation_time_ms": r.generation_time_ms,
                    "render_time_ms": r.render_time_ms,
                    "compare_time_ms": r.compare_time_ms,
                }
                for r in self.results
            ],
            "best_score": best_score,
            "final_iteration": self.results[-1].iteration if self.results else 0,
        }
        report_path.write_text(json.dumps(report_data, indent=2), encoding="utf-8")

        logger.info(f"\n=== DONE: Best SSIM = {best_score:.1%} ===")
        logger.info(f"Output: {best_path}")

        return self.results[-1] if self.results else IterationResult(
            iteration=0, ssim_score=0.0, verdict="no_iterations",
            html_path="", screenshot_path=""
        )

    def _extract_colors(self, doc: Any) -> List[Dict[str, str]]:
        """Extract color tokens from the IR document."""
        colors = {}
        def _walk(node: Any):
            fills = getattr(node, "fills", []) or []
            for fill in fills:
                color = getattr(fill, "color", None)
                if color:
                    r, g, b = int(color.r * 255), int(color.g * 255), int(color.b * 255)
                    hex_color = f"#{r:02x}{g:02x}{b:02x}"
                    if hex_color not in colors:
                        colors[hex_color] = 0
                    colors[hex_color] += 1
            for child in getattr(node, "children", []) or []:
                _walk(child)
        if hasattr(doc, "root"):
            _walk(doc.root)
        return [{"hex": h, "count": c} for h, c in sorted(colors.items(), key=lambda x: -x[1])]

    def _extract_typography(self, doc: Any) -> List[Dict[str, Any]]:
        """Extract typography tokens from the IR document."""
        styles = {}
        def _walk(node: Any):
            tc = getattr(node, "text_content", None)
            if tc:
                typo = getattr(node, "typography", None)
                if typo:
                    key = f"{typo.font_family}:{typo.font_size}:{typo.font_weight}"
                    if key not in styles:
                        styles[key] = {
                            "font_family": typo.font_family,
                            "font_size": typo.font_size,
                            "font_weight": typo.font_weight,
                            "count": 0,
                        }
                    styles[key]["count"] += 1
            for child in getattr(node, "children", []) or []:
                _walk(child)
        if hasattr(doc, "root"):
            _walk(doc.root)
        return list(styles.values())

    def _extract_sections(self, doc: Any) -> List[Dict[str, str]]:
        """Extract top-level sections from the IR document."""
        sections = []
        def _walk(node: Any, depth: int = 0):
            if depth < 2:
                name = getattr(node, "name", "unnamed")
                kind = getattr(node, "kind", "unknown")
                sections.append({"name": name, "kind": kind})
                for child in getattr(node, "children", []) or []:
                    _walk(child, depth + 1)
        if hasattr(doc, "root"):
            _walk(doc.root)
        return sections

    def _create_diff_viz(self, baseline: Any, generated: Any, mask: Any) -> Any:
        """Create a diff visualization image (PngImage)."""
        from core.png_codec import PngImage
        w, h = baseline.width, baseline.height
        channels = baseline.channels
        pixels = bytearray(w * h * channels)

        for y in range(h):
            row_off = y * w * channels
            for x in range(w):
                pix_off = row_off + x * channels
                if mask.getpixel((x, y)):
                    # Red for mismatched pixels
                    pixels[pix_off] = 255
                    pixels[pix_off + 1] = 0
                    pixels[pix_off + 2] = 0
                    if channels == 4:
                        pixels[pix_off + 3] = 255
                else:
                    # Dim version of baseline
                    base_off = pix_off
                    pixels[pix_off] = baseline.pixels[base_off] // 4
                    pixels[pix_off + 1] = baseline.pixels[base_off + 1] // 4
                    pixels[pix_off + 2] = baseline.pixels[base_off + 2] // 4
                    if channels == 4:
                        pixels[pix_off + 3] = baseline.pixels[base_off + 3]

        return PngImage(width=w, height=h, channels=channels, pixels=bytes(pixels))
