"""活模擬彈藥即時調整命令通道（#52）：push → drain → apply。"""

from __future__ import annotations

from fakeredis import FakeStrictRedis

from app.state.hot_state import InMemoryHotState
from app.state.live_ammo import apply_ammo_cmds, drain_ammo_cmds, push_ammo_cmd


def test_push_drain_roundtrip() -> None:
    client = FakeStrictRedis(decode_responses=True)
    push_ammo_cmd(client, "s1", "u1", "w-jav", 20)
    push_ammo_cmd(client, "s1", "u1", "w-atgm", 5)
    push_ammo_cmd(client, "s2", "u9", "w-x", 1)  # 他 session 不受影響
    cmds = drain_ammo_cmds(client, "s1")
    assert [(c["unit_id"], c["weapon_id"], c["ammo"]) for c in cmds] == [
        ("u1", "w-jav", 20),
        ("u1", "w-atgm", 5),
    ]
    # drain 後清空
    assert drain_ammo_cmds(client, "s1") == []
    # s2 仍在
    assert len(drain_ammo_cmds(client, "s2")) == 1


def test_apply_overwrites_ammo_by_weapon() -> None:
    hot = InMemoryHotState()
    hot.put_unit("u1", {"ammo_by_weapon": {"w-jav": 0, "w-atgm": 100}})
    n = apply_ammo_cmds(hot, [{"unit_id": "u1", "weapon_id": "w-jav", "ammo": 20}])
    assert n == 1
    st = hot.get_unit("u1")
    assert st is not None
    # 權威覆寫 Javelin=20，其餘保留。
    assert st["ammo_by_weapon"] == {"w-jav": 20, "w-atgm": 100}


def test_apply_skips_unit_without_hot_state() -> None:
    # 無此單位熱狀態（sim 尚未 seed）→ 略過，不建殘缺 unit（避免干擾之後的 seed）。
    hot = InMemoryHotState()
    n = apply_ammo_cmds(hot, [{"unit_id": "ghost", "weapon_id": "w", "ammo": 9}])
    assert n == 0
    assert hot.get_unit("ghost") is None


def test_apply_creates_ammo_by_weapon_when_absent() -> None:
    # 單位已 seed（有 lat 等）但尚無 ammo_by_weapon → 建立並寫入。
    hot = InMemoryHotState()
    hot.put_unit("u1", {"lat": 1.0, "lng": 2.0})
    apply_ammo_cmds(hot, [{"unit_id": "u1", "weapon_id": "w", "ammo": 7}])
    st = hot.get_unit("u1")
    assert st is not None and st["ammo_by_weapon"] == {"w": 7}


def test_apply_ignores_malformed() -> None:
    hot = InMemoryHotState()
    hot.put_unit("u1", {"ammo_by_weapon": {}})
    n = apply_ammo_cmds(
        hot,
        [
            {"unit_id": "u1", "weapon_id": "w", "ammo": "x"},  # 壞 ammo
            {"unit_id": "u1", "ammo": 5},  # 缺 weapon_id
            {"weapon_id": "w", "ammo": 5},  # 缺 unit_id
        ],
    )
    assert n == 0
