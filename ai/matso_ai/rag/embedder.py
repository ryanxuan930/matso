"""嵌入器（SPEC_FULL §9.4：bge-m3 中英雙語）。

- `HashEmbedder`：確定性、無模型——測試/CI 用（免下載 2GB 模型、免 GPU）。品質不足以檢索，
  但足以驗證入庫→檢索→查核管線的正確性。
- `load_bge_m3()`：真 bge-m3（WP-F1）。**惰性載入且不硬相依**——air-gapped 部署把模型檔
  納入外接資產（`MATSO_BGE_M3_PATH` 注入路徑）。

## 為什麼是「取得器回 None」而不是拋

模型不在（沒裝套件、沒有模型檔、載入失敗）是**部署現實**，不是程式錯誤：
本專案的 RAG 語料長期不足是設計前提，`AI_BARE` 模式本來就要能跑。
`load_bge_m3()` 回 `None` 讓呼叫端**明確地**決定降級並向使用者標示「檢索品質降級」
——拋例外會讓呼叫端要嘛 try/except 包起來（等於同一件事寫得更醜），
要嘛整個 ingest CLI 在沒有模型的機器上根本跑不起來。

⚠ **降級一定要看得見**。`describe_embedder()` 回一個給 UI 用的說明，
無聲降級會讓人以為檢索品質正常而信任它的引用。
"""

from __future__ import annotations

import hashlib
import logging
import os
import struct
from collections.abc import Sequence
from typing import Any, Protocol

_LOG = logging.getLogger(__name__)


class Embedder(Protocol):
    dim: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...  # pragma: no cover


# air-gapped 部署以 env 注入模型路徑（模型檔是部署資產，不進 repo）。
BGE_M3_PATH_ENV = "MATSO_BGE_M3_PATH"
BGE_M3_DIM = 1024


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


class Bge3Embedder:
    """真 bge-m3 後端。由 `load_bge_m3()` 建立——**不要直接 new**（那會把相依變成硬相依）。"""

    def __init__(self, model: Any, dim: int = BGE_M3_DIM) -> None:
        self._model = model
        self.dim = dim

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = self._model.encode(list(texts), normalize_embeddings=True)
        return [list(map(float, v)) for v in vectors]


def load_bge_m3(path: str | None = None) -> Bge3Embedder | None:
    """載入 bge-m3。**取不到一律回 None**（見模組說明）。

    `path` 未給則讀 `MATSO_BGE_M3_PATH`。air-gapped 下必須是本機路徑——
    不傳路徑而讓套件自己去下載，在斷網機器上會變成一次數十秒的逾時而不是一個明確的失敗。
    """
    location = path or os.environ.get(BGE_M3_PATH_ENV, "")
    if not location:
        _LOG.info("未設定 %s，檢索改用 hash 嵌入（品質降級）", BGE_M3_PATH_ENV)
        return None
    try:
        # 部署資產、非硬相依 → mypy 找不到 stub 是**預期**的（air-gapped 機器上才會裝）。
        from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]
    except ImportError:
        _LOG.warning("sentence-transformers 未安裝，檢索改用 hash 嵌入（品質降級）")
        return None
    try:
        model = SentenceTransformer(location, local_files_only=True)
    except Exception:
        _LOG.warning("bge-m3 載入失敗（path=%s），檢索改用 hash 嵌入（品質降級）", location)
        return None
    return Bge3Embedder(model)


def describe_embedder(embedder: Embedder) -> dict[str, Any]:
    """給 UI 的嵌入器說明。**降級一定要看得見**——無聲降級會讓人以為檢索品質正常。"""
    degraded = isinstance(embedder, HashEmbedder)
    return {
        "kind": type(embedder).__name__,
        "dim": embedder.dim,
        "degraded": degraded,
        "note": "檢索品質降級：未載入 bge-m3，改用雜湊嵌入" if degraded else "",
    }
