"""WS 訊息的 faction-scope 過濾（O4.3，SPEC §12/§13.3）——純函數。

傳輸層 fog of war 閘門：envelope 可帶頂層 `faction`（目標受眾；缺／None＝廣播全體）。非全知
角色只收到 audience 為己方或全體的訊息。**每單位情報投影**（哪些敵軍 contact 可見）由上游
intel 層（O3.3 per-faction store）產出，此處只強制受眾標籤——前端過濾不可信。
"""

from __future__ import annotations

from typing import Any

from app.models import UserRole

# White Cell（統裁）：可注入事件 / 時間控制 / 修改關係（SPEC §12）。ADMIN 為系統管理，非統裁。
WHITE_CELL_ROLES = frozenset({UserRole.EXERCISE_DIRECTOR, UserRole.WHITE_CELL_STAFF})
# 全知視角角色（統裁 + 管理）：見 ground truth 全部（含 god view / 視角切換）。
OMNISCIENT_ROLES = WHITE_CELL_ROLES | {UserRole.ADMIN}


def is_omniscient(role: UserRole) -> bool:
    return role in OMNISCIENT_ROLES


def is_white_cell(role: UserRole) -> bool:
    return role in WHITE_CELL_ROLES


def is_visible(envelope: dict[str, Any], faction: str, omniscient: bool) -> bool:
    """envelope 是否應送給此 faction 的 client。全知 → 全收；否則僅收己方受眾或無受眾標籤者。"""
    if omniscient:
        return True
    audience = envelope.get("faction")
    return audience is None or audience == faction
