#!/usr/bin/env python3
"""Agent iteration tools for FigmaForge.

Provides structured diff feedback that any agent (Freebuff, Claude, Cursor)
can use to drive the iteration loop. The agent IS the LLM — FigmaForge
provides the tools, the agent provides the intelligence.

Usage by agents:
1. Call figmaforge_spec → get design tokens
2. Call figmaforge_generate → get code
3. Call figmaforge_compare → get diff feedback (this module)
4. Agent analyzes the diff and fixes the code
5. Repeat until score > threshold
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class DiffFeedback:
    """Structured diff feedback for agent interpretation."""
    ssim_score: float
    verdict: str  # "identical", "changed", "render_error"
    mismatch_count: int
    mismatch_regions: List[Dict[str, Any]]
    color_mismatches: int
    layout_mismatches: int
    missing_elements: int
    extra_elements: int
    diff_summary: str  # Human-readable summary for the agent
    diff_image_path: Optional[str] = None
    generated_screenshot_path: Optional[str] = None
    baseline_screenshot_path: Optional[str] = None


@dataclass
class IterationState:
    """Tracks state across iterations for agent workflows."""
    iterations: List[Dict[str, Any]] = field(default_factory=list)
    best_score: float = 0.0
    best_iteration: int = 0
    plateau_count: int = 0
    last_score: float = 0.0


# ---------------------------------------------------------------------------
# Diff analysis
# ---------------------------------------------------------------------------

def analyze_diff(
    baseline_path: str,
    generated_path: str,
    *,
    viewport: int = 1440,
) -> DiffFeedback:
    """Compare two images and return structured diff feedback.
    
    This is the core tool agents use to understand what's wrong.
    Returns actionable feedback: which regions are wrong, what kind
    of mismatch, and a summary the agent can read.
    
    Args:
        baseline_path: Path to the original Figma screenshot (PNG)
        generated_path: Path to the agent's generated output (PNG or HTML)
        viewport: Viewport width for rendering if generated_path is HTML
        
    Returns:
        DiffFeedback with structured mismatch data
    """
    from core.render_harness import RenderHarness
    from core.pixel_diff import compare_images, resize_nearest
    from core.ssim import ssim
    from core.png_codec import decode_png
    
    baseline_path = Path(baseline_path)
    generated_path = Path(generated_path)
    
    # Load baseline
    if not baseline_path.exists():
        raise FileNotFoundError(f"Baseline not found: {baseline_path}")
    baseline_img = decode_png(baseline_path.read_bytes())
    
    # Load or render generated
    if generated_path.suffix == ".html":
        # Render HTML to screenshot
        screenshot_path = generated_path.parent / f"{generated_path.stem}_screenshot.png"
        harness = RenderHarness()
        harness.render(str(generated_path), str(screenshot_path), viewport=viewport)
        generated_img = decode_png(screenshot_path.read_bytes())
        screenshot_path_str = str(screenshot_path)
    else:
        generated_img = decode_png(generated_path.read_bytes())
        screenshot_path_str = str(generated_path)
    
    # Resize if needed
    if (baseline_img.width, baseline_img.height) != (generated_img.width, generated_img.height):
        generated_img = resize_nearest(generated_img, baseline_img.width, baseline_img.height)
    
    # Compare
    _, mask = compare_images(baseline_img, generated_img)
    ssim_score = max(0.0, min(1.0, ssim(baseline_img, generated_img)))
    
    # Extract mismatch regions
    from scripts.pipeline import _extract_mismatch_regions
    regions = _extract_mismatch_regions(mask, baseline_img.width, baseline_img.height)
    
    # Classify mismatches
    color_mismatches = [r for r in regions if r.get("kind") == "color"]
    layout_mismatches = [r for r in regions if r.get("kind") == "layout"]
    missing = [r for r in regions if r.get("kind") == "missing"]
    extra = [r for r in regions if r.get("kind") == "extra"]
    
    # Build summary
    if ssim_score >= 0.99:
        summary = "✅ Pixel-perfect match! No fixes needed."
    elif ssim_score >= 0.95:
        summary = f"✅ Near-perfect ({ssim_score:.1%}). {len(regions)} minor mismatches."
    elif ssim_score >= 0.80:
        summary = f"⚠️ Good but not perfect ({ssim_score:.1%}). {len(regions)} mismatches to fix."
    else:
        summary = f"❌ Needs work ({ssim_score:.1%}). {len(regions)} significant mismatches."
    
    # Add specific guidance
    if color_mismatches:
        summary += f"\n🎨 Color issues: {len(color_mismatches)} regions have wrong colors/gradients."
    if layout_mismatches:
        summary += f"\n📐 Layout issues: {len(layout_mismatches)} regions have wrong positioning/sizing."
    if missing:
        summary += f"\n🕳️ Missing elements: {len(missing)} elements present in baseline but not in output."
    if extra:
        summary += f"\n➕ Extra elements: {len(extra)} elements in output but not in baseline."
    
    # Add top mismatch details
    for i, r in enumerate(regions[:5]):
        summary += f"\n  Region {i+1}: {r.get('kind', '?')} at ({r.get('x', 0)}, {r.get('y', 0)}) — {r.get('description', 'N/A')}"
    
    return DiffFeedback(
        ssim_score=ssim_score,
        verdict="identical" if ssim_score >= 0.99 else "changed",
        mismatch_count=len(regions),
        mismatch_regions=regions,
        color_mismatches=len(color_mismatches),
        layout_mismatches=len(layout_mismatches),
        missing_elements=len(missing),
        extra_elements=len(extra),
        diff_summary=summary,
        generated_screenshot_path=screenshot_path_str,
        baseline_screenshot_path=str(baseline_path),
    )


def get_iteration_guidance(
    feedback: DiffFeedback,
    iteration: int,
    max_iterations: int = 10,
    target_ssim: float = 0.95,
) -> str:
    """Generate actionable guidance for the agent based on diff feedback.
    
    This tells the agent exactly what to fix and how.
    """
    if feedback.ssim_score >= target_ssim:
        return f"🎉 Target reached! SSIM {feedback.ssim_score:.1%} >= {target_ssim:.1%}. Stop iterating."
    
    remaining = max_iterations - iteration
    if remaining <= 0:
        return f"⚠️ Max iterations reached. Best score: {feedback.ssim_score:.1%}"
    
    guidance = f"""## Iteration {iteration}/{max_iterations} — Score: {feedback.ssim_score:.1%} (target: {target_ssim:.1%})
{remaining} iterations remaining.

### What to fix:
"""
    
    if feedback.color_mismatches > 0:
        guidance += f"""
**Color Issues ({feedback.color_mismatches} regions):**
- Check the exact hex values in the design spec
- Verify gradients match (start/end colors, stops)
- Check opacity values
- Ensure background colors fill the correct areas
"""
    
    if feedback.layout_mismatches > 0:
        guidance += f"""
**Layout Issues ({feedback.layout_mismatches} regions):**
- Check flexbox/grid properties
- Verify gap/margin/padding values
- Ensure elements have correct width/height
- Check if elements need position: absolute vs relative
"""
    
    if feedback.missing_elements > 0:
        guidance += f"""
**Missing Elements ({feedback.missing_elements} regions):**
- Compare your HTML structure to the baseline
- Ensure all text content is present
- Check for missing divs/sections
- Verify images are included (even as placeholders)
"""
    
    if feedback.extra_elements > 0:
        guidance += f"""
**Extra Elements ({feedback.extra_elements} regions):**
- Remove elements not in the baseline
- Check for duplicate sections
- Verify you haven't added decorative elements
"""
    
    # Top specific regions
    if feedback.mismatch_regions:
        guidance += "\n### Top mismatched regions:\n"
        for i, r in enumerate(feedback.mismatch_regions[:3]):
            guidance += f"{i+1}. **{r.get('kind', 'unknown')}** at ({r.get('x', 0)}, {r.get('y', 0)}) — {r.get('description', 'N/A')}\n"
    
    guidance += f"""
### Next steps:
1. Analyze the diff image (red = mismatched pixels)
2. Fix the issues listed above
3. Re-generate the HTML
4. Call `figmaforge_compare` again with the new output
5. Repeat until score >= {target_ssim:.0%}
"""
    
    return guidance


# ---------------------------------------------------------------------------
# State tracking
# ---------------------------------------------------------------------------

class IterationTracker:
    """Track iteration state across multiple compare calls.
    
    Agents use this to monitor progress and detect plateaus.
    """
    
    def __init__(self, target_ssim: float = 0.95, plateau_threshold: int = 3):
        self.target_ssim = target_ssim
        self.plateau_threshold = plateau_threshold
        self.state = IterationState()
    
    def record_iteration(self, feedback: DiffFeedback) -> Dict[str, Any]:
        """Record an iteration result and return status."""
        iteration = len(self.state.iterations) + 1
        
        record = {
            "iteration": iteration,
            "ssim_score": feedback.ssim_score,
            "mismatch_count": feedback.mismatch_count,
            "verdict": feedback.verdict,
        }
        self.state.iterations.append(record)
        
        # Track improvement
        improvement = feedback.ssim_score - self.state.last_score
        if improvement < 0.001:  # Less than 0.1% improvement
            self.state.plateau_count += 1
        else:
            self.state.plateau_count = 0
        
        # Update best
        if feedback.ssim_score > self.state.best_score:
            self.state.best_score = feedback.ssim_score
            self.state.best_iteration = iteration
        
        self.state.last_score = feedback.ssim_score
        
        # Build status
        status = {
            "iteration": iteration,
            "current_score": feedback.ssim_score,
            "best_score": self.state.best_score,
            "best_iteration": self.state.best_iteration,
            "target_reached": feedback.ssim_score >= self.target_ssim,
            "plateau_detected": self.state.plateau_count >= self.plateau_threshold,
            "should_stop": (
                feedback.ssim_score >= self.target_ssim or
                self.state.plateau_count >= self.plateau_threshold
            ),
        }
        
        return status
    
    def get_summary(self) -> str:
        """Get a human-readable summary of all iterations."""
        if not self.state.iterations:
            return "No iterations recorded."
        
        scores = [i["ssim_score"] for i in self.state.iterations]
        summary = f"Iteration Summary ({len(scores)} iterations):\n"
        summary += f"  Start: {scores[0]:.1%}\n"
        summary += f"  End: {scores[-1]:.1%}\n"
        summary += f"  Best: {self.state.best_score:.1%} (iteration {self.state.best_iteration})\n"
        summary += f"  Improvement: {scores[-1] - scores[0]:.1%}\n"
        
        if self.state.plateau_count >= self.plateau_threshold:
            summary += f"  ⚠️ Plateau detected ({self.state.plateau_count} rounds without improvement)\n"
        
        return summary
