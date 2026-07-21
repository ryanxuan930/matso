"""入庫管線 CLI（SPEC_FULL §9.4，O6.3）——只吃 markdown（PDF 走 SPEC_INGEST/O9）。

    uv run python -m matso_ai.rag.ingest <corpus_dir> [--qdrant :memory:|<url>] [--dim N]

走訪 corpus/<collection>/*.md（略過 README/_collection.md）→ chunk → 嵌入 → upsert Qdrant。
預設 HashEmbedder（RAG 目前空、無 bge-m3 模型時仍可跑通管線）；真部署以 --embedder bge-m3。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from qdrant_client import QdrantClient

from matso_ai.rag.chunker import Chunk, chunk_markdown
from matso_ai.rag.embedder import Embedder, HashEmbedder
from matso_ai.rag.store import RagStore

_SKIP = {"README.md", "_collection.md", "MANIFEST.md"}


def collect_chunks(corpus_dir: Path) -> list[Chunk]:
    """走訪 corpus 目錄，回傳所有 chunk（collection 由子目錄名決定，覆寫 front-matter）。"""
    chunks: list[Chunk] = []
    for md in sorted(corpus_dir.rglob("*.md")):
        if md.name in _SKIP:
            continue
        rel = md.relative_to(corpus_dir)
        collection = rel.parts[0] if len(rel.parts) > 1 else None
        chunks.extend(
            chunk_markdown(md.read_text(encoding="utf-8"), str(rel), collection_override=collection)
        )
    return chunks


def ingest(corpus_dir: Path, store: RagStore, embedder: Embedder) -> int:
    """入庫，回傳 upsert 的 chunk 數。空目錄→0（合法，不報錯）。"""
    store.ensure_collections()
    chunks = collect_chunks(corpus_dir)
    if not chunks:
        return 0
    vectors = embedder.embed([c.text for c in chunks])
    return store.upsert(chunks, vectors)


def _build_embedder(name: str, dim: int) -> Embedder:
    if name == "hash":
        return HashEmbedder(dim=dim)
    raise SystemExit(  # pragma: no cover
        f"embedder '{name}' 未實作（bge-m3 真後端於部署時惰性載入；RAG 目前以 hash 跑通管線）"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="MATSO RAG 入庫（markdown → Qdrant）")
    ap.add_argument("corpus_dir", type=Path)
    ap.add_argument("--qdrant", default=":memory:", help="Qdrant 位址或 :memory:")
    ap.add_argument("--embedder", default="hash", choices=["hash"])
    ap.add_argument("--dim", type=int, default=64)
    args = ap.parse_args(argv)

    embedder = _build_embedder(args.embedder, args.dim)
    client = (
        QdrantClient(location=":memory:")
        if args.qdrant == ":memory:"
        else QdrantClient(url=args.qdrant)
    )
    store = RagStore(client, dim=args.dim)
    n = ingest(args.corpus_dir, store, embedder)
    print(f"ingested {n} chunks（total in index: {store.total_count()}）")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
