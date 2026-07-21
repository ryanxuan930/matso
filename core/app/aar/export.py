"""AAR 匯出（O8.4，SPEC_FULL §14.2）——Ledger JSON/CSV + 匿名化選項。

匿名化（學術/去識別化）：單位真名（designation）→ 匿名標籤（UNIT-N）、去除 reasoning_chain
（CoT 可能含人名/敏感語）。**匿名化後不得含任何使用者名或單位真名**。
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Sequence
from typing import Any

from app.aar.events import AarEvent

_CSV_FIELDS = ["seq", "tick", "event_type", "initiator_id", "target_id", "damage_calc"]


def _anon_map(events: Sequence[AarEvent]) -> dict[str, str]:
    """穩定的 unit_id → 匿名標籤（依首次出現順序）。"""
    labels: dict[str, str] = {}
    for e in events:
        for uid in (e.initiator_id, e.target_id):
            if uid and uid not in labels:
                labels[uid] = f"UNIT-{len(labels) + 1}"
    return labels


def _row(e: AarEvent, anon: dict[str, str] | None) -> dict[str, Any]:
    def _u(uid: str | None) -> str | None:
        if uid is None:
            return None
        return anon.get(uid, uid) if anon is not None else uid

    row: dict[str, Any] = {
        "seq": e.seq,
        "tick": e.tick,
        "event_type": e.event_type,
        "initiator_id": _u(e.initiator_id),
        "target_id": _u(e.target_id),
        "damage_calc": e.damage_calc,
    }
    if anon is None:
        # 完整匯出含 ai_decision / CoT；匿名化時一律省略（可能含敏感文字）。
        row["ai_decision"] = e.ai_decision
        row["reasoning_chain"] = e.reasoning_chain
    return row


def export_json(events: Sequence[AarEvent], *, anonymize: bool = False) -> str:
    anon = _anon_map(events) if anonymize else None
    return json.dumps([_row(e, anon) for e in events], ensure_ascii=False, indent=2)


def export_csv(events: Sequence[AarEvent], *, anonymize: bool = False) -> str:
    anon = _anon_map(events) if anonymize else None
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for e in events:
        writer.writerow(_row(e, anon))
    return buf.getvalue()
