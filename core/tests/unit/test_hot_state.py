"""compute_diff 與 InMemoryHotState 單元測試（不需 Redis）。"""

from __future__ import annotations

from app.state.hot_state import InMemoryHotState, compute_diff

# ---------------- compute_diff ----------------


def test_diff_changed_field() -> None:
    assert compute_diff({"a": 1, "b": 2}, {"a": 1, "b": 3}) == {"b": 3}


def test_diff_added_field() -> None:
    assert compute_diff({"a": 1}, {"a": 1, "b": 2}) == {"b": 2}


def test_diff_no_change_is_empty() -> None:
    assert compute_diff({"a": 1, "b": 2}, {"a": 1, "b": 2}) == {}


def test_diff_three_fields() -> None:
    old = {"lat": 1.0, "lng": 2.0, "health": 100, "comms": "ONLINE"}
    new = {"lat": 1.5, "lng": 2.5, "health": 80, "comms": "ONLINE"}
    assert compute_diff(old, new) == {"lat": 1.5, "lng": 2.5, "health": 80}


def test_diff_from_empty_is_full() -> None:
    assert compute_diff({}, {"a": 1, "b": 2}) == {"a": 1, "b": 2}


# ---------------- InMemoryHotState ----------------


def test_put_and_get_roundtrip() -> None:
    hs = InMemoryHotState()
    hs.put_unit("u1", {"lat": 25.0, "health": 100})
    assert hs.get_unit("u1") == {"lat": 25.0, "health": 100}


def test_get_missing_unit_is_none() -> None:
    assert InMemoryHotState().get_unit("nope") is None


def test_put_unit_records_full_diff() -> None:
    hs = InMemoryHotState()
    hs.put_unit("u1", {"lat": 25.0, "health": 100})
    assert hs.drain_diff() == {"u1": {"lat": 25.0, "health": 100}}


def test_update_three_fields_diff_has_three() -> None:
    hs = InMemoryHotState()
    hs.put_unit("u1", {"lat": 1.0, "lng": 2.0, "health": 100, "comms": "ONLINE"})
    hs.drain_diff()  # 清掉部署 diff
    diff = hs.update_unit("u1", {"lat": 1.5, "lng": 2.5, "health": 80})
    assert diff == {"lat": 1.5, "lng": 2.5, "health": 80}
    assert hs.drain_diff() == {"u1": {"lat": 1.5, "lng": 2.5, "health": 80}}


def test_update_same_value_is_noop() -> None:
    hs = InMemoryHotState()
    hs.put_unit("u1", {"health": 100})
    hs.drain_diff()
    assert hs.update_unit("u1", {"health": 100}) == {}
    assert hs.drain_diff() == {}


def test_drain_clears_pending() -> None:
    hs = InMemoryHotState()
    hs.put_unit("u1", {"health": 100})
    hs.drain_diff()
    assert hs.drain_diff() == {}


def test_update_persists_merged_state() -> None:
    hs = InMemoryHotState()
    hs.put_unit("u1", {"lat": 1.0, "health": 100})
    hs.update_unit("u1", {"health": 50})
    assert hs.get_unit("u1") == {"lat": 1.0, "health": 50}


def test_get_all_returns_copies() -> None:
    hs = InMemoryHotState()
    hs.put_unit("u1", {"health": 100})
    hs.put_unit("u2", {"health": 90})
    snapshot = hs.get_all()
    assert snapshot == {"u1": {"health": 100}, "u2": {"health": 90}}
    snapshot["u1"]["health"] = 0  # 改副本不影響內部
    assert hs.get_unit("u1") == {"health": 100}


def test_units_are_independent() -> None:
    hs = InMemoryHotState()
    hs.put_unit("u1", {"health": 100})
    hs.put_unit("u2", {"health": 100})
    hs.drain_diff()
    hs.update_unit("u1", {"health": 50})
    assert hs.drain_diff() == {"u1": {"health": 50}}
