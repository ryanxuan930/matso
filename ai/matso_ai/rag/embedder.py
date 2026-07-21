"""嵌入器（SPEC_FULL §9.4：bge-m3 中英雙語）。

- `HashEmbedder`：確定性、無模型——測試/CI 用（免下載 2GB 模型、免 GPU）。品質不足以檢索，
  但足以驗證入庫→檢索→查核管線的正確性。
- 真 bge-m3 後端於部署時經 `load_bge_m3()` 惰性載入（FlagEmbedding/sentence-transformers）；
  air-gapped 部署把模型檔納入外接資產（env 注入路徑），此處不硬相依。
"""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Sequence
from typing import Protocol


class Embedder(Protocol):
    dim: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...  # pragma: no cover


class HashEmbedder:
    """確定性雜湊嵌入：同文字→同向量，不同文字→（幾乎必然）不同向量。僅供管線測試。"""

    def __init__(self, dim: int = 64) -> None:
        self.dim = dim

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        out: list[float] = []
        counter = 0
        while len(out) < self.dim:
            digest = hashlib.sha256(f"{counter}:{text}".encode()).digest()
            # 每 4 bytes → 一個 [0,1) float
            for i in range(0, len(digest), 4):
                if len(out) >= self.dim:
                    break
                (val,) = struct.unpack("<I", digest[i : i + 4])
                out.append(val / 0xFFFFFFFF)
            counter += 1
        norm = sum(v * v for v in out) ** 0.5 or 1.0
        return [v / norm for v in out]
