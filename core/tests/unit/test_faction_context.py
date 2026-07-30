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
        "b1": UnitMeta(faction="BLUFOR", designation="藍1", echelon="INFANTRY_SQUAD"),
        "b2": UnitMeta(faction="BLUFOR", designation="藍2", echelon="TANK_PLATOON"),
        "r1": UnitMeta(faction="OPFOR", designation="紅1", echelon="ARMOR"),
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


def test_fixed_unit_marked_in_view_and_prompt() -> None:
    # 固定單位（指揮部）：己方視圖帶 fixed=True，渲染出【固定·勿調動】讓 LLM 勿派其機動。
    meta = _meta()
    meta["b1"] = UnitMeta(faction="BLUFOR", designation="旅部", echelon="HQ", is_fixed=True)
    ctx = build_faction_context(
        faction="BLUFOR",
        tick=5,
        hot_snapshot=_hot(),
        unit_meta=meta,
        known_enemies=[],
        relations=_relations(),
    )
    b1 = next(u for u in ctx["own_units"] if u["unit_id"] == "b1")
    b2 = next(u for u in ctx["own_units"] if u["unit_id"] == "b2")
    assert b1["fixed"] is True
    assert "fixed" not in b2  # 非固定單位不帶此鍵
    prompt = render_context_prompt(ctx)
    assert "固定·勿調動" in prompt


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


# ---- WP-C1 壓制與姿態進 AI context ----


def _ctx_with(state_patch: dict) -> dict:
    hot = _hot()
    hot["b1"].update(state_patch)
    return build_faction_context(
        faction="BLUFOR",
        tick=5,
        hot_snapshot=hot,
        unit_meta=_meta(),
        known_enemies=[],
        relations=_relations(),
    )


def test_neutral_suppression_and_posture_leave_the_prompt_bit_identical() -> None:
    """**這是本組最重要的一條**：既有局的熱狀態沒有這兩個鍵，prompt 必須一個位元都不變。

    `ReplayClient` 按 prompt 雜湊重播；prompt 一動，所有已錄的 golden 自主場次全部作廢。
    顯式寫 0/MOVING（中性值）也必須不變——否則「單位曾被壓制過又恢復」就會改變 prompt。
    """
    base = render_context_prompt(_ctx_with({}))
    assert render_context_prompt(_ctx_with({"suppression": 0.0, "posture": "MOVING"})) == base


def test_suppression_renders_its_consequence_not_just_the_number() -> None:
    """「0.5」對 LLM 沒有意義；「射擊效能剩約 70%」才推得出「先撤出被壓制區」。"""
    ctx = _ctx_with({"suppression": 0.5})
    assert ctx["own_units"][0]["suppression"] == 0.5
    line = next(ln for ln in render_context_prompt(ctx).splitlines() if ln.startswith("- b1"))
    assert "壓制 0.5" in line
    assert "70%" in line  # 1 - 0.6*0.5 = 0.7


def test_posture_renders_its_modifier_and_the_cost_of_moving() -> None:
    ctx = _ctx_with({"posture": "DUG_IN"})
    assert ctx["own_units"][0]["posture"] == "DUG_IN"
    line = next(ln for ln in render_context_prompt(ctx).splitlines() if ln.startswith("- b1"))
    assert "DUG_IN" in line and "×0.5" in line
    assert "重新構工" in line  # 移動會作廢——不講的話 LLM 會把挖好的單位隨手調走


def test_enemy_suppression_never_reaches_the_context() -> None:
    """敵方壓制度＝免費的即時戰果評估（WP-C10.4 擋的正是這個）。

    `known_enemies` 走情報路徑、`_own_unit_view` 只投影本陣營——這裡釘住「就算敵方熱狀態
    有壓制度，本陣營 context 也拿不到」。
    """
    hot = _hot()
    hot["r1"].update({"suppression": 0.9, "posture": "DUG_IN"})
    ctx = build_faction_context(
        faction="BLUFOR",
        tick=5,
        hot_snapshot=hot,
        unit_meta=_meta(),
        known_enemies=[{"unit_id": "r1", "lat": 24.3, "lng": 121.3}],
        relations=_relations(),
    )
    assert "0.9" not in json.dumps(ctx, ensure_ascii=False)
    assert "DUG_IN" not in render_context_prompt(ctx)


def test_objectives_reach_the_prompt_as_prose_not_python_repr() -> None:
    """白軍在主控台輸入的目標是 `{"description": "..."}`，而提示詞組裝原本是 `f"- {o}"`
    ——直接把 Python 的大括號與單引號送進 LLM：`- {'description': '奪取 218 高地'}`。

    模型讀得懂，但那是我們在教它讀 repr 而不是讀命令。
    ⚠ 這個缺陷是**自主推演主控台把 objectives 接上那一刻才第一次上線**的：
    在那之前前端寫死 `[]`，這一行永遠不執行。
    """
    from app.ai_loop.context import render_context_prompt

    text = render_context_prompt(
        {
            "faction": "BLUE",
            "tick": 1,
            "objectives": [
                {"description": "奪取並確保 218 高地"},
                {"text": "阻絕紅軍沿 3 號公路增援"},
                "純字串的目標也要照舊",
            ],
        }
    )

    assert "- 奪取並確保 218 高地" in text
    assert "- 阻絕紅軍沿 3 號公路增援" in text
    assert "- 純字串的目標也要照舊" in text
    # **這一條才是重點**：斷言「218 高地 in text」會被 dict repr 蒙混過去。
    assert "{" not in text.split("## 目標/勝負條件")[1].split("##")[0]
    assert "'description'" not in text
