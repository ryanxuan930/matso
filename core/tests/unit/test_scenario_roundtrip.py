"""想定編輯器 roundtrip（O7.3 / WP-B6）：載入 → 匯出 → 重新載入**無損**（SPEC §11.2）。

WP-B6 把這批測試從「部分欄位相等」升級為兩條更強的性質：

1. **無損（lossless）**：`load(pkg)` 與 `load(dump(load(pkg)))` **逐欄位**相等。
   舊版 `_snapshot` 只比 name/factions/colors/relations/units(4 欄)/msel/victory，
   所以 dump 掉 `fixed`、`description`、`display_name` 三個欄位時它照樣是綠的。
2. **冪等（idempotent）**：`dump(load(dump(x)))` 與 `dump(x)` **位元一致**（WP-B6 驗收條文）。
   注意這條**單獨無法**抓到欄位遺失——掉的欄位在兩次輸出都不存在，比對照樣通過。
   兩條一起才構成完整的驗收。
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from app.scenario import dump_scenario_package, load_scenario_package, scenario_to_dict
from app.scenario.loader import LoadedScenario

_EXAMPLES = Path(__file__).resolve().parents[3] / "scenarios" / "examples"
_TUTORIAL = _EXAMPLES / "tutorial-platoon"


def official_packages() -> list[Path]:
    """`scenarios/examples/` 下所有官方想定（有 scenario.yaml 的目錄）。

    刻意用掃描而非寫死清單：新增官方想定時**自動**納入 roundtrip 驗收，
    不會出現「加了想定但忘了加測試」。
    """
    return sorted(p for p in _EXAMPLES.iterdir() if (p / "scenario.yaml").is_file())


def _snapshot(sc: LoadedScenario) -> dict[str, object]:
    """LoadedScenario 的**全欄位**快照（relations/units 轉為可比較的確定性型別）。

    以 `dataclasses.fields` 列舉而非手寫欄位清單——新增欄位時這裡自動涵蓋，
    忘了在 dump 補對應輸出就會當場紅燈。
    """
    out: dict[str, object] = {}
    for f in dataclasses.fields(sc):
        if f.name == "raw":
            continue  # 原始 dict：dump 是重建而非原樣搬運，比它等於比 YAML 排版
        value = getattr(sc, f.name)
        if f.name == "relations":
            out[f.name] = value.declarations()
        elif f.name == "units":
            out[f.name] = sorted(dataclasses.astuple(u) for u in value)
        elif f.name == "msel":
            out[f.name] = sorted(dataclasses.astuple(e) for e in value)
        else:
            out[f.name] = value
    return out


def _file_bytes(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


@pytest.mark.parametrize("pkg", official_packages(), ids=lambda p: p.name)
def test_official_scenario_roundtrip_is_lossless(pkg: Path, tmp_path: Path) -> None:
    original = load_scenario_package(pkg)
    dump_scenario_package(original, tmp_path)
    reloaded = load_scenario_package(tmp_path)
    assert _snapshot(original) == _snapshot(reloaded)


@pytest.mark.parametrize("pkg", official_packages(), ids=lambda p: p.name)
def test_official_scenario_export_is_byte_identical(pkg: Path, tmp_path: Path) -> None:
    """WP-B6 驗收：export→import→export 位元一致（dump 必須是確定性的）。"""
    first, second = tmp_path / "a", tmp_path / "b"
    dump_scenario_package(load_scenario_package(pkg), first)
    dump_scenario_package(load_scenario_package(first), second)
    assert _file_bytes(first) == _file_bytes(second)


def test_export_produces_valid_package(tmp_path: Path) -> None:
    original = load_scenario_package(_TUTORIAL)
    dump_scenario_package(original, tmp_path)
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


# --- WP-B6：逐欄位的遺失回歸（每條對應一個實際掉過的欄位）---


def test_fixed_flag_survives_export(tmp_path: Path) -> None:
    """規格點名的 bug：匯出掉 `fixed` → 匯入後指揮部開始會移動（且無任何錯誤訊息）。"""
    sc = load_scenario_package(_TUTORIAL)
    target = sc.units[0]
    sc.units[0] = dataclasses.replace(target, fixed=True)
    dump_scenario_package(sc, tmp_path)
    reloaded = load_scenario_package(tmp_path)
    fixed_now = {u.designation for u in reloaded.units if u.fixed}
    assert fixed_now == {target.designation}


def test_description_and_display_name_survive_export(tmp_path: Path) -> None:
    sc = load_scenario_package(_TUTORIAL)
    sc.description = "測試用敘述"
    sc.faction_display_names["BLUE"] = "藍軍"
    dump_scenario_package(sc, tmp_path)
    reloaded = load_scenario_package(tmp_path)
    assert reloaded.description == "測試用敘述"
    assert reloaded.faction_display_names["BLUE"] == "藍軍"


def test_no_strike_zones_survive_export(tmp_path: Path) -> None:
    """禁射區遺失＝保護區被靜默拆除（WP-A3 的核心風險）。"""
    sc = load_scenario_package(_TUTORIAL)
    assert sc.no_strike_zones, "tutorial 想定應含示範禁射區"
    dump_scenario_package(sc, tmp_path)
    reloaded = load_scenario_package(tmp_path)
    assert reloaded.no_strike_zones == sc.no_strike_zones


def test_dump_omits_default_fixed_to_keep_diffs_clean() -> None:
    sc = load_scenario_package(_TUTORIAL)
    from app.scenario.dump import _orbat_dict

    units = _orbat_dict(sc, "BLUE")["units"]
    assert all("fixed" not in u for u in units)  # 全部非固定 → 不輸出雜訊


def test_scenario_dict_has_no_none_holes() -> None:
    """dump 出來的 dict 不得含 None 值——YAML 會寫成 `null`，再載入時 schema 會擋。"""
    out = scenario_to_dict(load_scenario_package(_TUTORIAL))
    assert None not in out.values()


# --- WP-B6：orbat 編裝（equipment）---


def test_equipment_survives_export(tmp_path: Path) -> None:
    sc = load_scenario_package(_TUTORIAL)
    sc.units[0] = dataclasses.replace(
        sc.units[0], equipment=(("MBT", 4, 40), ("RIFLE_556", 9, None))
    )
    dump_scenario_package(sc, tmp_path)
    reloaded = load_scenario_package(tmp_path)
    by_name = {u.designation: u for u in reloaded.units}
    assert by_name[sc.units[0].designation].equipment == (("MBT", 4, 40), ("RIFLE_556", 9, None))


def test_declared_equipment_is_materialised(session_factory) -> None:  # type: ignore[no-untyped-def]
    """想定宣告的編裝要真的變成 EquipmentInstance——不然裁決層看不到那些武器。"""
    from sqlalchemy import select

    from app.models import EquipmentInstance, EquipmentTemplate, TacticalUnit
    from app.scenario import create_session_from_scenario

    sc = load_scenario_package(_TUTORIAL)
    sc.units[0] = dataclasses.replace(sc.units[0], equipment=(("MBT", 3, 25),))
    with session_factory() as db:
        sid = create_session_from_scenario(db, sc, master_seed=1, seed_default_equipment=True)
        unit = db.scalar(
            select(TacticalUnit).where(
                TacticalUnit.session_id == sid,
                TacticalUnit.designation == sc.units[0].designation,
            )
        )
        rows = list(
            db.scalars(select(EquipmentInstance).where(EquipmentInstance.owner_id == unit.id))
        )
        names = {db.get(EquipmentTemplate, r.template_id).name for r in rows}
    # 明確編裝的單位只拿到想定給的東西，**不會**再被配發預設步槍
    assert names == {"MBT"}
    assert rows[0].quantity == 3 and rows[0].current_state["ammo"] == 25


def test_unknown_equipment_template_names_the_exact_path(session_factory) -> None:  # type: ignore[no-untyped-def]
    """SPEC_FULL §11.1 要求精確錯誤路徑；靜默略過會讓單位空手上場，交戰時才發現。"""
    from app.scenario import create_session_from_scenario
    from app.scenario.loader import ScenarioError

    sc = load_scenario_package(_TUTORIAL)
    sc.units[0] = dataclasses.replace(sc.units[0], equipment=(("T-999", 1, None),))
    with session_factory() as db, pytest.raises(ScenarioError, match="T-999"):
        create_session_from_scenario(db, sc, master_seed=1)


def test_units_without_declared_equipment_still_get_the_default(session_factory) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import select

    from app.models import EquipmentInstance, TacticalUnit
    from app.scenario import create_session_from_scenario

    sc = load_scenario_package(_TUTORIAL)
    with session_factory() as db:
        sid = create_session_from_scenario(db, sc, master_seed=1, seed_default_equipment=True)
        units = list(db.scalars(select(TacticalUnit).where(TacticalUnit.session_id == sid)))
        for unit in units:
            assert db.scalar(
                select(EquipmentInstance).where(EquipmentInstance.owner_id == unit.id)
            ), f"{unit.designation} 應有預設配發"


def test_session_level_settings_survive_export(tmp_path: Path) -> None:
    """**三個想定層設定過去全都掉在 `scenario_to_dict` 的手寫白名單外**（Backlog 清理）。

    `_snapshot` 是以 `dataclasses.fields` 列舉的，本來就會抓到欄位遺失——
    但**官方想定沒有一個宣告這三個鍵**，所以那條保護一直沒被觸發。
    症狀：把要求火協、有申請配額、開了陣地變換的想定匯出再匯入，
    三個設定全部安靜消失，演習規則整個變了樣而畫面上毫無徵兆。
    """
    sc = load_scenario_package(_TUTORIAL)
    sc.request_quotas = {"AIR_RECON": 3, "FIRE_SUPPORT": 5}
    sc.indirect_fire_requires_approval = True
    sc.survivability_move = {"enabled": True, "missions_before_move": 4, "min_km": 1.5}
    dump_scenario_package(sc, tmp_path)
    reloaded = load_scenario_package(tmp_path)
    assert reloaded.request_quotas == {"AIR_RECON": 3, "FIRE_SUPPORT": 5}
    assert reloaded.indirect_fire_requires_approval is True
    assert reloaded.survivability_move["missions_before_move"] == 4


def test_scenario_bundle_carries_every_key_the_loader_reads() -> None:
    """`POST /scenarios` 的請求模型，鍵集不可以比載入器讀的少。

    這一條抓的是一個真的靜默資料遺失：`ScenarioBundle` 只宣告
    scenario/orbat/msel，而 pydantic 預設丟掉未宣告欄位——於是磁碟上的想定有
    交戰規則（roe）與機動覆寫（overrides），走 HTTP 存進來的**兩者都消失**，
    沒有任何錯誤訊息。載入器一直讀得到它們，只是永遠拿到 None。

    做法是**掃載入器的原始碼**找它從 bundle 取哪些鍵，而不是手寫一份清單——
    手寫的清單自己也會漂掉（那正是這個 bug 的成因）。
    """
    import ast
    import inspect

    from app.api.scenarios import ScenarioBundle
    from app.scenario import loader

    src = inspect.getsource(loader.load_scenario_bundle)
    read_keys: set[str] = set()
    for node in ast.walk(ast.parse(src.strip())):
        # `bundle.get("x")` 與 `bundle["x"]` 兩種取法都要抓到。
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "bundle"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            read_keys.add(str(node.args[0].value))
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id == "bundle"
            and isinstance(node.slice, ast.Constant)
        ):
            read_keys.add(str(node.slice.value))

    assert read_keys, "掃不到任何 bundle 鍵——這條測試自己壞了，先修它"
    declared = set(ScenarioBundle.model_fields)
    missing = read_keys - declared
    assert not missing, (
        f"載入器會讀 {sorted(read_keys)}，但 ScenarioBundle 只宣告 {sorted(declared)}"
        f"——{sorted(missing)} 會被 pydantic 靜默丟掉"
    )
