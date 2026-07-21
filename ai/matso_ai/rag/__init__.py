"""RAG 子系統（SPEC_FULL §9.4）——入庫、檢索、引用查核。

**空語料是常態**（RAG 目前為空，SPEC_FULL §9.0）：store 空時 index_empty=True，
QdrantCitationVerifier.verify 一律 False，上游護欄 G5 自動按 AI_BARE 語義處理，不失敗。

真部署以 bge-m3 嵌入 + Qdrant 服務；測試以 HashEmbedder + Qdrant :memory: 模式（免 GPU/服務）。
"""

from __future__ import annotations

from matso_ai.rag.chunker import Chunk, chunk_markdown
from matso_ai.rag.embedder import Embedder, HashEmbedder
from matso_ai.rag.store import COLLECTIONS, RagStore
from matso_ai.rag.verifier import QdrantCitationVerifier, parse_citation

__all__ = [
    "COLLECTIONS",
    "Chunk",
    "Embedder",
    "HashEmbedder",
    "QdrantCitationVerifier",
    "RagStore",
    "chunk_markdown",
    "parse_citation",
]
