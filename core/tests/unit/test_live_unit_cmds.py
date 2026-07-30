"""單位屬性即時調整命令通道（統裁編輯人數/戰力）。

守的是這條功能唯一會壞的地方：**改了 DB、熱狀態沒動**。
熱狀態才是裁決層讀的東西，只寫 DB 的話畫面上人數變了、實際打起來還是舊編制。
"""

from __future__ import annotations

from typing import Any

from app.state.hot_state import InMemoryHotState
from app.state.live_unit import EDITABLE_HOT_KEYS, apply_unit_cmds


class _FakeRedis:
    """只記下 rpush 的內容——本檔要驗的是「什麼被排進去」，不是 Redis 本身。"""

    def __init__(self) -> None:
        self.pushed: list[tuple[str, str]] = []

    def rpush(self, key: str, value: str) -> None:
        self.pushed.append((key, value))


def test_push_drops_keys_outside_the_whitelist() -> None:
    """非白名單的鍵在**排入前**就丟掉。

    不能讓它進 Redis 再靠下游過濾——那會讓「這個鍵到底能不能改」有兩個答案，
    而熱狀態裡還有 ammo_by_weapon / suppression 這些由各子系統維護的欄位，
    開放任意覆寫等於在單一寫入者上開後門。
    """
    import json

    from app.state.live_unit import push_unit_cmd

    r = _FakeRedis()
    push_unit_cmd(r, "s1", "u1", {"strength": 50, "suppression": 1.0, "ammo_by_weapon": {}})
    assert len(r.pushed) == 1
    patch = json.loads(r.pushed[0][1])["patch"]
    assert patch == {"strength": 50}, f"只有白名單的鍵該被排入，實得 {patch}"


def test_push_is_a_noop_when_nothing_is_editable() -> None:
    from app.state.live_unit import push_unit_cmd

    r = _FakeRedis()
    push_unit_cmd(r, "s1", "u1", {"designation": "B1"})  # 純顯示欄位不走這條通道
    assert r.pushed == []


def test_apply_writes_hot_state() -> None:
    hot = InMemoryHotState()
    hot.put_unit("u1", {"strength": 100.0, "authorized_strength": 100.0, "platform_count": 30})
    applied = apply_unit_cmds(
        hot, [{"unit_id": "u1", "patch": {"strength": 60.0, "platform_count": 18}}]
    )
    assert applied == 1
    state = hot.get_unit("u1")
    assert state is not None
    assert state["strength"] == 60.0
    assert state["platform_count"] == 18
    assert state["authorized_strength"] == 100.0, "沒送的鍵不該被動到"


def test_apply_merges_later_commands_over_earlier_ones() -> None:
    """同一個單位連按兩次儲存 → 後到的贏（而不是兩筆各套一半）。"""
    hot = InMemoryHotState()
    hot.put_unit("u1", {"strength": 100.0})
    apply_unit_cmds(
        hot,
        [
            {"unit_id": "u1", "patch": {"strength": 80.0}},
            {"unit_id": "u1", "patch": {"strength": 40.0}},
        ],
    )
    state = hot.get_unit("u1")
    assert state is not None and state["strength"] == 40.0


def test_apply_skips_units_the_sim_has_not_seeded() -> None:
    """sim 還沒播種的單位（例如剛從想定新增）→ 略過，不要憑空造一筆熱狀態。

    憑空造的那筆只有這一個鍵，其餘欄位全缺；裁決層讀到就會拿一堆預設值算，
    而那看起來完全正常。
    """
    hot = InMemoryHotState()
    assert apply_unit_cmds(hot, [{"unit_id": "nope", "patch": {"strength": 1.0}}]) == 0
    assert hot.get_unit("nope") is None


def test_apply_ignores_non_numeric_and_boolean_values() -> None:
    hot = InMemoryHotState()
    hot.put_unit("u1", {"strength": 100.0})
    bad: list[dict[str, Any]] = [
        {"unit_id": "u1", "patch": {"strength": "很多"}},
        {"unit_id": "u1", "patch": {"strength": True}},  # bool 是 int 的子類，要擋掉
    ]
    assert apply_unit_cmds(hot, bad) == 0
    state = hot.get_unit("u1")
    assert state is not None and state["strength"] == 100.0


def test_whitelist_covers_what_the_adjudicator_actually_reads() -> None:
    """白名單要涵蓋裁決層真的會讀的那幾個量，否則編輯了也沒效果。"""
    assert {"strength", "authorized_strength", "platform_count"} <= EDITABLE_HOT_KEYS
