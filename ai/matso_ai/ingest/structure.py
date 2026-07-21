"""文字 → 結構化 staging markdown（O9.1/O9.3）。

分節（~512 token 近似）+ 錨點自動編（<DOC_ID>-NN）+ front-matter 骨架（collection 待人工填）
+ 信心分級（每頁 confidence 傳遞到節）+ 表格/低信心 `<!-- INGEST-REVIEW -->` 告警註記。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from matso_ai.ingest.parse import PageText

_CHARS_PER_SECTION = 1600  # ~512 token 近似（中英混）
_TABLE_HINT = re.compile(r"(\t|\s{3,}\S+\s{3,}\S+)")  # 疑似表格（多欄對齊）


@dataclass(frozen=True, slots=True)
class Section:
    anchor: str
    title: str
    body: str
    confidence: str  # high/medium/none
    review_flags: list[str] = field(default_factory=list)


def _split_windows(text: str) -> list[str]:
    if len(text) <= _CHARS_PER_SECTION:
        return [text]
    # 盡量在段落邊界切
    paras = text.split("\n\n")
    chunks: list[str] = []
    cur = ""
    for p in paras:
        if len(cur) + len(p) > _CHARS_PER_SECTION and cur:
            chunks.append(cur.strip())
            cur = ""
        cur += p + "\n\n"
    if cur.strip():
        chunks.append(cur.strip())
    return chunks


def build_sections(pages: list[PageText], doc_id: str) -> list[Section]:
    """把頁文字切成帶錨點的節。低信心/疑似表格加 review flag（供 report 與人工）。"""
    sections: list[Section] = []
    n = 0
    for page in pages:
        if not page.text:
            n += 1
            sections.append(
                Section(
                    anchor=f"{doc_id}-{n:02d}",
                    title=f"第 {page.page + 1} 頁（無文字層）",
                    body="",
                    confidence="none",
                    review_flags=["EMPTY_PAGE：需 OCR 或人工轉錄"],
                )
            )
            continue
        for piece in _split_windows(page.text):
            n += 1
            flags: list[str] = []
            if page.confidence != "high":
                flags.append(f"LOW_CONFIDENCE：{page.confidence}（OCR）")
            if _TABLE_HINT.search(piece):
                flags.append("TABLE：疑似表格，需人工確認轉換")
            first_line = piece.splitlines()[0][:40] if piece.splitlines() else ""
            sections.append(
                Section(
                    anchor=f"{doc_id}-{n:02d}",
                    title=first_line or f"節 {n}",
                    body=piece,
                    confidence=page.confidence,
                    review_flags=flags,
                )
            )
    return sections


def to_staging_markdown(sections: list[Section], *, source: str) -> str:
    """組 staging markdown：front-matter 骨架（collection 待人工填）+ 帶錨點的節 + review 註記。"""
    lines = [
        "---",
        "collection: TODO   # 人工 promote 時指定（預設 doctrine_general）",
        f'source: "{source}"',
        "classification: UNCLASSIFIED",
        "version: TODO",
        "reviewer: TODO   # promote 時強制填",
        "---",
        "",
    ]
    for s in sections:
        for flag in s.review_flags:
            lines.append(f"<!-- INGEST-REVIEW: {flag} -->")
        lines.append(f"## [{s.anchor}] {s.title}")
        lines.append(s.body)
        lines.append("")
    return "\n".join(lines)


def staging_confidence_report(sections: list[Section]) -> list[dict[str, object]]:
    """低信心/需審核節清單（O9.3 report）。"""
    return [
        {"anchor": s.anchor, "confidence": s.confidence, "flags": s.review_flags}
        for s in sections
        if s.confidence != "high" or s.review_flags
    ]
