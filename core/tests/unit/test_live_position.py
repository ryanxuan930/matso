"""地圖狀態編輯座標命令通道（push/drain/apply）。"""

from __future__ import annotations

from app.state.hot_state import InMemoryHotState
from app.state.live_position import apply_pos_cmds, drain_pos_cmds, pos_cmd_key, push_pos_cmd


class _FakeRedis:
    def __init__(self) -> None:
        self._lists: dict[str, list[str]] = {}

    def rpush(self, key: str, val: str) -> None:
        self._lists.setdefault(key, []).append(val)

    def pipeline(self) -> _FakePipe:
        return _FakePipe(self)


class _FakePipe:
    def __init__(self, r: _FakeRedis) -> None:
        self._r = r
        self._ops: list[tuple[str, str]] = []

    def lrange(self, key: str, _a: int, _b: int) -> None:
        self._ops.append(("lrange", key))

    def delete(self, key: str) -> None:
        self._ops.append(("delete", key))

    def execute(self) -> list:
        out: list = []
        for op, key in self._ops:
            if op == "lrange":
                out.append(list(self._r._lists.get(key, [])))
            else:
                self._r._lists.pop(key, None)
                out.append(1)
        return out


def test_push_drain_roundtrip() -> None:
    r = _FakeRedis()
    push_pos_cmd(r, "s1", "u1", 24.1, 121.2)
    push_pos_cmd(r, "s1", "u2", 24.3, 121.4)
    cmds = drain_pos_cmds(r, "s1")
    assert [(c["unit_id"], c["lat"], c["lng"]) for c in cmds] == [
        ("u1", 24.1, 121.2),
        ("u2", 24.3, 121.4),
    ]
    assert drain_pos_cmds(r, "s1") == []  # drain 後清空


def test_apply_sets_hot_lat_lng() -> None:
    hot = InMemoryHotState()
    hot.update_unit("u1", {"lat": 0.0, "lng": 0.0, "strength": 100.0})
    n = apply_pos_cmds(hot, [{"unit_id": "u1", "lat": 24.5, "lng": 121.5}])
    assert n == 1
    s = hot.get_unit("u1")
    assert s is not None and s["lat"] == 24.5 and s["lng"] == 121.5
    assert s["strength"] == 100.0  # 只改座標，不動其他欄位


def test_apply_last_wins_and_skips_unseeded() -> None:
    hot = InMemoryHotState()
    hot.update_unit("u1", {"lat": 0.0, "lng": 0.0})
    # 同單位多筆 → 取最後；未 seed 的 ghost → 略過（不建立熱狀態）。
    n = apply_pos_cmds(
        hot,
        [
            {"unit_id": "u1", "lat": 1.0, "lng": 1.0},
            {"unit_id": "u1", "lat": 2.0, "lng": 2.0},
            {"unit_id": "ghost", "lat": 9.0, "lng": 9.0},
        ],
    )
    assert n == 1
    assert hot.get_unit("u1") == {"lat": 2.0, "lng": 2.0}
    assert hot.get_unit("ghost") is None


def test_pos_cmd_key() -> None:
    assert pos_cmd_key("abc") == "session:abc:pos_cmds"
