"""想定編輯器 roundtrip（O7.3）：載入 → 匯出 → 重新載入 等價（SPEC §11.2）。"""

from __future__ import annotations

from pathlib import Path

from app.scenario import dump_scenario_package, load_scenario_package

_TUTORIAL = Path(__file__).resolve().parents[3] / "scenarios" / "examples" / "tutorial-platoon"


def _snapshot(sc):  # type: ignore[no-untyped-def]
    return {
        "name": sc.name,
        "factions": sorted(sc.faction_ids),
        "colors": sc.faction_colors,
        "relations": sc.relations.declarations(),
        "units": sorted((u.faction, u.designation, u.unit_level, u.parent) for u in sc.units),
        "msel": sorted(e.id for e in sc.msel),
        "victory": sorted(vc["faction"] for vc in sc.victory_conditions),
    }


def test_edit_export_reload_roundtrip(tmp_path: Path) -> None:
    original = load_scenario_package(_TUTORIAL)
    dump_scenario_package(original, tmp_path)
    reloaded = load_scenario_package(tmp_path)
    assert _snapshot(original) == _snapshot(reloaded)


def test_export_produces_valid_package(tmp_path: Path) -> None:
    original = load_scenario_package(_TUTORIAL)
    dump_scenario_package(original, tmp_path)
    # 匯出的目錄結構完整、可再次載入不報錯
    assert (tmp_path / "scenario.yaml").exists()
    assert (tmp_path / "orbat" / "blue.yaml").exists()
    assert (tmp_path / "msel.yaml").exists()
    load_scenario_package(tmp_path)  # 不拋


def test_roundtrip_preserves_relations_after_edit(tmp_path: Path) -> None:
    from app.factions import Relation

    sc = load_scenario_package(_TUTORIAL)
    # 模擬編輯：新增第三陣營 + 中立關係
    sc.faction_ids.append("YELLOW")
    sc.relations.set_relation("BLUE", "YELLOW", Relation.NEUTRAL, tick=0)
    dump_scenario_package(sc, tmp_path)
    reloaded = load_scenario_package(tmp_path)
    assert reloaded.relations.is_neutral("BLUE", "YELLOW")
    assert reloaded.relations.is_hostile("BLUE", "RED")  # 原關係保留
