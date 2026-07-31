"""`make_context_fn` 的真實接線測試（WP-B2）。

## 這一檔為什麼存在

WP-B2 標了 ✅、`test_msel_runtime.py` 有 20 條測試全綠——但**每一個有 MSEL 條目的局
在活執行期每 tick 崩潰、tick 恆停在 0**，一直到 V2.1 exit 的 CPX 驗收才被發現。

成因是 `session_msel.make_context_fn` 裡的一行 `dict(...tuples())`：
`dict()` 看到物件有 `.keys()` 就走 mapping 路徑、回頭 subscript 它，而 SQLAlchemy 的
`Result` 正好有 `.keys()` 卻不支援 subscript → `TypeError`。

**它能活下來，是因為所有既有測試都餵假的 context_fn**：
`MselRuntime([entry], lambda t: TriggerContext(tick=t))`——
真正會在生產跑的那個 `make_context_fn` 一次都沒被呼叫過。

這與本週活體測試抓到的每一個缺陷同一個病灶：
**測試餵給函式的資料，不是引擎真的會產生的資料。**

所以本檔的紀律是：**一律用真的 session_factory 與真的 HotStateStore**，
不接受任何替身。跑得比較慢是應該付的代價。
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.models import IntelContact, IntelFidelity, TacticalUnit, UnitLevel, WargameSession
from app.scenario.msel_runtime import MselRuntime
from app.scenario.session_msel import context_snapshot, load_session_msel, make_context_fn
from app.scenario.triggers import MselEntry
from app.state.hot_state import InMemoryHotState

_SID = "msel-ctx"


def _seed(session_factory: sessionmaker[Session]) -> tuple[str, str]:
    """一局兩個單位（藍/紅）+ 一筆藍軍偵獲紅軍的接觸。回 (blue_id, red_id)。"""
    with session_factory() as db:
        db.add(
            WargameSession(
                id=_SID,
                name="msel ctx",
                master_seed=7,
                current_weather={},
                msel=[
                    {
                        "id": "m1",
                        "trigger": {"type": "time", "at_tick": 0},
                        "inject": {"event_type": "INJECT"},
                    }
                ],
            )
        )
        db.flush()
        blue = TacticalUnit(
            session_id=_SID,
            designation="B1",
            unit_level=UnitLevel.PLATOON,
            faction="BLUE",
            current_lat=23.70,
            current_lng=120.30,
            authorized_strength=100.0,
            current_strength=100.0,
        )
        red = TacticalUnit(
            session_id=_SID,
            designation="R1",
            unit_level=UnitLevel.PLATOON,
            faction="RED",
            current_lat=23.71,
            current_lng=120.31,
            authorized_strength=100.0,
            current_strength=80.0,
        )
        db.add_all([blue, red])
        db.flush()
        db.add(
            IntelContact(
                session_id=_SID,
                faction="BLUE",
                target_unit_id=red.id,
                fidelity=IntelFidelity.IDENTIFIED,
                last_seen_tick=0,
                last_seen_lat=23.71,
                last_seen_lng=120.31,
                error_radius_m=50.0,
            )
        )
        db.commit()
        return blue.id, red.id


def _hot(blue_id: str, red_id: str) -> InMemoryHotState:
    hot = InMemoryHotState()
    hot.put_unit(blue_id, {"lat": 23.70, "lng": 120.30, "strength": 100.0, "faction": "BLUE"})
    hot.put_unit(red_id, {"lat": 23.75, "lng": 120.35, "strength": 80.0, "faction": "RED"})
    return hot


def test_real_context_fn_does_not_explode(session_factory: sessionmaker[Session]) -> None:
    """**這一條就是那個沒人寫的測試。**

    `make_context_fn` 回的閉包每 tick 都會被 Kernel 呼叫。它一炸，整局就停在 tick 0。
    """
    blue_id, red_id = _seed(session_factory)
    build = make_context_fn(session_factory, _SID, _hot(blue_id, red_id))

    ctx = build(0)  # ← 修正前這一行丟 TypeError

    assert ctx.tick == 0
    assert set(ctx.faction_strength) == {"BLUE", "RED"}, (
        f"兩軍都要有戰力，實得 {ctx.faction_strength}"
    )
    assert len(ctx.unit_positions) == 2


def test_context_positions_come_from_hot_state_not_db(
    session_factory: sessionmaker[Session],
) -> None:
    """脈絡的位置要取自熱狀態，不是 DB。

    模組說明講得很清楚：活模擬只寫熱狀態，DB 的 `current_lat/lng` 停在開局位置。
    用 DB 組脈絡的話，「紅軍推進到北岸」這種條件**永遠不會成立，而且不會有任何徵兆**。
    這裡把兩者刻意設成不同值，才驗得出取的是哪一個。
    """
    blue_id, red_id = _seed(session_factory)
    build = make_context_fn(session_factory, _SID, _hot(blue_id, red_id))

    ctx = build(5)
    red_pos = [p for p in ctx.unit_positions if p[0] == "RED"]
    assert red_pos, "紅軍應在脈絡裡"
    _, lat, lng = red_pos[0]
    assert (lat, lng) == pytest.approx((23.75, 120.35)), (
        f"應取熱狀態的 (23.75, 120.35)，實得 ({lat}, {lng})——取到 DB 的開局值就是這個 bug"
    )


def test_context_contacts_are_faction_pairs(session_factory: sessionmaker[Session]) -> None:
    """`contact_established` 條件要用的是 (觀測陣營, 被看到的陣營) 配對。"""
    blue_id, red_id = _seed(session_factory)
    build = make_context_fn(session_factory, _SID, _hot(blue_id, red_id))
    assert ("BLUE", "RED") in build(1).contacts


def test_runtime_survives_a_broken_context_fn() -> None:
    """脈絡組建炸掉時，**整局不可以停擺**——落一筆 MSEL_INJECT_FAILED 就好。

    這條防護原本只包住 applier，`_context_fn(tick)` 在保護之外，
    於是上面那個 TypeError 直接打穿 Kernel 的 trigger 槽、runner 每 3 秒被重建一次。
    註解當時已經寫著「一則注入壞掉不得讓整局停擺」，只是防護範圍畫錯了地方。
    """

    def boom(_tick: int):  # type: ignore[no-untyped-def]
        raise TypeError("'ChunkedIteratorResult' object is not subscriptable")

    entry = MselEntry(id="m1", trigger={"type": "time", "at_tick": 0}, inject={}, once=True)
    events = MselRuntime([entry], boom).check(3)

    assert [e.event_type for e in events] == ["MSEL_INJECT_FAILED"]
    assert events[0].ai_decision["reason"] == "TypeError"
    assert events[0].ai_decision["stage"] == "context", "要分得出是脈絡壞了還是注入壞了"


def test_end_to_end_msel_fires_with_the_real_context(
    session_factory: sessionmaker[Session],
) -> None:
    """真腳本 + 真脈絡 + 真執行器：條件成立時要真的觸發。

    前四條各守一個點，這一條把它們串起來——因為這個 bug 正是「每個零件都對，
    接起來沒人跑過」。
    """
    blue_id, red_id = _seed(session_factory)
    with session_factory() as db:
        entries = load_session_msel(db, _SID)
    assert entries, "想定的 MSEL 應該讀得出來"

    rt = MselRuntime(entries, make_context_fn(session_factory, _SID, _hot(blue_id, red_id)))
    events = rt.check(0)

    # 落帳的型別取自 inject 自己宣告的 `event_type`（見 `_inject_event`），
    # 不是一個固定的 MSEL_TRIGGERED——白軍在腳本裡寫什麼，帳本上就是什麼。
    assert [e.event_type for e in events] == ["INJECT"], (
        f"at_tick=0 的條件應在 tick 0 觸發，實得 {[e.event_type for e in events]}"
    )
    # 同一則 once 事件不會再觸發第二次。
    assert rt.check(1) == []


def test_context_snapshot_is_debuggable(session_factory: sessionmaker[Session]) -> None:
    blue_id, red_id = _seed(session_factory)
    snap = context_snapshot(make_context_fn(session_factory, _SID, _hot(blue_id, red_id))(2))
    assert snap == {"tick": 2, "factions": ["BLUE", "RED"], "units": 2, "contacts": 1}
