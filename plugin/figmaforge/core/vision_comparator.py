#!/usr/bin/env python3
"""
Vision Design Comparator.

Evaluates design-intent similarity between a Figma baseline screenshot and
a generated HTML screenshot using a vision-capable LLM.  Bypasses the
cross-engine pixel-diff ceiling (Figma renderer ≠ Chromium).

Two modes:
  - agent: output scoring prompt + image paths for the calling agent
  - api: call a vision API directly (OpenAI, Anthropic, Gemini, custom)

Providers (auto-detected from env vars):
  - OPENAI_API_KEY      → GPT-4o
  - ANTHROPIC_API_KEY   → Claude Sonnet
  - GEMINI_API_KEY      → Gemini 1.5 Pro
  - Custom: set FIGMAFORGE_VISION_API_URL + FIGMAFORGE_VISION_API_KEY
  - Fallback: mock (deterministic test scores)
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

@dataclass
class VisionScore:
    layout: float = 0.0
    color: float = 0.0
    typography: float = 0.0
    spacing: float = 0.0
    hierarchy: float = 0.0
    composition: float = 0.0
    overall: float = 0.0
    summary: str = ""
    issues: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    latency_ms: int = 0
    raw_response: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "VisionScore":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def passes(self, threshold: float = 0.85) -> bool:
        return self.overall >= threshold

    def __str__(self) -> str:
        return (
            f"VisionScore({self.overall:.0%}) "
            f"[layout={self.layout:.0%} color={self.color:.0%} "
            f"typography={self.typography:.0%} spacing={self.spacing:.0%} "
            f"hierarchy={self.hierarchy:.0%} composition={self.composition:.0%}]"
        )


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_VISION_PROMPT = """\
You are an expert UI/UX designer evaluating visual fidelity.

You will receive two screenshots:
1. **Baseline**: The original Figma design
2. **Generated**: An HTML/CSS recreation

Score each dimension 0.0 (completely wrong) to 1.0 (perfect match).

## Dimensions
- **layout**: Element positions, alignment, grid. Are elements in the right place?
- **color**: Background colors, text colors, accents, gradients, opacity.
- **typography**: Font size, weight, family, line-height, spacing, alignment.
- **spacing**: Margins, padding, gaps, whitespace.
- **hierarchy**: Visual weight — headings vs body, primary vs secondary.
- **composition**: Overall balance, proportion, image placement, visual flow.
- **overall**: Weighted average.

## Return ONLY valid JSON:
{
  "layout": 0.XX, "color": 0.XX, "typography": 0.XX,
  "spacing": 0.XX, "hierarchy": 0.XX, "composition": 0.XX,
  "overall": 0.XX,
  "summary": "One sentence.",
  "issues": ["Max 5 issues."],
  "suggestions": ["Max 5 fixes."]
}

Be honest. Score high for genuinely well-matched dimensions. Score low for mismatches.
"""


# ---------------------------------------------------------------------------
# Provider implementations
# ---------------------------------------------------------------------------

def _http_post(url: str, payload: dict, headers: dict, timeout: int = 60) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _parse_response(raw: str) -> VisionScore:
    text = raw.strip()
    text = re.sub(r"```(?:json)?\s*", "", text)
    text = re.sub(r"```\s*$", "", text)
    text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
        if match:
            data = json.loads(match.group())
        else:
            return VisionScore(summary=f"Parse error: {text[:200]}", raw_response=raw)
    return VisionScore(
        layout=float(data.get("layout", 0)),
        color=float(data.get("color", 0)),
        typography=float(data.get("typography", 0)),
        spacing=float(data.get("spacing", 0)),
        hierarchy=float(data.get("hierarchy", 0)),
        composition=float(data.get("composition", 0)),
        overall=float(data.get("overall", 0)),
        summary=data.get("summary", ""),
        issues=data.get("issues", []),
        suggestions=data.get("suggestions", []),
        raw_response=raw,
    )


def _vision_request_openai(api_key: str, model: str, b64_a: str, b64_b: str) -> str:
    body = _http_post(
        "https://api.openai.com/v1/chat/completions",
        {
            "model": model,
            "messages": [
                {"role": "system", "content": _VISION_PROMPT},
                {"role": "user", "content": [
                    {"type": "text", "text": "Compare these two screenshots. First=baseline, second=generated."},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_a}", "detail": "high"}},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_b}", "detail": "high"}},
                ]},
            ],
            "max_tokens": 1024,
            "temperature": 0.1,
        },
        {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    return body["choices"][0]["message"]["content"]


def _vision_request_anthropic(api_key: str, model: str, b64_a: str, b64_b: str) -> str:
    body = _http_post(
        "https://api.anthropic.com/v1/messages",
        {
            "model": model,
            "max_tokens": 1024,
            "system": _VISION_PROMPT,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "Compare these two screenshots. First=baseline, second=generated."},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64_a}},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64_b}},
            ]}],
        },
        {"Content-Type": "application/json", "x-api-key": api_key, "anthropic-version": "2023-06-01"},
    )
    return body["content"][0]["text"]


def _vision_request_gemini(api_key: str, model: str, b64_a: str, b64_b: str) -> str:
    body = _http_post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}",
        {"contents": [{"parts": [
            {"text": _VISION_PROMPT + "\n\nCompare: first=baseline, second=generated."},
            {"inline_data": {"mime_type": "image/png", "data": b64_a}},
            {"inline_data": {"mime_type": "image/png", "data": b64_b}},
        ]}], "generationConfig": {"temperature": 0.1, "maxOutputTokens": 1024}},
        {"Content-Type": "application/json"},
    )
    return body["candidates"][0]["content"]["parts"][0]["text"]


def _vision_request_custom(url: str, api_key: str, model: str, b64_a: str, b64_b: str) -> str:
    """Generic OpenAI-compatible endpoint (vLLM, Ollama, Together, etc.)."""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    body = _http_post(
        url,
        {
            "model": model,
            "messages": [
                {"role": "system", "content": _VISION_PROMPT},
                {"role": "user", "content": [
                    {"type": "text", "text": "Compare these two screenshots. First=baseline, second=generated."},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_a}", "detail": "high"}},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_b}", "detail": "high"}},
                ]},
            ],
            "max_tokens": 1024,
            "temperature": 0.1,
        },
        headers,
    )
    return body["choices"][0]["message"]["content"]


# ---------------------------------------------------------------------------
# Mock provider
# ---------------------------------------------------------------------------

def _vision_request_mock(b64_a: str, b64_b: str) -> str:
    return json.dumps({
        "layout": 0.82, "color": 0.78, "typography": 0.85,
        "spacing": 0.80, "hierarchy": 0.88, "composition": 0.75,
        "overall": 0.81,
        "summary": "Mock: general layout captured, gradient and text positioning need work.",
        "issues": ["Gradient darker than baseline", "Text y-offset ~14px off", "Image cropping differs"],
        "suggestions": ["Adjust gradient alpha to 0.65x", "Fix heading y by ~14px", "Use object-fit:cover"],
    })


# ---------------------------------------------------------------------------
# Main comparator
# ---------------------------------------------------------------------------

class VisionComparator:
    """Compare two screenshots using a vision LLM.

    Usage:
        comp = VisionComparator.from_env()
        score = comp.compare("baseline.png", "generated.png")
    """

    def __init__(self, provider: str = "auto", api_key: Optional[str] = None,
                 model: Optional[str] = None, api_url: Optional[str] = None):
        self.provider_name, self._api_key, self._model, self._api_url = \
            self._resolve(provider, api_key, model, api_url)

    @classmethod
    def from_env(cls, provider: str = "auto") -> "VisionComparator":
        return cls(provider=provider)

    @classmethod
    def available_providers(cls) -> List[str]:
        available = ["mock"]
        for env in ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"]:
            if os.environ.get(env):
                available.append(env.split("_")[0].lower())
        if os.environ.get("FIGMAFORGE_VISION_API_URL"):
            available.append("custom")
        return available

    @staticmethod
    def _resolve(provider, api_key, model, api_url):
        if provider != "auto":
            key = api_key or os.environ.get(f"{provider.upper()}_API_KEY", "")
            url = api_url or os.environ.get("FIGMAFORGE_VISION_API_URL", "")
            models = {"openai": "gpt-4o", "anthropic": "claude-sonnet-4-20250514",
                      "gemini": "gemini-1.5-pro", "custom": "default"}
            return provider, key, model or models.get(provider, "gpt-4o"), url

        # Auto-detect
        if os.environ.get("OPENAI_API_KEY"):
            return "openai", os.environ["OPENAI_API_KEY"], model or "gpt-4o", ""
        if os.environ.get("ANTHROPIC_API_KEY"):
            return "anthropic", os.environ["ANTHROPIC_API_KEY"], model or "claude-sonnet-4-20250514", ""
        if os.environ.get("GEMINI_API_KEY"):
            return "gemini", os.environ["GEMINI_API_KEY"], model or "gemini-1.5-pro", ""
        url = api_url or os.environ.get("FIGMAFORGE_VISION_API_URL", "")
        if url:
            key = api_key or os.environ.get("FIGMAFORGE_VISION_API_KEY", "")
            return "custom", key, model or "default", url
        return "mock", "", "", ""

    def compare(self, baseline_path: str | Path, generated_path: str | Path) -> VisionScore:
        baseline_path, generated_path = Path(baseline_path), Path(generated_path)
        if not baseline_path.exists():
            raise FileNotFoundError(f"Baseline not found: {baseline_path}")
        if not generated_path.exists():
            raise FileNotFoundError(f"Generated not found: {generated_path}")

        b64_a = base64.b64encode(baseline_path.read_bytes()).decode("ascii")
        b64_b = base64.b64encode(generated_path.read_bytes()).decode("ascii")

        start = time.monotonic()
        raw = self._call(b64_a, b64_b)
        elapsed = int((time.monotonic() - start) * 1000)

        score = _parse_response(raw)
        score.provider = self.provider_name
        score.model = self._model
        score.latency_ms = elapsed
        return score

    def compare_images(self, b64_a: str, b64_b: str,
                       mime_a: str = "image/png", mime_b: str = "image/png") -> VisionScore:
        start = time.monotonic()
        raw = self._call(b64_a, b64_b)
        elapsed = int((time.monotonic() - start) * 1000)
        score = _parse_response(raw)
        score.provider = self.provider_name
        score.model = self._model
        score.latency_ms = elapsed
        return score

    def _call(self, b64_a: str, b64_b: str) -> str:
        if self.provider_name == "mock":
            return _vision_request_mock(b64_a, b64_b)
        if not self._api_key and self.provider_name != "custom":
            raise ValueError(f"No API key for {self.provider_name}. Set {self.provider_name.upper()}_API_KEY.")
        if self.provider_name == "openai":
            return _vision_request_openai(self._api_key, self._model, b64_a, b64_b)
        if self.provider_name == "anthropic":
            return _vision_request_anthropic(self._api_key, self._model, b64_a, b64_b)
        if self.provider_name == "gemini":
            return _vision_request_gemini(self._api_key, self._model, b64_a, b64_b)
        if self.provider_name == "custom":
            return _vision_request_custom(self._api_url, self._api_key, self._model, b64_a, b64_b)
        raise ValueError(f"Unknown provider: {self.provider_name}")


# ---------------------------------------------------------------------------
# Agent mode helpers
# ---------------------------------------------------------------------------

def generate_agent_prompt(baseline_path: str | Path, generated_path: str | Path) -> str:
    """Generate scoring prompt for an agent to evaluate as the vision model."""
    return (
        f"Compare these two screenshots and score design-intent similarity.\n\n"
        f"**Baseline**: {Path(baseline_path).resolve()}\n"
        f"**Generated**: {Path(generated_path).resolve()}\n\n"
        f"Read both images. Score each dimension 0.0–1.0:\n"
        f"- layout, color, typography, spacing, hierarchy, composition, overall\n\n"
        f"Return ONLY a JSON object:\n"
        f'{{"layout":0.XX,"color":0.XX,"typography":0.XX,"spacing":0.XX,'
        f'"hierarchy":0.XX,"composition":0.XX,"overall":0.XX,'
        f'"summary":"...","issues":["..."],"suggestions":["..."]}}'
    )


def parse_agent_score(response: str) -> VisionScore:
    """Parse an agent's JSON response into a VisionScore."""
    score = _parse_response(response)
    score.provider = "agent"
    return score


def vision_compare(baseline: str | Path, generated: str | Path,
                   provider: str = "auto", api_key: Optional[str] = None) -> VisionScore:
    """One-shot vision comparison."""
    return VisionComparator(provider=provider, api_key=api_key).compare(baseline, generated)
