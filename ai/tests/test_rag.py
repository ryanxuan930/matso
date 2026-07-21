"""RAG 管線（O6.3）：chunk → 入庫 → 檢索 → 引用查核 roundtrip + 空庫降級。"""

from __future__ import annotations

from matso_ai.rag import (
    HashEmbedder,
    QdrantCitationVerifier,
    RagStore,
    chunk_markdown,
    parse_citation,
)
from matso_ai.rag.ingest import collect_chunks, ingest, main

_DOC = """---
collection: doctrine_general
source: 合成教學
classification: UNCLASSIFIED
---

序言（無錨點）。

## [GEN-01] 遲滯要領
每一天然隘口 MUST 部署至少 1 個班級觀測所。

## [GEN-02] 撤退節奏
一旦敵開始協調攻擊即撤離，不與其決戰。
"""


def test_chunk_markdown_splits_by_anchor() -> None:
    chunks = chunk_markdown(_DOC, "doctrine_general/delay.md")
    anchors = [c.anchor for c in chunks]
    assert "GEN-01" in anchors and "GEN-02" in anchors
    assert all(c.collection == "doctrine_general" for c in chunks)


def test_parse_citation() -> None:
    assert parse_citation("doctrine_general/delay.md#GEN-01") == (
        "doctrine_general",
        "doctrine_general/delay.md",
        "GEN-01",
    )
    assert parse_citation("no-hash") is None
    assert parse_citation("#anchor-only") is None


def test_ingest_retrieve_citation_roundtrip() -> None:
    store = RagStore.in_memory(dim=64)
    store.ensure_collections()
    chunks = chunk_markdown(_DOC, "doctrine_general/delay.md")
    emb = HashEmbedder(dim=64)
    store.upsert(chunks, emb.embed([c.text for c in chunks]))

    # 檢索：以 GEN-01 chunk 原文查回自己（HashEmbedder 非語意，故用完全相同文字驗管線）。
    gen01 = next(c for c in chunks if c.anchor == "GEN-01")
    hits = store.search("doctrine_general", emb.embed([gen01.text])[0])
    assert hits and hits[0][1]["anchor"] == "GEN-01"

    # 引用查核：真錨點 True、捏造錨點 False
    v = QdrantCitationVerifier(store)
    assert v.verify("doctrine_general/delay.md#GEN-01") is True
    assert v.verify("doctrine_general/delay.md#FAKE-99") is False
    assert v.verify("doctrine_red/other.md#GEN-01") is False  # 錯 collection
    assert v.index_empty is False


def test_empty_index_degrades_not_fails() -> None:
    """空庫（RAG 現實）：index_empty=True、任何引用 verify=False——不報錯（SPEC §9.0）。"""
    store = RagStore.in_memory(dim=64)
    store.ensure_collections()
    v = QdrantCitationVerifier(store)
    assert v.index_empty is True
    assert v.verify("doctrine_general/anything.md#X-01") is False


def test_ingest_empty_dir_returns_zero(tmp_path) -> None:
    store = RagStore.in_memory(dim=64)
    assert ingest(tmp_path, store, HashEmbedder(dim=64)) == 0
    assert store.index_empty is True


def test_ingest_real_corpus_dir(tmp_path) -> None:
    """collect_chunks 走訪目錄並以子目錄名為 collection；略過 README/_collection。"""
    (tmp_path / "doctrine_general").mkdir()
    (tmp_path / "doctrine_general" / "delay.md").write_text(_DOC, encoding="utf-8")
    (tmp_path / "doctrine_general" / "_collection.md").write_text("skip me", encoding="utf-8")
    chunks = collect_chunks(tmp_path)
    assert chunks and all(c.collection == "doctrine_general" for c in chunks)
    assert all("_collection" not in c.doc_path for c in chunks)

    store = RagStore.in_memory(dim=64)
    n = ingest(tmp_path, store, HashEmbedder(dim=64))
    assert n == len(chunks) and store.total_count() == n


def test_ingest_cli_main(tmp_path, capsys) -> None:
    (tmp_path / "doctrine_general").mkdir()
    (tmp_path / "doctrine_general" / "delay.md").write_text(_DOC, encoding="utf-8")
    rc = main([str(tmp_path), "--qdrant", ":memory:", "--dim", "32"])
    assert rc == 0
    assert "ingested" in capsys.readouterr().out
