"""推演參數（#93 P1）：預設等同原常數、壞值退預設、預覽與執行同源。

最重要的不變式：**未設定時與硬編碼行為位元相同** —— 既有推演局、既有測試、
golden replay 都不能因為「加了可調參數」而改變結果。
"""

from __future__ import annotations

import pytest

from app.movement import params as mp
from app.sim_params import DEFAULTS, SimParams, parse_sim_params, to_config


def test_defaults_match_the_original_constants() -> None:
    """預設值必須等於原本寫死的常數——這條若鬆掉，等於偷偷改了所有既有局的物理。"""
    p = SimParams()

    assert p.foot_xc_kmh == mp.FOOT_XC_KMH
    assert p.foot_road_kmh == mp.FOOT_ROAD_KMH
    assert p.vehicle_fallback_kmh == mp.MOVE_SPEED_KMH
    assert p.march_attrition == mp.MARCH_ATTRITION_PER_KM
    assert p.attrition_for("FOOT") == mp.march_attrition_per_km("FOOT")
    assert p.attrition_for("TRACKED") == mp.march_attrition_per_km("TRACKED")


def test_unknown_profile_falls_back_like_before() -> None:
    assert SimParams().attrition_for("HOVERCRAFT") == mp.march_attrition_per_km("HOVERCRAFT")


@pytest.mark.parametrize("raw", [None, "nope", [], 42])
def test_missing_or_wrong_shaped_config_is_all_defaults(raw: object) -> None:
    assert parse_sim_params(raw) == SimParams()


def test_values_are_applied() -> None:
    p = parse_sim_params(
        {
            "foot_xc_kmh": 4.0,
            "foot_road_kmh": 7.5,
            "resupply_range_km": 5.0,
            "intrinsic_optical_range_m": 8000,
            "sensor_interval_ticks": 2,
            "march_attrition": {"FOOT": 0.09},
        }
    )

    assert p.foot_xc_kmh == 4.0
    assert p.foot_road_kmh == 7.5
    assert p.resupply_range_km == 5.0
    assert p.intrinsic_optical_range_m == 8000
    assert p.sensor_interval_ticks == 2
    assert p.attrition_for("FOOT") == 0.09
    # 未指定的 profile 保留預設（不是被清成 0）
    assert p.attrition_for("TRACKED") == mp.MARCH_ATTRITION_PER_KM["TRACKED"]


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("foot_xc_kmh", 0),  # 0 速度會讓單位永遠走不到
        ("foot_xc_kmh", -3),
        ("foot_xc_kmh", "fast"),
        ("resupply_range_km", -1),
        ("intrinsic_optical_range_m", None),
    ],
)
def test_bad_values_fall_back_per_field(field: str, bad: object) -> None:
    """壞值只影響該欄，其餘照常——一個打錯的數字不該讓整場推演跑不動。"""
    p = parse_sim_params({field: bad, "foot_road_kmh": 9.0})

    assert getattr(p, field) == getattr(DEFAULTS, field)
    assert p.foot_road_kmh == 9.0  # 同批的好值仍生效


def test_sensor_interval_is_at_least_one_tick() -> None:
    """0 或負的間隔會讓 `tick % interval` 爆掉或每 tick 全掃。"""
    assert parse_sim_params({"sensor_interval_ticks": 0}).sensor_interval_ticks == 1
    assert parse_sim_params({"sensor_interval_ticks": -5}).sensor_interval_ticks == 1


def test_round_trip_through_config() -> None:
    """存進 DB 再讀回來要一致（設定頁 PUT → GET 的往返）。"""
    p = parse_sim_params({"foot_xc_kmh": 6.25, "march_attrition": {"WHEELED": 0.04}})

    assert parse_sim_params(to_config(p)) == p


# ---- 漂移守門：dataclass / 解析器 / 序列化器三者必須同步 ----


def _nudged(p: SimParams) -> SimParams:
    """每一欄都改成與預設不同的值——roundtrip 才驗得出「有欄位沒接到」。"""
    from dataclasses import fields, replace

    changes: dict[str, object] = {}
    for f in fields(SimParams):
        value = getattr(p, f.name)
        if isinstance(value, dict):
            # 有既定鍵的表（`march_attrition` 是固定的 profile 集）改值；
            # 開放鍵空間的表（`supply_daily_rates`）加一個鍵。
            changes[f.name] = (
                {k: float(v) + 0.25 for k, v in value.items()} if value else {"TEST_CLASS": 3.5}
            )
        elif isinstance(value, bool):
            changes[f.name] = not value
        elif isinstance(value, int):
            changes[f.name] = int(value) + 7
        elif isinstance(value, float):
            changes[f.name] = float(value) + 0.25
        else:
            raise AssertionError(f"未知欄位型別，roundtrip 守門要補：{f.name}={value!r}")
    return replace(p, **changes)


def test_every_field_survives_a_config_roundtrip() -> None:
    """**這是本檔最重要的一條。**

    `SimParams` 曾有 11 個欄位在 dataclass 裡卻不在 `parse_sim_params` 裡：
    設定寫了也讀不到。後果不只是「調不動」——

    - `weather_refresh_ticks` 永遠是 0 → WP-C4b 的天氣逐 tick 刷新在生產環境開不起來
    - `supply_daily_rates` 永遠是空表 → WP-C7.1 的每日消耗同樣開不起來
    - 而 `to_config` 也一起漏了 → **參數凍結簽證（WP-B4）沒有雜湊到這些保真係數**

    逐欄列舉會再漂一次；改用「全欄都改過再 roundtrip」，新增欄位漏接就會紅。
    """
    tuned = _nudged(SimParams())

    assert parse_sim_params(to_config(tuned)) == tuned


def test_config_view_exposes_every_field() -> None:
    """`to_config` 是設定頁與封簽雜湊共用的投影——少一欄就是少簽一個係數。"""
    from dataclasses import fields

    exposed = set(to_config(SimParams()))

    assert exposed == {f.name for f in fields(SimParams)}


def test_weather_refresh_ticks_may_be_zero() -> None:
    """0 ＝「永不刷新」這個中性預設本身。**不可**跟其他間隔一樣夾到最小 1
    ——那會讓既有局忽然開始每 tick 問一次天氣服務。"""
    assert parse_sim_params({"weather_refresh_ticks": 0}).weather_refresh_ticks == 0
    assert parse_sim_params({"weather_refresh_ticks": 30}).weather_refresh_ticks == 30


def test_a_broken_supply_rate_drops_only_that_class() -> None:
    """壞值不該讓整份消耗率表變成空（那等於全軍忽然不用吃飯）。"""
    rates = parse_sim_params(
        {"supply_daily_rates": {"FOOD": 1.5, "AMMO": "很多", "FUEL": -1}}
    ).supply_daily_rates

    assert rates == {"FOOD": 1.5}


# ---- 該局的 tick 長度：想定宣告 > 系統設定 ----


def test_scenario_tick_rate_beats_the_system_setting(session_factory) -> None:  # type: ignore[no-untyped-def]
    """想定的 `tick_rate_ms` **過去只進得了匯出檔**。

    schema 把它列為必填、loader 讀得進 `LoadedScenario`，但唯一的消費者是 `dump.py`
    ——沒有任何一條路把它帶進執行期，於是每一局都跑系統設定那個值。
    roundtrip 測試一直是綠的，因為 loader→dump 對得上。
    """
    from app.models.tables import WargameSession
    from app.sim_params import session_tick_rate_ms

    params = SimParams(tick_rate_ms=60_000)
    with session_factory() as db:
        db.add(
            WargameSession(
                id="s-declared", name="x", master_seed=1, current_weather={}, tick_rate_ms=30_000
            )
        )
        db.add(WargameSession(id="s-silent", name="y", master_seed=1, current_weather={}))
        db.commit()

        assert session_tick_rate_ms(db, "s-declared", params) == 30_000
        # 未宣告 → 系統設定（既有局零變更）。
        assert session_tick_rate_ms(db, "s-silent", params) == 60_000
        # 查無此局 → 系統設定，不炸。
        assert session_tick_rate_ms(db, "nope", params) == 60_000
