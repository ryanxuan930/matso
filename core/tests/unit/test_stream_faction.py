"""WS faction-scope 過濾 + 背壓（O4.3）。"""

from __future__ import annotations

import pytest

from app.models import UserRole
from app.stream.faction_filter import is_omniscient, is_visible
from app.stream.sender import BackpressureError, BoundedSender

# ---------------- faction 過濾 ----------------


def test_omniscient_roles() -> None:
    assert is_omniscient(UserRole.EXERCISE_DIRECTOR)
    assert is_omniscient(UserRole.WHITE_CELL_STAFF)
    assert is_omniscient(UserRole.ADMIN)
    assert not is_omniscient(UserRole.COMMANDER)
    assert not is_omniscient(UserRole.OBSERVER)


def test_unscoped_envelope_visible_to_all() -> None:
    assert is_visible({"type": "CLOCK"}, "BLUE", omniscient=False) is True


def test_scoped_envelope_only_own_faction() -> None:
    env = {"type": "EVENT", "faction": "RED"}
    assert is_visible(env, "RED", omniscient=False) is True
    assert is_visible(env, "BLUE", omniscient=False) is False


def test_omniscient_sees_all_factions() -> None:
    env = {"type": "EVENT", "faction": "RED"}
    assert is_visible(env, "WHITE_CELL", omniscient=True) is True


# ---------------- 背壓 ----------------


async def test_bounded_sender_fifo() -> None:
    s = BoundedSender(maxsize=4)
    s.offer("a")
    s.offer("b")
    assert await s.next() == "a"
    assert await s.next() == "b"


async def test_bounded_sender_overflow_raises() -> None:
    # 慢 client（不消費）→ 佇列填滿 → 溢出斷線訊號
    s = BoundedSender(maxsize=3)
    s.offer(1)
    s.offer(2)
    s.offer(3)
    with pytest.raises(BackpressureError):
        s.offer(4)
    assert s.pending() == 3
