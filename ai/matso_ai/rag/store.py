"""Qdrant 向量庫封裝（SPEC_FULL §9.4）。

6 個 collection（含 doctrine_general）。真部署連 Qdrant 服務；測試用 `RagStore.in_memory()`
（Qdrant :memory: 模式，免服務）。**空庫合法**：index_empty / total_count 供上游降級判斷。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from qdrant_client import QdrantClient, models

from matso_ai.rag.chunker import Chunk

# SPEC_FULL §9.4 六個 collection（doctrine_general 為無法歸陣營者的主力）。
COLLECTIONS: tuple[str, ...] = (
    "doctrine_general",
    "doctrine_blue",
    "doctrine_red",
    "equipment_specs",
    "terrain_analysis",
    "historical_ops",
)


class RagStore:
    def __init__(self, client: QdrantClient, *, dim: int) -> None:
        self._client = client
        self._dim = dim

    @classmethod
    def in_memory(cls, *, dim: int) -> RagStore:
        return cls(QdrantClient(location=":memory:"), dim=dim)

    def ensure_collections(self) -> None:
        existing = {c.name for c in self._client.get_collections().collections}
        for name in COLLECTIONS:
            if name not in existing:
                self._client.create_collection(
                    name,
                    vectors_config=models.VectorParams(
                        size=self._dim, distance=models.Distance.COSINE
                    ),
                )

    def upsert(self, chunks: Sequence[Chunk], vectors: Sequence[Sequence[float]]) -> int:
        by_col: dict[str, list[models.PointStruct]] = {}
        for chunk, vec in zip(chunks, vectors, strict=True):
            by_col.setdefault(chunk.collection, []).append(
                models.PointStruct(
                    id=str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            f"{chunk.doc_path}#{chunk.anchor}:{chunk.text[:32]}",
                        )
                    ),
                    vector=list(vec),
                    payload={
                        "collection": chunk.collection,
                        "doc_path": chunk.doc_path,
                        "anchor": chunk.anchor,
                        "text": chunk.text,
                    },
                )
            )
        n = 0
        for col, points in by_col.items():
            self._client.upsert(col, points=points)
            n += len(points)
        return n

    def anchor_exists(self, collection: str, doc_path: str, anchor: str) -> bool:
        """該 (doc_path, anchor) 是否存在於 collection——引用查核核心（防捏造引用，G5）。"""
        if collection not in COLLECTIONS:
            return False
        flt = models.Filter(
            must=[
                models.FieldCondition(key="doc_path", match=models.MatchValue(value=doc_path)),
                models.FieldCondition(key="anchor", match=models.MatchValue(value=anchor)),
            ]
        )
        return self._client.count(collection, count_filter=flt).count > 0

    def total_count(self) -> int:
        total = 0
        existing = {c.name for c in self._client.get_collections().collections}
        for name in COLLECTIONS:
            if name in existing:
                total += self._client.count(name).count
        return total

    @property
    def index_empty(self) -> bool:
        return self.total_count() == 0

    def search(
        self, collection: str, vector: Sequence[float], *, limit: int = 5
    ) -> list[tuple[float, dict[str, object]]]:
        if collection not in COLLECTIONS:
            return []
        res = self._client.query_points(collection, query=list(vector), limit=limit).points
        return [(p.score, dict(p.payload or {})) for p in res]
