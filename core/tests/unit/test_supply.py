"""補給類別與消耗（WP-C7.1）：中性預設、階梯、接線。

[JTLS-F p.1058] Class I–X 與再訂購水位；[JCATS-A p.26–27]「絕非申請後直接恢復戰力」。
"""

from __future__ import annotations

import pytest

from app.adjudication.supply import (
    REORDER_LEVEL,
    SupplyClass,
    SupplyLevel,
    consume,
    daily_consumption,
    is_below_reorder,
    starvation_modifier,
)
from app.engine.supply_wiring import (
    STARVED_DAYS_KEY,
    SUPPLY_KEY,
    SUPPLY_TICK_KEY,
    needs_resupply,
    read_levels,
    supply_effectiveness,
    tick_supply,
    write_levels,
)
from app.state.hot_state import InMemoryHotState

_TICK_MS = 60_000  # 1 tick = 1 分鐘
_DAY_TICKS = 1440


# ---- 中性預設：既有局位元不變 ----


def test_a_unit_without_a_supply_key_is_left_completely_alone() -> None:
    """**本卡最重要的接線保護**：既有局的熱狀態沒有 `supply` 鍵。

    回 None ＝呼叫端不寫任何熱狀態鍵 ＝ STATE_DIFF 零雜訊 ＝ golden 不必重錄。
    """
    hot = InMemoryHotState()
    hot.put_unit("u1", {"lat": 24.0, "lng": 121.0})
    assert tick_supply(hot, "u1", 100, _TICK_MS) is None


def test_missing_key_reads_as_empty_not_as_all_zero() -> None:
    """空 dict 與「全部 0」差很多：後者會讓每個既有單位看起來都處於斷補狀態。"""
    assert read_levels({}) == {}
    assert read_levels({SUPPLY_KEY: {}}) == {}
    assert supply_effectiveness({}) == 1.0
    assert needs_resupply({}) == []


def test_the_first_settlement_only_starts_the_clock() -> None:
    """宣告了補給的單位第一次被結算時，**只寫時鐘起點、不扣任何存量**。

    ⚠ 這條原本斷言的是「預設消耗率是 0 所以什麼都不寫」。那個中性是用**把物理關掉**換來的
    ——`DAILY_CONSUMPTION` 已經改成校準過的真值（Class I 1.0 補給日/日），
    真正的中性保證是上面那條（沒有 `supply` 鍵就一個鍵都不寫）。

    而「只寫時鐘起點」這一步不能省：`supply_tick` 的唯一寫入端就是這個 patch，
    以前缺鍵時直接把「上次結算」當成「現在」→ `elapsed` 恆為 0 → 那個鍵永遠不會被寫
    → **宣告了補給的單位永遠不吃飯**。既有測試看不出來，因為它們每一條都自己種了它。
    """
    hot = InMemoryHotState()
    hot.put_unit("u1", {SUPPLY_KEY: {"I": [10.0, 10.0]}})

    patch = tick_supply(hot, "u1", _DAY_TICKS, _TICK_MS)

    assert patch == {SUPPLY_TICK_KEY: _DAY_TICKS}
    assert daily_consumption(SupplyClass.I) == 1.0
    # 時鐘起好之後才真的開始扣。
    hot.update_unit("u1", patch)
    after = tick_supply(hot, "u1", _DAY_TICKS * 2, _TICK_MS)
    assert after is not None and after[SUPPLY_KEY]["I"][0] == pytest.approx(9.0)


def test_a_tick_with_zero_actual_consumption_writes_nothing_at_all() -> None:
    """**連時間戳都不寫**：完全沒有消耗的局不該每 tick 推一次 STATE_DIFF。

    ⚠ 上一條測試碰不到這個 guard——它沒有 `supply_tick`，於是 `elapsed == 0` 就先回 None 了。
    要走到「有經過時間、但消耗量是 0」那條路才驗得到（突變測試抓出來的）。
    """
    hot = InMemoryHotState()
    hot.put_unit("u1", {SUPPLY_KEY: {"I": [10.0, 10.0]}, SUPPLY_TICK_KEY: 0})
    assert tick_supply(hot, "u1", _DAY_TICKS, _TICK_MS, {"I": 0.0}) is None


def test_an_undeclared_class_never_consumes_or_reorders() -> None:
    """`capacity <= 0` ＝**未編制**，不是「空的」。否則每個單位都會為它沒有的東西申請補給。"""
    empty = SupplyLevel(0.0, 0.0)
    assert empty.declared is False
    assert consume(empty, 5.0, 100, _TICK_MS) is empty
    assert is_below_reorder(empty) is False


# ---- 消耗 ----


def test_consumption_is_proportional_to_elapsed_simulated_time() -> None:
    level = SupplyLevel(10.0, 10.0)
    one_day = consume(level, 3.0, _DAY_TICKS, _TICK_MS)
    assert one_day.on_hand == pytest.approx(7.0)
    half = consume(level, 3.0, _DAY_TICKS // 2, _TICK_MS)
    assert half.on_hand == pytest.approx(8.5)


def test_stock_never_goes_negative() -> None:
    assert consume(SupplyLevel(1.0, 10.0), 100.0, _DAY_TICKS, _TICK_MS).on_hand == 0.0


def test_settlement_uses_elapsed_ticks_so_a_rollback_stays_consistent() -> None:
    """**按經過 tick 補算**而不是每 tick 扣一點：每 tick 扣會累積浮點誤差，
    而且 checkpoint 回滾之後帳目就對不起來。回滾把 `supply_tick` 一起帶回去，帳自動一致。"""
    hot = InMemoryHotState()
    hot.put_unit("u1", {SUPPLY_KEY: {"I": [10.0, 10.0]}, SUPPLY_TICK_KEY: 0})
    patch = tick_supply(hot, "u1", _DAY_TICKS, _TICK_MS, {"I": 4.0})
    assert patch is not None
    assert patch[SUPPLY_KEY]["I"][0] == pytest.approx(6.0)
    assert patch[SUPPLY_TICK_KEY] == _DAY_TICKS


def test_levels_serialise_in_a_stable_order() -> None:
    """熱狀態會進 `compute_state_hash`——dict 順序不穩就會讓同一個世界算出不同雜湊。"""
    levels = {SupplyClass.IX: SupplyLevel(1.0, 2.0), SupplyClass.I: SupplyLevel(3.0, 4.0)}
    assert list(write_levels(levels)) == ["I", "IX"]


def test_an_unknown_class_in_hot_state_is_skipped_not_fatal() -> None:
    assert read_levels({SUPPLY_KEY: {"XVII": [1.0, 2.0], "I": [3.0, 4.0]}}) == {
        SupplyClass.I: SupplyLevel(3.0, 4.0)
    }


# ---- 斷補階梯 ----


def test_starvation_is_a_staircase_not_a_cliff() -> None:
    """[JCATS-A p.26–27]：補給不足是**逐步失能**。一刀切成 0 會讓後勤變成一個開關，
    而開關沒有可供指揮官權衡的中間狀態。"""
    assert starvation_modifier(0.0) == 1.0
    values = [starvation_modifier(d) for d in (1.0, 2.0, 3.0, 5.0)]
    assert values == sorted(values, reverse=True)
    assert all(0.0 < v < 1.0 for v in values)


def test_never_starved_means_exactly_one() -> None:
    """既有局沒有斷補概念 → 恆為 1.0 → 射手效能位元不變。"""
    assert starvation_modifier(-1.0) == 1.0
    assert supply_effectiveness({STARVED_DAYS_KEY: 0.0}) == 1.0


def test_starved_days_accumulate_while_empty_and_reset_when_resupplied() -> None:
    hot = InMemoryHotState()
    hot.put_unit("u1", {SUPPLY_KEY: {"I": [1.0, 10.0]}, SUPPLY_TICK_KEY: 0})
    # 一天吃掉 4 份 → 見底 → 開始累積斷補天數。
    patch = tick_supply(hot, "u1", _DAY_TICKS, _TICK_MS, {"I": 4.0})
    assert patch is not None and patch[SUPPLY_KEY]["I"][0] == 0.0
    assert patch[STARVED_DAYS_KEY] > 0.0
    hot.update_unit("u1", patch)
    # 補給到位 → 歸零。
    hot.update_unit("u1", {SUPPLY_KEY: {"I": [10.0, 10.0]}})
    again = tick_supply(hot, "u1", _DAY_TICKS * 2, _TICK_MS, {"I": 4.0})
    assert again is not None and again[STARVED_DAYS_KEY] == 0.0


def test_only_rations_drive_starvation_not_repair_parts() -> None:
    """只看 Class I——口糧斷了才是「斷補」；維修件（IX）見底影響的是修復，不是即刻戰力。"""
    hot = InMemoryHotState()
    hot.put_unit("u1", {SUPPLY_KEY: {"IX": [0.5, 10.0]}, SUPPLY_TICK_KEY: 0})
    patch = tick_supply(hot, "u1", _DAY_TICKS, _TICK_MS, {"IX": 4.0})
    assert patch is not None and patch[STARVED_DAYS_KEY] == 0.0


# ---- 再訂購水位 ----


def test_reorder_triggers_below_the_threshold_only() -> None:
    assert is_below_reorder(SupplyLevel(1.0, 10.0)) is True
    assert is_below_reorder(SupplyLevel(9.0, 10.0)) is False
    assert needs_resupply({SUPPLY_KEY: {"I": [1.0, 10.0], "IX": [9.0, 10.0]}}) == [SupplyClass.I]
    assert REORDER_LEVEL == 0.3


def test_needs_resupply_is_deterministically_ordered() -> None:
    """觸發清單會變成自動申請單的來源——順序不穩就會讓同一個世界產生不同的令序。"""
    state = {SUPPLY_KEY: {"IX": [0.0, 10.0], "I": [0.0, 10.0]}}
    assert needs_resupply(state) == [SupplyClass.I, SupplyClass.IX]
