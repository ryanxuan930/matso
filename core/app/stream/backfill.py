"""重連補償計畫（O4.3，contracts/ws_protocol.md）——純函數、確定性、可單元測試。

client HELLO 帶 last_seq；server 依 ring buffer 現存 seq 範圍決定：補送缺漏 / 已最新 / 全量
重同步。**範圍檢查非差值檢查**（O1.7/R7）：last_seq 不在 [ring_min-1, ring_max] 內
（缺口過大或 seq 倒退＝崩潰復原後的新串流）→ RESYNC_REQUIRED。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

Envelope = dict[str, Any]


@dataclass(frozen=True, slots=True)
class ResumePlan:
    resync: bool  # True → 回 RESYNC_REQUIRED，client 走 GET /state 全量重同步
    backfill_after_seq: int | None  # None → 不補送（新客戶端或已最新）；int → 補送 seq > 此值
    resumed_from_seq: int  # WELCOME.resumed_from_seq 回報


def plan_resume(ring_min: int | None, ring_max: int | None, last_seq: int | None) -> ResumePlan:
    """依 ring 現存 seq 範圍 [ring_min, ring_max]（空 ring 為 None）與 client last_seq 決定補償。"""
    # 新客戶端（未帶 last_seq）：不補送，從當前起接 live。
    if last_seq is None:
        return ResumePlan(resync=False, backfill_after_seq=None, resumed_from_seq=ring_max or 0)

    # ring 為空：有 last_seq>0 但 server 無任何 seq → seq 倒退 / 尚未有事件的重置 → RESYNC。
    if ring_min is None or ring_max is None:
        if last_seq == 0:
            return ResumePlan(resync=False, backfill_after_seq=None, resumed_from_seq=0)
        return ResumePlan(resync=True, backfill_after_seq=None, resumed_from_seq=0)

    # seq 倒退（last_seq 超過現存最大——reset_stream 後新串流 seq 較低）→ RESYNC。
    if last_seq > ring_max:
        return ResumePlan(resync=True, backfill_after_seq=None, resumed_from_seq=0)

    # 缺口過大（欲補送的最早 seq 已被 ring trim 掉）→ RESYNC。
    if last_seq < ring_min - 1:
        return ResumePlan(resync=True, backfill_after_seq=None, resumed_from_seq=0)

    # ring_min-1 <= last_seq <= ring_max：可補償。
    if last_seq == ring_max:
        return ResumePlan(resync=False, backfill_after_seq=None, resumed_from_seq=last_seq)
    return ResumePlan(resync=False, backfill_after_seq=last_seq, resumed_from_seq=last_seq)


def seq_range(envelopes: list[Envelope]) -> tuple[int | None, int | None]:
    """ring 內 envelope 的 (最小 seq, 最大 seq)；空 → (None, None)。穩健起見取極值。"""
    seqs = [int(e["seq"]) for e in envelopes if "seq" in e]
    if not seqs:
        return None, None
    return min(seqs), max(seqs)


def select_backfill(envelopes: list[Envelope], after_seq: int) -> list[Envelope]:
    """取 seq > after_seq 的 envelope（保持原順序）。"""
    return [e for e in envelopes if int(e.get("seq", 0)) > after_seq]
