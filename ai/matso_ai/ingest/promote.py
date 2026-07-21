"""promote：staging → corpus（O9.1）——格式校驗 + 強制 reviewer。corpus 的唯一寫入路徑。

校驗：front-matter 五欄齊全（collection 合法、classification=UNCLASSIFIED、reviewer 非 TODO）、
錨點唯一、無殘留 TODO。任何不符 → IngestError（含原因）。
"""

from __future__ import annotations

import re

import yaml

from matso_ai.rag.store import COLLECTIONS

_FRONT_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_ANCHOR_RE = re.compile(r"^##\s*\[([A-Z0-9][A-Z0-9\-]*)\]", re.MULTILINE)


class IngestError(ValueError):
    """promote 校驗失敗。"""


def promote_markdown(staging_md: str, *, collection: str, reviewer: str) -> str:
    """把 staging markdown 校驗並轉為 corpus-ready markdown（填入 collection/reviewer）。"""
    if collection not in COLLECTIONS:
        raise IngestError(f"未知 collection：{collection}（須為 {', '.join(COLLECTIONS)}）")
    if not reviewer or reviewer == "TODO":
        raise IngestError("promote 必須指定 reviewer（人工審核者）")

    m = _FRONT_RE.match(staging_md)
    if not m:
        raise IngestError("缺少 front-matter")
    front = yaml.safe_load(m.group(1)) or {}
    if not isinstance(front, dict):
        raise IngestError("front-matter 非 mapping")
    if front.get("classification") != "UNCLASSIFIED":
        raise IngestError("classification 必須為 UNCLASSIFIED")

    body = staging_md[m.end() :]
    anchors = _ANCHOR_RE.findall(body)
    if not anchors:
        raise IngestError("無任何錨點段落")
    dupes = {a for a in anchors if anchors.count(a) > 1}
    if dupes:
        raise IngestError(f"重覆錨點：{', '.join(sorted(dupes))}")
    if "TODO" in body:
        raise IngestError("正文仍含 TODO（未完成審核）")

    # 填入審核後 front-matter。
    front["collection"] = collection
    front["reviewer"] = reviewer
    if not front.get("version") or front.get("version") == "TODO":
        front["version"] = "1.0"
    new_front = yaml.safe_dump(front, allow_unicode=True, sort_keys=False).strip()
    return f"---\n{new_front}\n---\n{body}"
