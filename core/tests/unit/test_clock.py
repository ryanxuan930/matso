import dataclasses

import pytest

from app.engine.clock import SimClock, SimTime


def test_default_start_is_zero() -> None:
    clock = SimClock()
    assert clock.tick == 0
    assert clock.tick_rate_ms == 1000
    assert clock.now() == SimTime(tick=0, sim_time_ms=0)


def test_advance_default_one_tick() -> None:
    clock = SimClock(tick_rate_ms=1000)
    result = clock.advance()
    assert result == SimTime(tick=1, sim_time_ms=1000)
    assert clock.now() == result


def test_advance_multiple_ticks() -> None:
    clock = SimClock(tick_rate_ms=250)
    clock.advance(4)
    assert clock.now() == SimTime(tick=4, sim_time_ms=1000)


def test_sim_time_ms_scales_with_tick_rate() -> None:
    clock = SimClock(tick_rate_ms=500)
    clock.advance(3)
    assert clock.now() == SimTime(tick=3, sim_time_ms=1500)


def test_start_tick_restore() -> None:
    # checkpoint 復原情境（O1.5）：從指定 tick 重建
    clock = SimClock(tick_rate_ms=1000, start_tick=42)
    assert clock.now() == SimTime(tick=42, sim_time_ms=42000)


def test_now_is_read_only() -> None:
    clock = SimClock()
    clock.now()
    clock.now()
    assert clock.tick == 0  # 讀取不推進時間


@pytest.mark.parametrize("bad_rate", [0, -1, -1000])
def test_invalid_tick_rate_rejected(bad_rate: int) -> None:
    with pytest.raises(ValueError, match="tick_rate_ms"):
        SimClock(tick_rate_ms=bad_rate)


def test_negative_start_tick_rejected() -> None:
    with pytest.raises(ValueError, match="start_tick"):
        SimClock(start_tick=-1)


@pytest.mark.parametrize("bad_ticks", [0, -1])
def test_advance_requires_positive(bad_ticks: int) -> None:
    clock = SimClock()
    with pytest.raises(ValueError, match="ticks"):
        clock.advance(bad_ticks)


def test_sim_time_is_frozen() -> None:
    t = SimTime(tick=1, sim_time_ms=1000)
    with pytest.raises(dataclasses.FrozenInstanceError):
        t.tick = 2  # type: ignore[misc]


def test_sim_time_ordering() -> None:
    assert SimTime(tick=1, sim_time_ms=1000) < SimTime(tick=2, sim_time_ms=2000)
    assert SimTime(tick=5, sim_time_ms=5000) == SimTime(tick=5, sim_time_ms=5000)
