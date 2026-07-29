"""AI 敵情迷霧誠實化（WP-A1）——釘住「AI 與人受同一套 fog of war」。

改版前 `ai_loop` 用 `ground_truth_enemies`：AI 指揮官全知敵方存活單位位置，自主推演的
「偵察→情報→決策」閉環名存實亡。本測試釘住四件事：
1. 未被偵測的敵方單位**不出現**在 AI context；
2. fidelity 分級照 `IntelService` 閘門（DETECTED 不洩番號/型號/陣營）；
3. 盟軍走共享視圖（非偵測）而**看得見**——改版前盟軍對 AI 是完全隱形的；
4. `ai_ground_truth` 退回開關能把行為切回全知（供對照實驗），且預設不啟用。
"""

from __future__ import annotations

from _order_fakes import seed_world
from sqlalchemy.orm import Session, sessionmaker

from app.ai_loop.context import render_context_prompt
from app.ai_loop.world_view import allied_units, contacts_from_intel, recent_events
from app.factions.relations import FactionRelations, Relation
from app.intel import store
from app.intel.sweep import Contact
from app.models.enums import IntelFidelity, UnitLevel
from app.models.tables import TacticalUnit
from app.state.ledger import LedgerEvent, LedgerWriter

_HOSTILE = FactionRelations()  # 未宣告＝全 HOSTILE（既有局語義）


def _add_unit(db: Session, session_id: str, faction: str, designation: str) -> str:
    unit = TacticalUnit(
        session_id=session_id,
        designation=designation,
        unit_level=UnitLevel.PLATOON,
        faction=faction,
        current_lat=23.77,
        current_lng=121.27,
    )
    db.add(unit)
    db.flush()
    uid: str = unit.id
    db.commit()
    return uid


def _see(db: Session, sid: str, observer: str, target_id: str, fidelity: IntelFidelity) -> None:
    store.record(
        db,
        sid,
        Contact(
            observer_faction=observer,
            target_unit_id=target_id,
            fidelity=fidelity,
            tick=7,
            lat=23.76,
            lng=121.26,
            error_radius_m=250.0,
        ),
    )
    db.commit()


def test_undetected_enemy_is_absent_from_ai_context(
    session_factory: sessionmaker[Session],
) -> None:
    """BLUE 沒偵測到 RED → AI 的敵情清單是空的（改版前這裡會有 ground truth 的 RED）。"""
    world = seed_world(session_factory)
    with session_factory() as db:
        enemies = contacts_from_intel(db, world.session_id, "BLUE", _HOSTILE)
    assert enemies == []


def test_detected_enemy_appears_only_after_contact_recorded(
    session_factory: sessionmaker[Session],
) -> None:
    world = seed_world(session_factory)
    with session_factory() as db:
        _see(db, world.session_id, "BLUE", world.red_unit_id, IntelFidelity.DETECTED)
        enemies = contacts_from_intel(db, world.session_id, "BLUE", _HOSTILE)
    assert len(enemies) == 1
    # 位置與時間戳是 DETECTED 級就該有的（AI 要能判斷情報有多舊）。
    assert enemies[0]["last_seen_tick"] == 7
    assert enemies[0]["error_radius_m"] == 250.0
    # 橋接用的真實 unit id 必須在（否則 AI 接上迷霧後就再也打不了任何目標）。
    assert enemies[0]["unit_id"] == world.red_unit_id


def test_detected_fidelity_hides_identity_but_identified_reveals_it(
    session_factory: sessionmaker[Session],
) -> None:
    """fidelity 閘門：DETECTED 不給番號/型號/陣營；IDENTIFIED 才給。"""
    world = seed_world(session_factory)
    with session_factory() as db:
        _see(db, world.session_id, "BLUE", world.red_unit_id, IntelFidelity.DETECTED)
        detected = contacts_from_intel(db, world.session_id, "BLUE", _HOSTILE)[0]
    assert "designation" not in detected
    assert "unit_type" not in detected
    assert "faction" not in detected
    # prompt 也不得洩漏——render 出來的文字裡不能出現真實番號。
    prompt = render_context_prompt({"known_enemies": [detected], "faction": "BLUE"})
    assert "R1" not in prompt

    with session_factory() as db:  # 升級為 IDENTIFIED（upsert 同一 contact）
        _see(db, world.session_id, "BLUE", world.red_unit_id, IntelFidelity.IDENTIFIED)
        identified = contacts_from_intel(db, world.session_id, "BLUE", _HOSTILE)[0]
    assert identified["designation"] == "R1"
    assert identified["faction"] == "RED"
    assert identified["unit_type"] == UnitLevel.PLATOON.value


def test_contact_persists_after_target_moves_or_dies(
    session_factory: sessionmaker[Session],
) -> None:
    """contact 是**最後已知位置**：目標死了/走了仍留在敵情裡——AI 打空點是迷霧的本義。

    這條刻意釘住，防止日後有人「順手」用 ground truth 過濾掉死亡單位而把迷霧漏回來。
    """
    world = seed_world(session_factory)
    with session_factory() as db:
        _see(db, world.session_id, "BLUE", world.red_unit_id, IntelFidelity.DETECTED)
        red = db.get(TacticalUnit, world.red_unit_id)
        assert red is not None
        red.current_strength = 0  # 已殲滅
        red.current_lat, red.current_lng = 24.5, 122.0  # 且位置早已改變
        db.commit()
        enemies = contacts_from_intel(db, world.session_id, "BLUE", _HOSTILE)
    assert len(enemies) == 1
    assert enemies[0]["lat"] == 23.76  # 仍是最後觀測位置，不是真實新位置


def test_allies_are_visible_without_detection(session_factory: sessionmaker[Session]) -> None:
    """盟軍走共享視圖：沒有任何 contact 也看得到（改版前盟軍對 AI 完全隱形）。"""
    world = seed_world(session_factory)
    relations = FactionRelations([("BLUE", "YELLOW", Relation.ALLIED)])
    with session_factory() as db:
        ally_id = _add_unit(db, world.session_id, "YELLOW", "Y1")
        allies = allied_units(db, world.session_id, "BLUE", relations)
        # 敵對陣營不會混進盟軍桶。
        assert [a["unit_id"] for a in allies] == [ally_id]
        assert allies[0]["designation"] == "Y1"
        # 反向：YELLOW 眼中 BLUE 也是盟軍。
        assert [a["unit_id"] for a in allied_units(db, world.session_id, "YELLOW", relations)] == [
            world.blue_unit_id
        ]


def test_hostile_units_never_enter_the_ally_bucket(
    session_factory: sessionmaker[Session],
) -> None:
    world = seed_world(session_factory)
    with session_factory() as db:
        assert allied_units(db, world.session_id, "BLUE", _HOSTILE) == []


def test_recent_events_are_audience_filtered(session_factory: sessionmaker[Session]) -> None:
    """事件 feed 也受迷霧：他方之間的交戰不進本陣營 briefing，全域事件則人人可見。"""
    world = seed_world(session_factory)
    with session_factory() as db:
        third_id = _add_unit(db, world.session_id, "GREEN", "G1")
    writer = LedgerWriter(session_factory)
    writer.append(
        world.session_id,
        [
            LedgerEvent(  # BLUE 參與 → BLUE 看得到
                event_type="ENGAGEMENT_RESOLVED",
                tick=1,
                initiator_id=world.blue_unit_id,
                target_id=world.red_unit_id,
                ai_decision={"status": "HIT"},
                damage_calc=12.0,
            ),
            LedgerEvent(  # RED vs GREEN → BLUE 看不到
                event_type="ENGAGEMENT_RESOLVED",
                tick=2,
                initiator_id=world.red_unit_id,
                target_id=third_id,
                ai_decision={"status": "HIT"},
            ),
            LedgerEvent(event_type="SESSION_CONCLUDED", tick=3),  # 全域 → 人人可見
        ],
    )
    faction_of = {
        world.blue_unit_id: "BLUE",
        world.red_unit_id: "RED",
        third_id: "GREEN",
    }
    with session_factory() as db:
        events = recent_events(
            db,
            world.session_id,
            "BLUE",
            faction_for=lambda uid: faction_of.get(uid, ""),
        )
    kinds = [(e["tick"], e["event_type"]) for e in events]
    assert kinds == [(1, "ENGAGEMENT_RESOLVED"), (3, "SESSION_CONCLUDED")]
    assert events[0]["damage"] == 12.0


def test_sensor_contact_events_are_excluded_from_briefing(
    session_factory: sessionmaker[Session],
) -> None:
    """SENSOR_CONTACT 的 target_id 是被偵測單位的**真實 id**（永不下發）→ 不進 briefing。"""
    world = seed_world(session_factory)
    LedgerWriter(session_factory).append(
        world.session_id,
        [
            LedgerEvent(
                event_type="SENSOR_CONTACT",
                tick=4,
                target_id=world.red_unit_id,
                ai_decision={"observer_faction": "BLUE"},
            )
        ],
    )
    with session_factory() as db:
        events = recent_events(db, world.session_id, "BLUE", faction_for=lambda _uid: "BLUE")
    assert events == []
