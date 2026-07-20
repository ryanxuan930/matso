"""HealthMonitor 狀態機測試（O2.5）——以腳本化 poll_once 驗 3-strikes / 恢復，無 threading。"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock

from matso_sdk import HealthState

from app.plugins.terrain_client import HealthMonitor


def _monitor_with_states(states: list[HealthState]) -> tuple[HealthMonitor, MagicMock]:
    controller = MagicMock()
    mon = HealthMonitor(channel=MagicMock(), controller=controller, failure_threshold=3)
    it: Iterator[HealthState] = iter(states)
    mon.poll_once = lambda: next(it)  # type: ignore[method-assign]
    return mon, controller


def test_pauses_after_three_consecutive_failures() -> None:
    mon, controller = _monitor_with_states([HealthState.DOWN] * 3)
    mon.evaluate()
    assert not mon.is_down  # 1 次
    controller.pause_all.assert_not_called()
    mon.evaluate()
    assert not mon.is_down  # 2 次
    mon.evaluate()  # 第 3 次 → DOWN
    assert mon.is_down
    controller.pause_all.assert_called_once()


def test_intermittent_failures_do_not_pause() -> None:
    mon, controller = _monitor_with_states(
        [HealthState.DOWN, HealthState.DOWN, HealthState.HEALTHY, HealthState.DOWN]
    )
    for _ in range(4):
        mon.evaluate()
    assert not mon.is_down  # 連續計數被 HEALTHY 打斷 → 未達 3
    controller.pause_all.assert_not_called()


def test_degraded_is_not_a_failure() -> None:
    mon, controller = _monitor_with_states([HealthState.DEGRADED] * 5)
    for _ in range(5):
        mon.evaluate()
    assert not mon.is_down  # DEGRADED = 插件活著，不算失敗
    controller.pause_all.assert_not_called()


def test_resume_after_recovery() -> None:
    mon, controller = _monitor_with_states(
        [HealthState.DOWN, HealthState.DOWN, HealthState.DOWN, HealthState.HEALTHY]
    )
    for _ in range(3):
        mon.evaluate()
    assert mon.is_down
    controller.pause_all.assert_called_once()
    mon.evaluate()  # 恢復
    assert not mon.is_down
    controller.resume_all.assert_called_once()


def test_pause_fired_once_while_down() -> None:
    mon, controller = _monitor_with_states([HealthState.DOWN] * 6)
    for _ in range(6):
        mon.evaluate()
    controller.pause_all.assert_called_once()  # 持續 DOWN 不重複觸發
