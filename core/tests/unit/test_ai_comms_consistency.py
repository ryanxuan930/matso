"""WP-C5 的第三項：**AI 指揮官與人類指揮官看同一張圖**。

`GET /units` 凍結了斷聯單位的位置、`GET /intel` 粗化了敵情，但 AI 若照舊直接吃熱狀態，
就等於「人受迷霧、AI 不受」——那正是 WP-A1 花整張卡消滅的不對稱，不能在這裡復活。
"""

from __future__ import annotations

from _order_fakes import seed_world
from sqlalchemy.orm import Session, sessionmaker

from app.ai_loop.context import UnitMeta, build_faction_context, render_context_prompt
from app.ai_loop.world_view import (
    allied_units,
    contacts_from_intel,
    faction_granularity,
    projected_snapshot,
)
from app.comms import IntelGranularity
from app.factions.relations import FactionRelations, Relation
from app.intel import store
from app.intel.sweep import Contact
from app.models.enums import IntelFidelity

_HOSTILE = FactionRelations()

_REPORT = {"report_lat": 23.500, "report_lng": 121.000, "report_tick": 42}
_TRUTH = {"lat": 24.900, "lng": 121.900}


def _meta(faction: str) -> UnitMeta:
    return UnitMeta(faction=faction, designation="B1", unit_type="PLATOON")


# --- 位置凍結 ---


def test_offline_unit_position_is_frozen_for_the_ai() -> None:
    projected = projected_snapshot(
        {"u1": {"comms_state": "OFFLINE", **_TRUTH, **_REPORT}},
    )
    assert (projected["u1"]["lat"], projected["u1"]["lng"]) == (23.500, 121.000)
    assert projected["u1"]["stale_since_tick"] == 42


def test_online_unit_keeps_truth() -> None:
    projected = projected_snapshot({"u1": {"comms_state": "ONLINE", **_TRUTH, **_REPORT}})
    assert (projected["u1"]["lat"], projected["u1"]["lng"]) == (24.900, 121.900)
    assert "stale_since_tick" not in projected["u1"]


def test_projection_never_mutates_the_source_snapshot() -> None:
    """熱狀態是 Kernel 的權威資料——投影只能產生副本，改到原件就等於改了物理事實。"""
    source = {"u1": {"comms_state": "OFFLINE", **_TRUTH, **_REPORT}}
    projected_snapshot(source)
    assert source["u1"]["lat"] == 24.900


def test_briefing_tells_the_llm_that_orders_cannot_be_delivered() -> None:
    """斷聯單位收不到新令（`order_admissible`）。不講的話 LLM 會對聽不見的部隊反覆下令。"""
    ctx = build_faction_context(
        faction="BLUE",
        tick=50,
        hot_snapshot=projected_snapshot({"u1": {"comms_state": "OFFLINE", **_TRUTH, **_REPORT}}),
        unit_meta={"u1": _meta("BLUE")},
        known_enemies=[],
        relations=_HOSTILE,
    )
    assert ctx["own_units"][0]["comms"] == "OFFLINE"
    prompt = render_context_prompt(ctx)
    assert "通聯 OFFLINE" in prompt
    assert "新令無法送達" in prompt
    assert "tick 42 的最後回報" in prompt
    assert "24.9" not in prompt  # 真實位置一個字都不能出現在 briefing 裡


def test_degraded_unit_is_flagged_as_delayed_not_undeliverable() -> None:
    ctx = build_faction_context(
        faction="BLUE",
        tick=50,
        hot_snapshot=projected_snapshot({"u1": {"comms_state": "DEGRADED", **_TRUTH, **_REPORT}}),
        unit_meta={"u1": _meta("BLUE")},
        known_enemies=[],
        relations=_HOSTILE,
    )
    assert "新令延遲送達" in render_context_prompt(ctx)


def test_allied_positions_also_come_from_the_projection(
    session_factory: sessionmaker[Session],
) -> None:
    """盟軍的位置一樣經該軍的回報鏈路而來；斷聯的盟軍在共享視圖上同樣凍結。"""
    world = seed_world(session_factory)
    allied = FactionRelations([("BLUE", "RED", Relation.ALLIED)])
    snapshot = projected_snapshot({world.red_unit_id: {"comms_state": "OFFLINE", **_TRUTH}})
    with session_factory() as db:
        views = allied_units(db, world.session_id, "BLUE", allied, snapshot)
    # 沒有任何回報 → 位置不明。**不得**退回 DB 的真實座標（那會讓凍結破功）。
    assert views and "lat" not in views[0]


# --- 敵情粗化 ---


def test_granularity_follows_own_faction_only() -> None:
    snapshot = {
        "b1": {"comms_state": "OFFLINE"},
        "b2": {"comms_state": "OFFLINE"},
        "r1": {"comms_state": "ONLINE"},
    }
    meta = {"b1": _meta("BLUE"), "b2": _meta("BLUE"), "r1": _meta("RED")}
    assert faction_granularity(snapshot, meta, "BLUE") is IntelGranularity.FROZEN
    assert faction_granularity(snapshot, meta, "RED") is IntelGranularity.FULL


def test_ai_contacts_are_coarsened_at_the_same_gate_as_the_rest_endpoint(
    session_factory: sessionmaker[Session],
) -> None:
    """AI 的敵情走 `IntelService.visible_contacts`——與 `GET /intel` 同一份粗化，不是另一套。"""
    world = seed_world(session_factory)
    with session_factory() as db:
        store.record(
            db,
            world.session_id,
            Contact(
                "BLUE", world.red_unit_id, IntelFidelity.IDENTIFIED, 9, 23.7601, 121.2601, 50.0
            ),
        )
        db.commit()
        sharp = contacts_from_intel(db, world.session_id, "BLUE", _HOSTILE)
        coarse = contacts_from_intel(
            db, world.session_id, "BLUE", _HOSTILE, IntelGranularity.COARSE
        )
    assert sharp[0]["fidelity"] == "IDENTIFIED" and "designation" in sharp[0]
    assert coarse[0]["fidelity"] == "DETECTED" and "designation" not in coarse[0]
    assert (coarse[0]["lat"], coarse[0]["lng"]) != (sharp[0]["lat"], sharp[0]["lng"])
    assert coarse[0]["error_radius_m"] > sharp[0]["error_radius_m"]
