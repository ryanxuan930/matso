"""白軍 MSEL 命令通道（WP-B2c）。

**為什麼要一條通道**：`MselRuntime` 活在 sim runner 行程裡，白軍按的按鈕在 API 行程。
API 直接改 runtime 的記憶做不到（不同行程），直接寫 Redis 熱狀態也不行
（`RedisHotState` 有 in-process mirror，外部直寫會被忽略）。
"""

from __future__ import annotations

import json

from app.scenario.msel_runtime import MselRuntime
from app.scenario.triggers import MselEntry, TriggerContext
from app.state.live_msel import (
    FIRE,
    SKIP,
    apply_msel_cmds,
    drain_msel_cmds,
    msel_cmd_key,
    publish_pending,
    push_msel_cmd,
    read_pending,
)


class _FakeRedis:
    """夠用的假 Redis：list rpush/lrange/delete + string get/set + pipeline。"""

    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = {}
        self.strings: dict[str, str] = {}

    def rpush(self, key: str, value: str) -> None:
        self.lists.setdefault(key, []).append(value)

    def lrange(self, key: str, start: int, end: int) -> list[str]:
        return self.lists.get(key, [])[start : end + 1]

    def delete(self, key: str) -> None:
        self.lists.pop(key, None)

    def get(self, key: str) -> str | None:
        return self.strings.get(key)

    def set(self, key: str, value: str) -> None:
        self.strings[key] = value

    def pipeline(self) -> _FakePipe:
        return _FakePipe(self)


class _FakePipe:
    def __init__(self, r: _FakeRedis) -> None:
        self._r = r
        self._ops: list[tuple[str, tuple[object, ...]]] = []

    def lrange(self, key: str, start: int, end: int) -> None:
        self._ops.append(("lrange", (key, start, end)))

    def delete(self, key: str) -> None:
        self._ops.append(("delete", (key,)))

    def execute(self) -> list[object]:
        out: list[object] = []
        for op, args in self._ops:
            out.append(getattr(self._r, op)(*args))
        return out


def _rt() -> MselRuntime:
    entries = [
        MselEntry(id="m1", trigger={"type": "manual"}, inject={"event_type": "X"}),
        MselEntry(id="m2", trigger={"type": "manual"}, inject={"event_type": "Y"}),
    ]
    return MselRuntime(entries, lambda t: TriggerContext(tick=t))


def test_a_queued_fire_reaches_the_runtime() -> None:
    r = _FakeRedis()
    push_msel_cmd(r, "s1", FIRE, "m1")  # type: ignore[arg-type]
    rt = _rt()
    assert apply_msel_cmds(rt, drain_msel_cmds(r, "s1")) == 1  # type: ignore[arg-type]
    events = rt.check(type("T", (), {"tick": 7})())
    assert [e.ai_decision["msel_id"] for e in events] == ["m1"]


def test_draining_clears_the_queue() -> None:
    """套過的命令不能留著——不然每個 tick 都會再扣一次板機。"""
    r = _FakeRedis()
    push_msel_cmd(r, "s1", FIRE, "m1")  # type: ignore[arg-type]
    drain_msel_cmds(r, "s1")  # type: ignore[arg-type]
    assert drain_msel_cmds(r, "s1") == []  # type: ignore[arg-type]
    assert msel_cmd_key("s1") not in r.lists


def test_skip_prevents_firing() -> None:
    r = _FakeRedis()
    push_msel_cmd(r, "s1", SKIP, "m1")  # type: ignore[arg-type]
    push_msel_cmd(r, "s1", FIRE, "m1")  # 被跳過的就算扣板機也不發
    rt = _rt()
    apply_msel_cmds(rt, drain_msel_cmds(r, "s1"))  # type: ignore[arg-type]
    assert rt.check(type("T", (), {"tick": 7})()) == []


def test_a_corrupt_command_is_dropped_not_fatal() -> None:
    """壞掉的命令丟掉就好——一個手動塞進 Redis 的爛字串不該讓 tick 炸掉。"""
    r = _FakeRedis()
    r.rpush(msel_cmd_key("s1"), "{ not json")
    push_msel_cmd(r, "s1", FIRE, "m1")  # type: ignore[arg-type]
    cmds = drain_msel_cmds(r, "s1")  # type: ignore[arg-type]
    assert [c["entry_id"] for c in cmds] == ["m1"]


def test_unknown_action_is_ignored() -> None:
    r = _FakeRedis()
    r.rpush(msel_cmd_key("s1"), json.dumps({"action": "DETONATE", "entry_id": "m1"}))
    rt = _rt()
    assert apply_msel_cmds(rt, drain_msel_cmds(r, "s1")) == 0  # type: ignore[arg-type]


def test_pending_list_round_trips() -> None:
    r = _FakeRedis()
    publish_pending(r, "s1", ["m1", "m2"])  # type: ignore[arg-type]
    assert read_pending(r, "s1") == ["m1", "m2"]  # type: ignore[arg-type]


def test_reading_pending_for_a_dead_session_is_empty_not_an_error() -> None:
    """該局沒在跑（runner 沒發布）→ 空清單。白軍看到空的比看到 500 有用。"""
    assert read_pending(_FakeRedis(), "nope") == []  # type: ignore[arg-type]
