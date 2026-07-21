"""引用查核（護欄 G5 用，SPEC_FULL §10）。

QdrantCitationVerifier 結構上滿足 core 的 CitationVerifier 協定（verify + index_empty），
於 O6.5 orchestration 注入 core 的 GuardrailGateway。**空庫時 index_empty=True**、verify 一律
False——Gateway 自動按 AI_BARE 語義處理（引用皆屬捏造），系統不因 RAG 空而失敗。
"""

from __future__ import annotations

from dataclasses import dataclass

from matso_ai.rag.store import RagStore


def parse_citation(citation: str) -> tuple[str, str, str] | None:
    """`collection/path/to/file.md#ANCHOR` → (collection, doc_path, anchor)；格式不符 → None。"""
    if "#" not in citation:
        return None
    doc_path, _, anchor = citation.partition("#")
    if not doc_path or not anchor or "/" not in doc_path:
        return None
    collection = doc_path.split("/", 1)[0]
    return collection, doc_path, anchor


@dataclass(frozen=True, slots=True)
class QdrantCitationVerifier:
    store: RagStore

    def verify(self, citation: str) -> bool:
        parsed = parse_citation(citation)
        if parsed is None:
            return False
        collection, doc_path, anchor = parsed
        return self.store.anchor_exists(collection, doc_path, anchor)

    @property
    def index_empty(self) -> bool:
        return self.store.index_empty
