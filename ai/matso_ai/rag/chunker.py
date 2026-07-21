"""Markdown 語料切塊（SPEC_FULL §9.4：512 tokens, overlap 64）。

依 corpus 格式（ai/rag/corpus/README.md）：front-matter（collection/source/...）+ 每個可引用段落
為 `## [ANCHOR-ID] 標題`。切塊以「一節一 chunk」為主；過長的節再切窗（近似以字元數）。
純函數、無 I/O、可單元測試。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import yaml

# 近似：512 token ≈ 2000 字元（中英混）；overlap 64 token ≈ 256 字元。
_CHARS_PER_CHUNK = 2000
_OVERLAP_CHARS = 256
_ANCHOR_RE = re.compile(r"^##\s*\[([A-Z0-9][A-Z0-9\-]*)\]\s*(.*)$")
_FRONT_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass(frozen=True, slots=True)
class Chunk:
    collection: str
    doc_path: str  # 相對 corpus 的路徑，如 doctrine_general/foo.md
    anchor: str  # 穩定錨點 id，如 GEN-01（無標題錨點者為 ""）
    text: str


def parse_front_matter(md: str) -> tuple[dict[str, object], str]:
    """回傳 (front-matter dict, body)。無 front-matter → ({}, 全文)。"""
    m = _FRONT_RE.match(md)
    if not m:
        return {}, md
    data = yaml.safe_load(m.group(1)) or {}
    return (data if isinstance(data, dict) else {}), md[m.end() :]


def chunk_markdown(
    md: str, doc_path: str, *, collection_override: str | None = None
) -> list[Chunk]:
    """把一份 markdown 切成 Chunk 清單。collection 取 front-matter，可被 override。"""
    front, body = parse_front_matter(md)
    collection = collection_override or str(front.get("collection") or "doctrine_general")

    sections = _split_by_anchor(body)
    chunks: list[Chunk] = []
    for anchor, text in sections:
        clean = text.strip()
        if not clean:
            continue
        for piece in _windows(clean):
            chunks.append(
                Chunk(collection=collection, doc_path=doc_path, anchor=anchor, text=piece)
            )
    return chunks


def _split_by_anchor(body: str) -> list[tuple[str, str]]:
    """依 `## [ANCHOR] 標題` 切段。標題前的序言（若有）歸於 anchor=""。"""
    out: list[tuple[str, str]] = []
    cur_anchor = ""
    cur: list[str] = []
    for line in body.splitlines():
        m = _ANCHOR_RE.match(line)
        if m:
            if cur:
                out.append((cur_anchor, "\n".join(cur)))
            cur_anchor = m.group(1)
            cur = [line]
        else:
            cur.append(line)
    if cur:
        out.append((cur_anchor, "\n".join(cur)))
    return out


def _windows(text: str) -> list[str]:
    """過長段落再切窗（overlap）；短段原樣單一 chunk。"""
    if len(text) <= _CHARS_PER_CHUNK:
        return [text]
    pieces: list[str] = []
    start = 0
    step = _CHARS_PER_CHUNK - _OVERLAP_CHARS
    while start < len(text):
        pieces.append(text[start : start + _CHARS_PER_CHUNK])
        start += step
    return pieces
