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

⚠ 但這套算法只有在**存量不被四捨五入**的前提下才成立（見 `write_levels`）。
存量若捨到小數第四位，1 秒/tick 的想定每 tick 只吃掉 1.2e-5 份，零頭捨掉就永遠不見底、
進位就多吃七成——而事件、畫面、單元測試全都看不出異常。

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
    dict 順序不穩就會讓同一個世界算出不同的雜湊。

    ⚠ **不四捨五入**（曾經是四位小數）。存量是**逐次結算累加出來的量**，把它捨到小數第四位
    等於每次結算都丟掉一個零頭：1 秒/tick 的想定每 tick 只吃掉 1.2e-5 份，零頭捨掉就
    永遠不見底、進位就多吃七成，而事件與畫面完全看不出異常。而且捨入誤差是**系統性**的
    （每次結算的增量固定，尾數方向也就固定），跑得愈久偏得愈多。
    熱狀態的其他數量欄位（`strength`、`ammo`）本來就都是直接寫浮點——這裡沒有理由特別。
    """
    return {c.value: [v.on_hand, v.capacity] for c, v in sorted(levels.items())}


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

    宣告了補給的單位第一次被結算時**只寫時鐘起點**（`supply_tick`），不扣任何存量
    ——沒有起點就算不出經過時間，而這個 patch 是那個鍵唯一的寫入端。
    """
    state = hot.get_unit(unit_id) or {}
    levels = read_levels(state)
    if not levels:
        return None

    raw_last = state.get(SUPPLY_TICK_KEY)
    if not isinstance(raw_last, (int, float)):
        # **第一次看到這個單位：只把時鐘起點寫下來，不扣任何存量。**
        # 這一行過去是 `last = now_tick` → `elapsed == 0` → return None。而 `supply_tick`
        # 的**唯一寫入端就是這個 patch**——於是它永遠不會被寫，`tick_supply` 對每個
        # 宣告了補給的單位都是死路：宣告了也永遠不吃飯。既有測試全綠，因為它們每一條
        # 都自己種了 `supply_tick`（測試繞過了真正缺的那一層）。
        return {SUPPLY_TICK_KEY: now_tick}
    elapsed = max(0, now_tick - int(raw_last))
    if elapsed <= 0:
        return None

    updated = {
        c: consume(lv, daily_consumption(c, rates), elapsed, tick_rate_ms)
        for c, lv in levels.items()
    }
    days = elapsed * tick_rate_ms / _MS_PER_DAY
    drained = any(updated[c].on_hand != lv.on_hand for c, lv in levels.items())
    if not drained and not _is_starving(updated):
        # 這一 tick 一格都沒扣到。**要分清楚是哪一種「沒扣到」**：
        #
        # (a) 編制中的類別**消耗率全是 0** → 這些格子永遠不會動，經過多少時間都一樣。
        #     連時間戳都不寫，完全沒有消耗的局不該每 tick 推一次 STATE_DIFF。
        # (b) 率不是 0，只是**存量已經見底** → 時鐘必須繼續走。
        #
        # (b) 過去也走 `return None`，而判斷「還在餓」的 `_is_starving` **只看 Class I**。
        # 於是沒有宣告口糧的單位（例如只帶維修件的支援單位）一旦 Class IX 見底，
        # `supply_tick` 就永遠凍在那一刻。補給點之後把它補滿，下一次結算拿凍住的起點
        # 算經過時間，把整場累積的時間債一次追討——實測 100000 tick（≈69 模擬日）的債
        # 讓剛補滿的 20 點料件在**一個 tick 內全部蒸發**，而且沒有任何事件說明發生了什麼。
        # 指揮官只會看到補給車白跑一趟。
        if all(daily_consumption(c, rates) <= 0 for c, lv in levels.items() if lv.declared):
            return None
        return {SUPPLY_TICK_KEY: now_tick}

    patch: dict[str, Any] = {SUPPLY_TICK_KEY: now_tick}
    if drained:
        patch[SUPPLY_KEY] = write_levels(updated)
    # **已經見底的單位即使這一 tick 扣不動任何東西，斷補天數仍要繼續累積**——
    # 否則懲罰會凍在剛見底的那一刻，「斷補愈久愈打不動」這個階梯就只走得到第一階。
    patch[STARVED_DAYS_KEY] = _starved_days(state, levels, updated, days, rates)
    return patch


def _is_starving(levels: dict[SupplyClass, SupplyLevel]) -> bool:
    """口糧編制了而且見底了。只看 Class I——理由同 `_starved_days`。"""
    rations = levels.get(SupplyClass.I)
    return rations is not None and rations.declared and rations.on_hand <= 0.0


def _starved_days(
    state: dict[str, Any],
    before: dict[SupplyClass, SupplyLevel],
    after: dict[SupplyClass, SupplyLevel],
    days: float,
    rates: dict[str, float] | None,
) -> float:
    """斷補天數：Class I 見底後**真正空著的那段時間**才累加，補到一次就歸零。

    只看 Class I——口糧斷了才是「斷補」；維修件（IX）見底影響的是修復，不是即刻戰力。

    ## 只算區間裡空著的部分，否則結果會隨結算頻率漂

    這裡曾經是「區間結束時是空的 → 把**整段** `days` 都算成斷補」，不問它是第幾分鐘見底的。
    於是同一個想定會因為 `tick_supply` 多久跑一次而得到不同的斷補曲線：
    滿載 3 DOS 被切斷，**逐 tick 結算**第 4 日才掉到 ×0.9（見底那一刻只累積了一個 tick 的量），
    **一天結算一次**卻在第 3 日就掉——因為見底的那一次結算把整整一天都記成餓著。

    生產跑的是逐 tick，所以畫面是對的；但任何粗粒度的重播、補算、或測試都會得到另一條曲線，
    而「同樣的輸入得到同樣的結果」是這個引擎最基本的承諾。改成用消耗率反推見底時刻：
    區間開始時有 `on_hand` 份、每日吃 `rate` 份 → 撐 `on_hand / rate` 天，
    剩下的 `days - on_hand / rate` 才是餓著的時間。
    """
    raw = state.get(STARVED_DAYS_KEY)
    current = float(raw) if isinstance(raw, (int, float)) else 0.0
    rations = after.get(SupplyClass.I)
    if rations is None or not rations.declared:
        return 0.0
    if rations.on_hand > 0.0:
        return 0.0
    rate = daily_consumption(SupplyClass.I, rates)
    had = before[SupplyClass.I].on_hand if SupplyClass.I in before else 0.0
    # 率是 0 而存量已經是 0 → 整段都餓著（它永遠不會自己補回來）。
    lasted = had / rate if rate > 0 else 0.0
    # **不四捨五入**（曾經是 4 位小數）。這是逐次結算累加出來的量，而 1 分鐘/tick 的局
    # 每 tick 只加 1/1440 ＝ 0.000694——進位到 0.0007 是每次多算 0.8%，
    # 於是階梯提早 11 個 tick 觸發，長時間斷補還會繼續累積偏差。
    # 同一個理由見 `refit_wiring._apply_repair` 對 `strength` 的處理。
    return current + max(0.0, days - lasted)


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


def auto_resupply(db: Any, hot: Any, session_id: str, unit_lookup: Any, tick: int) -> list[Any]:
    """低於再訂購水位的單位 → 從最近的己方補給點拉貨（WP-C7.2）。回帳本事件。

    **「拉」不是「推」**（見 `supply_points` 模組說明）。沒有任何單位低於水位、
    或本局根本沒有補給點時，**一次 DB 查詢之外什麼都不做**。

    `unit_lookup(unit_id) -> (faction, lat, lng) | None` 由呼叫端提供——
    本模組不查 `TacticalUnit`（那是呼叫端已經有的資料）。
    """
    from app.engine.supply_points import draw_from, load_points, nearest_usable, topped_up
    from app.state.ledger import LedgerEvent

    hungry = [
        (uid, classes)
        for uid in sorted(hot.get_all())
        if (classes := needs_resupply(hot.get_unit(uid) or {}))
    ]
    if not hungry:
        return []
    points = load_points(db, session_id)
    if not points:
        return []

    events: list[Any] = []
    for unit_id, classes in hungry:
        meta = unit_lookup(unit_id)
        if meta is None:
            continue
        faction, lat, lng = meta
        point = nearest_usable(points, faction, lat, lng)
        if point is None:
            continue  # 補給線斷了——**這正是打擊敵後勤要達到的效果**
        levels = read_levels(hot.get_unit(unit_id) or {})
        wanted = {
            c: max(0.0, levels[c].capacity - levels[c].on_hand) for c in classes if c in levels
        }
        issued = draw_from(db, point, wanted)
        if not issued:
            continue
        hot.update_unit(unit_id, {SUPPLY_KEY: write_levels(topped_up(levels, issued))})
        events.append(
            LedgerEvent(
                event_type="RESUPPLIED",
                tick=tick,
                initiator_id=unit_id,
                ai_decision={
                    "supply_point": point.feature_id,
                    "issued": {c.value: round(v, 3) for c, v in sorted(issued.items())},
                },
            )
        )
    if events:
        db.commit()
    return events


__all__ = [
    "STARVED_DAYS_KEY",
    "SUPPLY_KEY",
    "SUPPLY_TICK_KEY",
    "auto_resupply",
    "needs_resupply",
    "read_levels",
    "supply_effectiveness",
    "tick_supply",
    "write_levels",
]
