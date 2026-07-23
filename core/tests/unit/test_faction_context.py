"""O11.1 Faction COP context builder：分流、霧化不洩漏、序列化、渲染、狀態導出。"""

from __future__ import annotations

import json

import pytest

from app.ai_loop.context import (
    UnitMeta,
    build_faction_context,
    render_context_prompt,
    unit_status,
)
from app.factions.relations import FactionRelations, Relation


def _relations() -> FactionRelations:
    return FactionRelations([("BLUFOR", "OPFOR", Relation.HOSTILE)])


def _meta() -> dict[str, UnitMeta]:
    return {
        "b1": UnitMeta(faction="BLUFOR", designation="藍1", unit_type="INFANTRY_SQUAD"),
        "b2": UnitMeta(faction="BLUFOR", designation="藍2", unit_type="TANK_PLATOON"),
        "r1": UnitMeta(faction="OPFOR", designation="紅1", unit_type="ARMOR"),
    }


def _hot() -> dict[str, dict]:
    return {
        "b1": {
            "lat": 24.1,
            "lng": 121.1,
            "strength": 100.0,
            "health": 100.0,
            "ammo_by_weapon": {"w1": 30, "w2": 4},
        },
        "b2": {"lat": 24.2, "lng": 121.2, "strength": 40.0, "health": 40.0},
        "r1": {"lat": 24.3, "lng": 121.3, "strength": 90.0, "health": 90.0},
    }


def test_own_vs_enemy_split_by_faction() -> None:
    ctx = build_faction_context(
        faction="BLUFOR",
        tick=5,
        hot_snapshot=_hot(),
        unit_meta=_meta(),
        known_enemies=[],
        relations=_relations(),
    )
    own_ids = {u["unit_id"] for u in ctx["own_units"]}
    assert own_ids == {"b1", "b2"}  # r1（OPFOR）不入己方
    assert ctx["own_units"][0]["unit_id"] == "b1"  # 依 unit_id 排序（決定性）


def test_fog_enemies_come_only_from_injection() -> None:
    # known_enemies 空 → context 不得從 hot_snapshot 自行揭露敵方 r1（霧化紅線）。
    ctx = build_faction_context(
        faction="BLUFOR",
        tick=5,
        hot_snapshot=_hot(),
        unit_meta=_meta(),
        known_enemies=[],
        relations=_relations(),
    )
    assert ctx["known_enemies"] == []
    blob = json.dumps(ctx, ensure_ascii=False)
    assert "r1" not in blob  # 敵方單位 id 不得洩漏到 BLUFOR 的 context


def test_injected_enemies_passed_through() -> None:
    enemy = [{"contact_id": "c1", "lat": 24.3, "lng": 121.3, "fidelity": "DETECTED"}]
    ctx = build_faction_context(
        faction="BLUFOR",
        tick=5,
        hot_snapshot=_hot(),
        unit_meta=_meta(),
        known_enemies=enemy,
        relations=_relations(),
    )
    assert ctx["known_enemies"] == enemy


def test_relations_from_declared_factions() -> None:
    ctx = build_faction_context(
        faction="BLUFOR",
        tick=0,
        hot_snapshot=_hot(),
        unit_meta=_meta(),
        known_enemies=[],
        relations=_relations(),
    )
    assert ctx["relations"] == {"OPFOR": "HOSTILE"}


def test_own_unit_view_fields_and_h3() -> None:
    ctx = build_faction_context(
        faction="BLUFOR",
        tick=0,
        hot_snapshot=_hot(),
        unit_meta=_meta(),
        known_enemies=[],
        relations=_relations(),
    )
    b1 = next(u for u in ctx["own_units"] if u["unit_id"] == "b1")
    assert b1["designation"] == "藍1"
    assert b1["ammo_by_weapon"] == {"w1": 30, "w2": 4}
    assert "h3" in b1 and isinstance(b1["h3"], str)
    assert b1["status"] == "OPERATIONAL"


def test_context_is_json_serialisable() -> None:
    ctx = build_faction_context(
        faction="OPFOR",
        tick=9,
        hot_snapshot=_hot(),
        unit_meta=_meta(),
        known_enemies=[{"contact_id": "c9", "lat": 24.1, "lng": 121.1}],
        relations=_relations(),
        objectives=[{"type": "DESTROY_UNIT", "target": "b1"}],
        recent_events=["b1 交戰 r1"],
        mission="殲滅藍軍",
    )
    json.dumps(ctx, ensure_ascii=False)  # 不拋即通過


def test_render_prompt_contains_key_sections() -> None:
    ctx = build_faction_context(
        faction="BLUFOR",
        tick=7,
        hot_snapshot=_hot(),
        unit_meta=_meta(),
        known_enemies=[{"contact_id": "c1", "lat": 24.3, "lng": 121.3}],
        relations=_relations(),
        mission="奪取山脊",
    )
    text = render_context_prompt(ctx)
    assert "你指揮陣營：BLUFOR" in text
    assert "奪取山脊" in text
    assert "b1" in text and "c1" in text
    assert "未偵測者不在此" in text  # 明示霧化語義


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ({"strength": 0.0, "health": 0.0}, "DESTROYED"),
        ({"strength": 30.0, "health": 30.0}, "DEGRADED"),
        ({"strength": 80.0, "health": 80.0}, "OPERATIONAL"),
        ({}, "OPERATIONAL"),  # 無資料 → 保守 OPERATIONAL
    ],
)
def test_unit_status_derivation(state: dict, expected: str) -> None:
    assert unit_status(state) == expected
