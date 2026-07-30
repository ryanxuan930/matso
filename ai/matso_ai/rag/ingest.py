"""入庫管線 CLI（SPEC_FULL §9.4，O6.3）——只吃 markdown（PDF 走 SPEC_INGEST/O9）。

    uv run python -m matso_ai.rag.ingest <corpus_dir> [--qdrant :memory:|<url>] [--dim N]

走訪 corpus/<collection>/*.md（略過 README/_collection.md）→ chunk → 嵌入 → upsert Qdrant。
預設 HashEmbedder（RAG 目前空、無 bge-m3 模型時仍可跑通管線）；真部署以 --embedder bge-m3。

## WP-F1：`--embedder bge-m3` 取不到模型時**降級而不是失敗**

air-gapped 部署沒有模型是常態（模型檔是部署資產）。降級的同時：
1. stdout 明說「檢索品質降級」——不能靜靜換掉。
2. `corpus_manifest.json` 記下**當時用的是哪一種嵌入器**——否則事後看到一批品質差的
   檢索結果，無從判斷是語料不好還是嵌入器降級了。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from qdrant_client import QdrantClient

from matso_ai.rag.chunker import Chunk, chunk_markdown
from matso_ai.rag.embedder import Embedder, HashEmbedder, describe_embedder, load_bge_m3
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
    """建嵌入器。`bge-m3` 取不到模型 → **降級為 hash 而不是失敗**（見模組說明）。"""
    if name == "bge-m3":
        real = load_bge_m3()
        if real is not None:
            return real
        print("⚠ 檢索品質降級：未載入 bge-m3，改用雜湊嵌入")
    return HashEmbedder(dim=dim)


def write_manifest(corpus_dir: Path, chunks: list[Chunk], embedder: Embedder) -> Path:
    """寫 `corpus_manifest.json`（來源/版本/授權留痕 + **當時的嵌入器**）。

    嵌入器要記，理由很實際：事後看到一批品質差的檢索結果時，
    沒有這一欄就無從判斷是語料不好還是嵌入器降級了。
    """
    by_source: dict[str, int] = {}
    for chunk in chunks:
        by_source[chunk.doc_path] = by_source.get(chunk.doc_path, 0) + 1
    manifest = {
        "generated_from": str(corpus_dir),
        "chunk_count": len(chunks),
        "sources": [{"path": p, "chunks": n} for p, n in sorted(by_source.items())],
        "embedder": describe_embedder(embedder),
    }
    path = corpus_dir / "corpus_manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="MATSO RAG 入庫（markdown → Qdrant）")
    ap.add_argument("corpus_dir", type=Path)
    ap.add_argument("--qdrant", default=":memory:", help="Qdrant 位址或 :memory:")
    ap.add_argument("--embedder", default="hash", choices=["hash", "bge-m3"])
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
    manifest = write_manifest(args.corpus_dir, collect_chunks(args.corpus_dir), embedder)
    print(f"ingested {n} chunks（total in index: {store.total_count()}）")
    print(f"manifest: {manifest}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
