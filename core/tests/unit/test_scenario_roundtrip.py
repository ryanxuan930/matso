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
