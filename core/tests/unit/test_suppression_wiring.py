"""壓制與姿態的活執行期接線（WP-C1）。

純函數的部分在 `test_suppression.py`。這裡釘的是**接線**：
熱狀態怎麼讀寫、既有局位元不變、以及 POSTURE 令終於會做事。
"""

from __future__ import annotations

from app.adjudication.suppression import Posture
from app.engine.engage_wiring import make_engage_env
from app.engine.suppression_wiring import (
    POSTURE_KEY,
    SUPPRESSION_KEY,
    apply_hit_suppression,
    interrupt_posture,
    read_posture,
    set_posture,
    tick_suppression,
)
from app.state.hot_state import InMemoryHotState


def _hot_pair() -> InMemoryHotState:
    hot = InMemoryHotState()
    hot.put_unit("S", {"lat": 24.0, "lng": 121.0})
    hot.put_unit("T", {"lat": 24.01, "lng": 121.0})
    return hot


# ---- 既有局位元不變 ----


def test_a_session_with_no_suppression_keys_gets_exactly_one() -> None:
    """**這條是本卡最重要的保護**：既有局的熱狀態沒有 `suppression`/`posture` 鍵，
    修正必須剛好 1.0，命中率一個位元都不能動。"""
    env = make_engage_env(_hot_pair())("S", "T")
    assert env.shooter_suppression_modifier == 1.0
    assert env.target_posture_modifier == 1.0


def test_a_clean_tick_writes_nothing() -> None:
    """每 tick 對每個單位寫一次「壓制還是 0」是純粹的雜訊——會推爆 STATE_DIFF。"""
    hot = _hot_pair()
    hot.drain_diff()
    assert tick_suppression(hot, 5) == 0
    assert hot.drain_diff() == {}


# ---- 累積與效果 ----


def test_being_shelled_degrades_your_shooting() -> None:
    hot = _hot_pair()
    apply_hit_suppression(hot, "S", "ARTILLERY")
    env = make_engage_env(hot)("S", "T")
    assert env.shooter_suppression_modifier < 1.0


def test_digging_in_makes_you_harder_to_hit() -> None:
    hot = _hot_pair()
    hot.update_unit("T", {POSTURE_KEY: Posture.DUG_IN.value})
    env = make_engage_env(hot)("S", "T")
    assert env.target_posture_modifier == 0.5


def test_an_unknown_posture_falls_back_to_moving() -> None:
    """熱狀態被寫進奇怪的值 → 中性，不是崩潰。"""
    hot = _hot_pair()
    hot.update_unit("T", {POSTURE_KEY: "SLEEPING"})
    assert make_engage_env(hot)("S", "T").target_posture_modifier == 1.0


def test_suppression_decays_over_ticks() -> None:
    hot = _hot_pair()
    apply_hit_suppression(hot, "S", "ARTILLERY")
    for tick in range(20):
        tick_suppression(hot, tick)
    assert (hot.get_unit("S") or {})[SUPPRESSION_KEY] == 0.0


# ---- 姿態 ----


def test_posture_takes_time_to_settle() -> None:
    """宣告掘壕的那一秒就享有掘壕防護，會讓工事變成一個免費按鈕。"""
    hot = _hot_pair()
    set_posture(hot, "T", Posture.DUG_IN, tick=0)
    assert make_engage_env(hot)("S", "T").target_posture_modifier == 1.0  # 還在挖

    for tick in range(0, 245):
        tick_suppression(hot, tick)
    assert make_engage_env(hot)("S", "T").target_posture_modifier == 0.5


def test_moving_wipes_the_posture() -> None:
    hot = _hot_pair()
    set_posture(hot, "T", Posture.HASTY, tick=0)
    tick_suppression(hot, 1)
    assert read_posture(hot.get_unit("T") or {}).current is Posture.HASTY
    interrupt_posture(hot, "T", 2)
    assert read_posture(hot.get_unit("T") or {}).current is Posture.MOVING


def test_interrupting_an_already_moving_unit_writes_nothing() -> None:
    """每個移動中的單位每 tick 推一次無意義的 diff——那會蓋掉真正的變化。"""
    hot = _hot_pair()
    hot.drain_diff()
    interrupt_posture(hot, "T", 9)
    assert hot.drain_diff() == {}
