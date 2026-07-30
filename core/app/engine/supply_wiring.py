"""補給在活執行期的接線（WP-C7.1）。

`adjudication/supply.py` 是純函數；本模組只做 I/O 邊界：熱狀態怎麼存、每 tick 怎麼扣。

## 中性保證做在入口，而且是**結構性**的

`read_levels()` 讀不到 `supply` 鍵就回空 dict，`tick_supply()` 看到空 dict 直接 return
——**一次計算都不做、一個熱狀態鍵都不寫**。既有局沒有那個鍵，所以這條路徑對它們是
零成本、零行為變更，golden 不必重錄。

⚠ WP-C3 就是在這一層栽的（`mounted` 缺鍵被 `bool()` 收成 False，命中率無聲掉 20%），
所以本卡的中性測試同樣打在接線層，不是在純函數層。

## 為什麼消耗是「按經過 tick 補算」而不是每 tick 扣一點

每 tick 扣 `rate/ticks_per_day` 會在浮點上累積誤差，而且 checkpoint 回滾之後就對不起來。
改成記「上次結算的 tick」，每次結算時按**實際經過的 tick 數**一次算清——
回滾把 `last_tick` 一起帶回去，帳目自動一致。

## 斷補天數也存在熱狀態

`starvation_modifier` 要的是「斷了幾天」，那是**狀態**不是時刻的函數（補到一次就歸零）。
與 WP-C4a 的光照不同——光照能由時鐘導出，這個不行。
"""

from __future__ import annotations

from typing import Any

from app.adjudication.supply import (
    SupplyClass,
    SupplyLevel,
    consume,
    daily_consumption,
    is_below_reorder,
    starvation_modifier,
)

SUPPLY_KEY = "supply"  # {類別: [存量, 容量]}
SUPPLY_TICK_KEY = "supply_tick"  # 上次結算的 tick
STARVED_DAYS_KEY = "starved_days"

_MS_PER_DAY = 86_400_000


def read_levels(state: dict[str, Any]) -> dict[SupplyClass, SupplyLevel]:
    """熱狀態 → 各類別水位。**缺鍵回空 dict**（不是「全部 0」）。

    空 dict 與「全部 0」差很多：後者會讓每個既有單位看起來都處於斷補狀態。
    未編制的類別（`capacity <= 0`）不消耗、不觸發再訂購、不扣效能。
    """
    raw = state.get(SUPPLY_KEY)
    if not isinstance(raw, dict) or not raw:
        return {}
    out: dict[SupplyClass, SupplyLevel] = {}
    for key, value in raw.items():
        try:
            supply_class = SupplyClass(str(key))
        except ValueError:
            continue  # 認不得的類別跳過，不讓一筆髒資料毀掉整個帳
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            try:
                out[supply_class] = SupplyLevel(float(value[0]), float(value[1]))
            except (TypeError, ValueError):
                continue
    return out


def write_levels(levels: dict[SupplyClass, SupplyLevel]) -> dict[str, list[float]]:
    """水位 → 可序列化的熱狀態片段。**依類別名排序**——熱狀態會進 `compute_state_hash`，
    dict 順序不穩就會讓同一個世界算出不同的雜湊。"""
    return {c.value: [round(v.on_hand, 4), round(v.capacity, 4)] for c, v in sorted(levels.items())}


def tick_supply(
    hot: Any,
    unit_id: str,
    now_tick: int,
    tick_rate_ms: int,
    rates: dict[str, float] | None = None,
) -> dict[str, Any] | None:
    """結算一個單位的補給消耗。回要寫入熱狀態的 patch；**無事可做時回 None**。

    「無事可做」包含：沒有 `supply` 鍵（既有局）、所有類別都未編制、所有消耗率都是 0。
    這三種情況下**一個熱狀態鍵都不會被寫**，STATE_DIFF 也就不會有雜訊。
    """
    state = hot.get_unit(unit_id) or {}
    levels = read_levels(state)
    if not levels:
        return None

    raw_last = state.get(SUPPLY_TICK_KEY)
    last = int(raw_last) if isinstance(raw_last, (int, float)) else now_tick
    elapsed = max(0, now_tick - last)
    if elapsed <= 0:
        return None

    updated: dict[SupplyClass, SupplyLevel] = {}
    changed = False
    for supply_class, level in levels.items():
        rate = daily_consumption(supply_class, rates)
        after = consume(level, rate, elapsed, tick_rate_ms)
        updated[supply_class] = after
        if after.on_hand != level.on_hand:
            changed = True
    if not changed:
        # **連時間戳都不寫**：完全沒有消耗的局不該每 tick 推一次 STATE_DIFF。
        return None

    patch: dict[str, Any] = {
        SUPPLY_KEY: write_levels(updated),
        SUPPLY_TICK_KEY: now_tick,
    }
    days = elapsed * tick_rate_ms / _MS_PER_DAY
    patch[STARVED_DAYS_KEY] = _starved_days(state, updated, days)
    return patch


def _starved_days(
    state: dict[str, Any], levels: dict[SupplyClass, SupplyLevel], days: float
) -> float:
    """斷補天數：Class I 見底就累加，補到一次就歸零。

    只看 Class I——口糧斷了才是「斷補」；維修件（IX）見底影響的是修復，不是即刻戰力。
    """
    raw = state.get(STARVED_DAYS_KEY)
    current = float(raw) if isinstance(raw, (int, float)) else 0.0
    rations = levels.get(SupplyClass.I)
    if rations is None or not rations.declared:
        return 0.0
    return round(current + days, 4) if rations.on_hand <= 0.0 else 0.0


def supply_effectiveness(state: dict[str, Any]) -> float:
    """該單位因補給狀況而來的效能倍率。**沒有斷補概念的既有局恆為 1.0**。"""
    raw = state.get(STARVED_DAYS_KEY)
    return starvation_modifier(float(raw) if isinstance(raw, (int, float)) else 0.0)


def needs_resupply(state: dict[str, Any]) -> list[SupplyClass]:
    """低於再訂購水位的類別（WP-C7.2 的觸發線）。依類別名排序＝確定性。"""
    return sorted(
        (c for c, level in read_levels(state).items() if is_below_reorder(level)),
        key=lambda c: c.value,
    )


__all__ = [
    "STARVED_DAYS_KEY",
    "SUPPLY_KEY",
    "SUPPLY_TICK_KEY",
    "needs_resupply",
    "read_levels",
    "supply_effectiveness",
    "tick_supply",
    "write_levels",
]
