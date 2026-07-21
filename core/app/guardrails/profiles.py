"""Guardrail 設定載入（guardrail_profiles.yaml）。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_DEFAULT_PATH = Path(__file__).with_name("guardrail_profiles.yaml")


@dataclass(frozen=True, slots=True)
class GuardrailProfile:
    cot_min_steps: int = 3
    citation_similarity_threshold: float = 0.75
    adapter_quantized: bool = False


def load_profile(
    path: Path | None = None, *, quantized_override: bool | None = None
) -> GuardrailProfile:
    """讀 yaml → GuardrailProfile。quantized_override（來自 Settings.ai_adapter_quantized）優先。"""
    raw: dict[str, Any] = {}
    p = path or _DEFAULT_PATH
    if p.exists():
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    quantized = raw.get("adapter_quantized", False)
    if quantized_override is not None:
        quantized = quantized_override
    return GuardrailProfile(
        cot_min_steps=int(raw.get("cot_min_steps", 3)),
        citation_similarity_threshold=float(raw.get("citation_similarity_threshold", 0.75)),
        adapter_quantized=bool(quantized),
    )
