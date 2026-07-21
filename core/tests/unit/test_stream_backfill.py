"""重連補償範圍檢查（O4.3，ws_protocol.md / O1.7-R7）——plan_resume 純邏輯。"""

from __future__ import annotations

from app.stream.backfill import plan_resume, select_backfill, seq_range


def test_fresh_client_no_backfill() -> None:
    p = plan_resume(None, None, None)  # 空 ring + 無 last_seq
    assert not p.resync and p.backfill_after_seq is None and p.resumed_from_seq == 0


def test_fresh_client_with_existing_ring() -> None:
    p = plan_resume(5, 10, None)
    assert not p.resync and p.backfill_after_seq is None and p.resumed_from_seq == 10


def test_last_seq_zero_empty_ring_is_fresh() -> None:
    p = plan_resume(None, None, 0)
    assert not p.resync and p.backfill_after_seq is None


def test_last_seq_positive_empty_ring_resyncs() -> None:
    # server 無 ring 但 client 宣稱看過 seq → 倒退/重置 → RESYNC
    assert plan_resume(None, None, 5).resync is True


def test_up_to_date_no_backfill() -> None:
    p = plan_resume(1, 10, 10)
    assert not p.resync and p.backfill_after_seq is None and p.resumed_from_seq == 10


def test_backfill_middle() -> None:
    p = plan_resume(1, 10, 5)
    assert not p.resync and p.backfill_after_seq == 5 and p.resumed_from_seq == 5


def test_backfill_whole_ring_when_last_seq_zero() -> None:
    p = plan_resume(1, 10, 0)
    assert not p.resync and p.backfill_after_seq == 0


def test_boundary_last_seq_equals_min_minus_one_backfills_all() -> None:
    # client 看到剛好 ring 最舊之前一格 → 補送整個 ring（無缺口）
    p = plan_resume(5, 10, 4)
    assert not p.resync and p.backfill_after_seq == 4


def test_gap_too_large_resyncs() -> None:
    # last_seq < ring_min-1：中間事件已被 trim → RESYNC
    assert plan_resume(5, 10, 3).resync is True


def test_seq_regression_resyncs() -> None:
    # last_seq > ring_max：reset_stream 後新串流 seq 較低 → RESYNC
    assert plan_resume(1, 10, 11).resync is True


# ---------------- seq_range / select_backfill ----------------


def test_seq_range() -> None:
    envs = [{"seq": 3}, {"seq": 1}, {"seq": 2}]
    assert seq_range(envs) == (1, 3)
    assert seq_range([]) == (None, None)


def test_select_backfill_preserves_order() -> None:
    envs = [{"seq": 1}, {"seq": 2}, {"seq": 3}, {"seq": 4}]
    assert [e["seq"] for e in select_backfill(envs, 2)] == [3, 4]
    assert select_backfill(envs, 4) == []
