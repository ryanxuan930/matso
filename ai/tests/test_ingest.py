"""文檔轉換（O9，SPEC_INGEST）：PDF→staging roundtrip + promote 校驗 + corpus 隔離 + 端到端。"""

from __future__ import annotations

import pytest

from matso_ai.ingest import IngestError, extract_pages, promote_markdown, to_staging_markdown
from matso_ai.ingest.structure import build_sections


def _make_pdf(tmp_path, paragraphs):  # type: ignore[no-untyped-def]
    import fitz

    doc = fitz.open()
    for para in paragraphs:
        page = doc.new_page()
        page.insert_text((72, 72), para, fontsize=11)
    path = tmp_path / "doc.pdf"
    doc.save(str(path))
    doc.close()
    return path


# born-digital PDF fixture 用 ASCII（PyMuPDF 預設字型不含 CJK 字形）。
_DOCTRINE = (
    "RED DELAY OPERATIONS\n"
    "Each natural defile MUST be covered by at least one squad-sized observation post, "
    "reporting contact 30 minutes before the enemy main body arrives."
)


# ---- O9.1 parse + structure ----


def test_extract_born_digital_pdf_high_confidence(tmp_path) -> None:
    pdf = _make_pdf(tmp_path, [_DOCTRINE])
    pages = extract_pages(pdf)
    assert len(pages) == 1
    assert pages[0].confidence == "high"  # 文字層
    assert "defile" in pages[0].text


def test_convert_produces_anchored_sections(tmp_path) -> None:
    pdf = _make_pdf(tmp_path, [_DOCTRINE])
    sections = build_sections(extract_pages(pdf), "GEN1")
    assert sections[0].anchor == "GEN1-01"
    md = to_staging_markdown(sections, source="doc.pdf")
    assert "## [GEN1-01]" in md and "classification: UNCLASSIFIED" in md
    assert "reviewer: TODO" in md  # 骨架待人工


# ---- O9.1 promote 校驗 ----


def _staging(tmp_path) -> str:  # type: ignore[no-untyped-def]
    pdf = _make_pdf(tmp_path, [_DOCTRINE])
    return to_staging_markdown(build_sections(extract_pages(pdf), "GEN1"), source="doc.pdf")


def test_promote_requires_reviewer(tmp_path) -> None:
    md = _staging(tmp_path)
    with pytest.raises(IngestError, match="reviewer"):
        promote_markdown(md, collection="doctrine_general", reviewer="TODO")


def test_promote_rejects_unknown_collection(tmp_path) -> None:
    md = _staging(tmp_path)
    with pytest.raises(IngestError, match="collection"):
        promote_markdown(md, collection="not_a_collection", reviewer="alice")


def test_promote_rejects_duplicate_anchors() -> None:
    dup = (
        "---\ncollection: TODO\nclassification: UNCLASSIFIED\nreviewer: TODO\n---\n\n"
        "## [GEN1-01] a\n內容一\n\n## [GEN1-01] b\n內容二\n"
    )
    with pytest.raises(IngestError, match="重覆錨點"):
        promote_markdown(dup, collection="doctrine_general", reviewer="alice")


def test_promote_success_fills_frontmatter(tmp_path) -> None:
    md = _staging(tmp_path)
    corpus_md = promote_markdown(md, collection="doctrine_general", reviewer="alice")
    assert "collection: doctrine_general" in corpus_md
    assert "reviewer: alice" in corpus_md
    assert "TODO" not in corpus_md  # 審核完成，無殘留


# ---- O9.3 端到端：convert → promote → O6.3 入庫 → 檢索 ----


def test_end_to_end_convert_promote_ingest(tmp_path) -> None:
    from matso_ai.rag import HashEmbedder, QdrantCitationVerifier, RagStore, chunk_markdown

    pdf = _make_pdf(tmp_path, [_DOCTRINE])
    staging = to_staging_markdown(build_sections(extract_pages(pdf), "GEN1"), source="doc.pdf")
    corpus_md = promote_markdown(staging, collection="doctrine_general", reviewer="alice")

    # 入庫（O6.3）
    store = RagStore.in_memory(dim=64)
    store.ensure_collections()
    chunks = chunk_markdown(corpus_md, "doctrine_general/gen1.md")
    store.upsert(chunks, HashEmbedder(dim=64).embed([c.text for c in chunks]))

    # 引用查核命中 promote 出來的錨點
    v = QdrantCitationVerifier(store)
    assert v.verify("doctrine_general/gen1.md#GEN1-01") is True
    assert v.verify("doctrine_general/gen1.md#FAKE") is False
