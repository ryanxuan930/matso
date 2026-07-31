"""引用查核索引（eval 用）：從語料檔案系統建「這個錨點存不存在」的索引。

## 為什麼不直接用 `QdrantCitationVerifier`

護欄 G5 用的是向量庫（`rag/verifier.py`），它同時查「錨點存在」與「相似度 > 閾值」。
eval runner 要能在 **CI / air-gapped 且沒有 Qdrant、沒有嵌入模型**的環境跑完；為了回答
「這份文件在語料裡真的存在嗎」而起一個向量庫並不成比例，而且錨點存不存在本來就是
**檔案事實**——用檔案回答比用向量庫回答更不可能出錯（向量庫還得先 ingest 成功）。

⚠ **本索引比 G5 寬鬆，且必須寫清楚寬在哪**：它擋得住「引用一份根本不存在的文件」
（＝捏造引用，`opfor-contradictory-001` 的誘餌就是這種），但擋不住「引用了存在卻不相干
的段落」——那需要相似度，而相似度需要嵌入器。真模型 eval 若要收緊到 G5 等級，
應在有 Qdrant 的環境把 `CitationIndex` 換成向量庫版本（介面只有 `resolves` / `empty`）。

## 空索引 ＝ AI_BARE 語義

SPEC_FULL §19.4：`AI_BARE` 或庫空時「`cited_documents` MUST 為空，任何非空引用一律視為捏造」。
空索引的 `resolves()` 恆 False，於是「所有引用都無法解析」＝「所有引用都是捏造」——
語義自然成立，呼叫端不必為空庫寫特例分支。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from matso_ai.rag.chunker import chunk_markdown
from matso_ai.rag.verifier import parse_citation

CORPUS_DIR = Path(__file__).resolve().parents[2] / "rag" / "corpus"

# 與 rag/ingest.py 的 `_SKIP` 同義（README/集合說明不是可引用語料）。
# 刻意重複而不 import：`rag.ingest` 會拉進 qdrant_client 與嵌入器，
# 而本模組存在的理由就是「不需要那些也能查引用」。
_SKIP_FILES = {"README.md", "_collection.md", "MANIFEST.md"}


@dataclass(frozen=True, slots=True)
class CitationIndex:
    """語料錨點集合。元素形如 `doctrine_red/red_delay_ops.md#RED-DELAY-03`。"""

    anchors: frozenset[str]

    @property
    def empty(self) -> bool:
        return not self.anchors

    def resolves(self, citation: str) -> bool:
        """該引用是否指向語料中真實存在的錨點。格式不合 / 不存在 → False。"""
        if not isinstance(citation, str) or parse_citation(citation) is None:
            return False
        return citation.strip() in self.anchors

    @classmethod
    def from_corpus(cls, corpus_dir: Path | str = CORPUS_DIR) -> CitationIndex:
        """走訪語料目錄，收集所有 `## [ANCHOR] …` 段落錨點。目錄不存在 → 空索引。"""
        root = Path(corpus_dir)
        if not root.is_dir():
            return cls(frozenset())
        anchors: set[str] = set()
        for md in sorted(root.rglob("*.md")):
            if md.name in _SKIP_FILES:
                continue
            rel = md.relative_to(root)
            collection = rel.parts[0] if len(rel.parts) > 1 else None
            for chunk in chunk_markdown(
                md.read_text(encoding="utf-8"), str(rel), collection_override=collection
            ):
                if chunk.anchor:  # 錨點為空的前言段落不可被引用
                    anchors.add(f"{chunk.doc_path}#{chunk.anchor}")
        return cls(frozenset(anchors))

    @classmethod
    def empty_index(cls) -> CitationIndex:
        """AI_BARE / 無語料：任何引用皆無法解析（§19.4 語義反轉）。"""
        return cls(frozenset())


__all__ = ["CORPUS_DIR", "CitationIndex"]
