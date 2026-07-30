"""編制規模（`platform_count`）——**整個漸進消耗模型的分母**。

## 這一檔在防什麼

`cp_per_platform = authorized_strength / platform_count`。這個分母過去在生產路徑上
**恆為 1**：`platform_count` 全系統沒有任何寫入端（loader 不寫 `attributes`、
不寫 `personnel_current`；`orbat.schema.json` 是 `additionalProperties: false` 且
沒有這個欄位；前端也沒有）。於是 `cp_per_platform = 100/1 = 100`，
**一發步槍命中滿編步兵連扣掉 70 戰力，兩發全連覆滅**。

**而所有交戰測試都是綠的**——因為每一條都自己手塞 `platform_count`
（9/10/14/120），生產路徑的預設值一次都沒被走到。這一檔專門測**沒有人塞值**的那條路。
"""

from __future__ import annotations

from typing import ClassVar

from app.adjudication.establishment import (
    DEFAULT_PLATFORM_COUNT,
    PERSONNEL_BY_LEVEL,
    platform_count_for,
)
from app.models.enums import UnitLevel


def test_a_company_is_not_a_single_platform() -> None:
    """**這條就是那個 bug 的反面**：沒有人填任何欄位時，連不可以是「單體」。"""
    assert platform_count_for("COMPANY") > 1
    assert platform_count_for("COMPANY", attributes={}, personnel_current=None) > 1
    # 具體到會不會被一發打死：authorized 100 / platform_count → 每平台戰力
    cp = 100.0 / platform_count_for("COMPANY")
    assert 0.70 * cp < 5.0, f"單發步槍會扣 {0.70 * cp:.1f} 戰力——連隊撐不過幾發"


def test_explicit_values_win_over_the_derivation() -> None:
    """明示優先：想定作者寫了就照他寫的算。"""
    assert platform_count_for("COMPANY", attributes={"platform_count": 7}) == 7
    assert platform_count_for("COMPANY", personnel_current=88) == 88
    # attributes 比 personnel_current 更優先
    both = platform_count_for("COMPANY", attributes={"platform_count": 7}, personnel_current=88)
    assert both == 7
    # 非法值不採用，退回導出
    squad = PERSONNEL_BY_LEVEL["SQUAD"]
    assert platform_count_for("SQUAD", attributes={"platform_count": 0}) == squad
    assert platform_count_for("SQUAD", attributes="壞資料") == squad


def test_unknown_echelon_falls_back_to_platoon_not_one() -> None:
    """認不得的編制 → 排級。**不是 1**——1 代表「單體」，那正是要修掉的錯誤預設。"""
    assert platform_count_for("NOT_A_LEVEL") == DEFAULT_PLATFORM_COUNT
    assert platform_count_for(None) == DEFAULT_PLATFORM_COUNT
    assert DEFAULT_PLATFORM_COUNT > 1


def test_every_declared_echelon_has_a_size() -> None:
    """`UnitLevel` 加了新層級就要一起補人數，否則那一級會靜靜退回排級。"""
    missing = {level.value for level in UnitLevel} - set(PERSONNEL_BY_LEVEL)
    assert not missing, f"這些編制沒有人數：{sorted(missing)}"


def test_sizes_increase_with_echelon() -> None:
    """大編制的人一定比小編制多——這張表打錯順序會讓大部隊比小部隊還脆。"""
    sizes = [PERSONNEL_BY_LEVEL[level.value] for level in UnitLevel]
    assert sizes == sorted(sizes, reverse=True), f"人數未依編制遞減：{sizes}"


def test_a_scenario_loaded_unit_gets_a_real_platform_count() -> None:
    """**走生產路徑**：loader 建出來的單位（attributes={}、personnel_current=None）
    要拿到依編制導出的值，不是 1。

    這是唯一一條會踩到「沒有人填值」的測試——其餘交戰測試都自己塞值，
    所以這個 bug 才能活到現在。
    """
    from app.engine.engage_wiring import _platform_count_of

    class _Unit:
        unit_level = UnitLevel.COMPANY
        attributes: ClassVar[dict[str, object]] = {}
        personnel_current = None

    assert _platform_count_of(_Unit()) == PERSONNEL_BY_LEVEL["COMPANY"]  # type: ignore[arg-type]
