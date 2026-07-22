"""TickPacer / run_paced 單元測試（O1.7/R6：SPEC §3.3 自動降頻）。"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from app.engine.kernel import Kernel, TickReport
from app.runtime import TickPacer, run_paced

_NS_PER_MS = 1_000_000


def _report(duration_ms: float, overran: bool, tick: int = 0) -> TickReport:
    return TickReport(
        tick=tick,
        events_written=0,
        duration_ns=int(duration_ms * _NS_PER_MS),
        overran=overran,
    )


def test_normal_tick_sleeps_remaining_interval() -> None:
    pacer = TickPacer(tick_rate_ms=1000)
    assert pacer.next_delay_s(_report(200, overran=False)) == pytest.approx(0.8)
    assert pacer.slowdown == 1.0


def test_compression_scales_interval() -> None:
    pacer = TickPacer(tick_rate_ms=1000, compression=10.0)  # 10x → 額定 100ms
    assert pacer.next_delay_s(_report(20, overran=False)) == pytest.approx(0.08)


def test_slow_tick_never_negative_delay() -> None:
    pacer = TickPacer(tick_rate_ms=1000)
    assert pacer.next_delay_s(_report(1500, overran=True)) == 0.0


def test_sustained_overrun_triggers_slowdown() -> None:
    pacer = TickPacer(tick_rate_ms=100, backoff_after=3, backoff_factor=2.0)
    for _ in range(2):
        pacer.next_delay_s(_report(250, overran=True))
    assert pacer.slowdown == 1.0  # 未達門檻
    pacer.next_delay_s(_report(250, overran=True))  # 第 3 次連續 overrun
    assert pacer.slowdown == 2.0  # 自動降頻啟動
    # 降頻後額定間隔 100ms×2=200ms；耗時 250ms → 仍 0 等待，但持續 overrun 續退
    pacer.next_delay_s(_report(250, overran=True))
    assert pacer.slowdown == 4.0


def test_slowdown_capped_at_max() -> None:
    pacer = TickPacer(tick_rate_ms=100, backoff_after=1, backoff_factor=10.0, max_slowdown=8.0)
    for _ in range(5):
        pacer.next_delay_s(_report(500, overran=True))
    assert pacer.slowdown == 8.0


def test_recovery_decays_slowdown_back_to_nominal() -> None:
    pacer = TickPacer(tick_rate_ms=100, backoff_after=1, backoff_factor=2.0)
    pacer.next_delay_s(_report(300, overran=True))
    pacer.next_delay_s(_report(300, overran=True))
    assert pacer.slowdown == 4.0
    pacer.next_delay_s(_report(10, overran=False))
    assert pacer.slowdown == 2.0
    pacer.next_delay_s(_report(10, overran=False))
    assert pacer.slowdown == 1.0
    pacer.next_delay_s(_report(10, overran=False))
    assert pacer.slowdown == 1.0  # 不低於 1.0


@pytest.mark.parametrize("bad", [0, -5])
def test_invalid_tick_rate_rejected(bad: int) -> None:
    with pytest.raises(ValueError, match="tick_rate_ms"):
        TickPacer(tick_rate_ms=bad)


def test_zero_compression_rejected() -> None:
    with pytest.raises(ValueError, match="compression"):
        TickPacer(tick_rate_ms=1000, compression=0)


async def test_run_paced_runs_n_ticks_and_sleeps(
    build_noop_kernel: Callable[..., Kernel],
) -> None:
    kernel = build_noop_kernel()
    pacer = TickPacer(tick_rate_ms=1000)
    delays: list[float] = []

    async def fake_sleep(s: float) -> None:
        delays.append(s)

    count = await run_paced(kernel, pacer, ticks=3, sleep=fake_sleep)
    assert count == 3
    assert len(delays) == 3
    # NullMonotonicClock → duration 0 → 每次等待 = 額定 1.0s
    assert all(d == pytest.approx(1.0) for d in delays)


async def test_run_paced_should_stop(build_noop_kernel: Callable[..., Kernel]) -> None:
    kernel = build_noop_kernel()
    pacer = TickPacer(tick_rate_ms=1000)
    ran: list[int] = []

    async def fake_sleep(s: float) -> None:
        ran.append(1)

    count = await run_paced(
        kernel, pacer, ticks=None, sleep=fake_sleep, should_stop=lambda: len(ran) >= 2
    )
    assert count == 2


async def test_run_paced_pause_freezes_ticks(
    build_noop_kernel: Callable[..., Kernel],
) -> None:
    # 新 #6：should_pause 為 True 時凍結——不推進 tick，僅輪詢；解除後才跑滿 ticks。
    kernel = build_noop_kernel()
    pacer = TickPacer(tick_rate_ms=1000)
    sleeps = {"n": 0}
    paused = {"v": True}

    async def fake_sleep(_s: float) -> None:
        sleeps["n"] += 1
        if sleeps["n"] >= 3:
            paused["v"] = False  # 三次輪詢後解除暫停

    count = await run_paced(
        kernel, pacer, ticks=2, sleep=fake_sleep, should_pause=lambda: paused["v"]
    )
    assert count == 2  # 解除後才跑滿 2 tick
    assert sleeps["n"] == 5  # 3 次暫停輪詢 + 2 次 tick 後 sleep


async def test_run_paced_requires_termination_condition(
    build_noop_kernel: Callable[..., Kernel],
) -> None:
    with pytest.raises(ValueError, match="should_stop"):
        await run_paced(build_noop_kernel(), TickPacer(tick_rate_ms=1000), ticks=None)
