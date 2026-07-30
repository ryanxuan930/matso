"""乘駐車與隊形（WP-C3）：中性預設、係數方向、接線、驗收條文。

[JCATS-A p.12,25] Mount 是操作要點（單兵未上車行軍速率過慢）；p.7,26 五種隊形影響受損與火力。
"""

from __future__ import annotations

import pytest

from app.adjudication.area_fire import AreaTarget, resolve_area_fire
from app.adjudication.formation import (
    DISMOUNTED_EXPOSURE,
    Formation,
    area_exposure_modifier,
    coeffs_of,
    crew_casualties,
    direct_fire_target_modifier,
    formation_of,
    march_speed_modifier,
    shooter_frontage_modifier,
)
from app.adjudication.weapon import WeaponProfile
from app.engine.formation_wiring import (
    FORMATION_KEY,
    MOUNTED_KEY,
    drain_formation_orders,
    read_formation,
    set_formation,
)
from app.engine.rng import DeterministicRNG
from app.state.hot_state import InMemoryHotState

# ---- 中性預設：既有局位元不變 ----


def test_missing_keys_read_as_column_and_undeclared() -> None:
    """**這條是本卡最重要的保護**：既有局的熱狀態沒有這兩個鍵。

    `mounted` 必須讀回 `None`（從未宣告）而**不是** `False`——見下一條測試。
    """
    formation, mounted = read_formation({})
    assert formation is Formation.COLUMN and mounted is None


def test_an_existing_session_gets_exactly_1_0_from_both_modifiers() -> None:
    """**第一版真的做錯了這件事**：`mounted` 缺鍵被 `bool()` 收成 False → 判定為「已下車」
    → 每個既有局的目標都吃到 0.8 的受彈面折減，全域命中率無聲下降 20%。

    golden 抓不到（沒有一個案例跑直射交戰），交戰單元測試也抓不到（它們直接建
    `EnvSnapshot`，用的是欄位預設 1.0）——錯在**接線**那一層，所以測試要打在接線上。
    """
    formation, mounted = read_formation({})
    assert direct_fire_target_modifier(formation, mounted) == 1.0
    assert shooter_frontage_modifier(formation, mounted) == 1.0


def test_declaring_mounted_true_is_not_the_same_as_undeclared_for_shooting() -> None:
    """宣告「我在車上」要吃車內射擊的折減；沒宣告則不吃。兩者不可混為一談。"""
    assert shooter_frontage_modifier(Formation.COLUMN, True) < shooter_frontage_modifier(
        Formation.COLUMN, None
    )


def test_column_is_the_neutral_formation() -> None:
    """COLUMN 三個係數全 1.0——它是「沒有特別展開」的預設，不是「最好的隊形」。

    把 LINE 當中性值會讓所有既有局憑空獲得火力加成。
    """
    c = coeffs_of(Formation.COLUMN)
    assert (c.march_speed_mult, c.exposure_mult, c.fire_frontage_mult) == (1.0, 1.0, 1.0)
    assert march_speed_modifier(Formation.COLUMN) == 1.0
    assert area_exposure_modifier(Formation.COLUMN) == 1.0


def test_unknown_formation_string_is_neutral_not_a_crash() -> None:
    for raw in ("PHALANX", "", None, 42):
        assert formation_of(raw) is Formation.COLUMN


# ---- 係數的方向 ----


def test_column_marches_fastest_and_suffers_most_from_shelling() -> None:
    """[JCATS-A p.7,26]：縱隊行軍快、擠在一條線上挨砲最慘。"""
    for other in (Formation.LINE, Formation.WEDGE, Formation.VEE, Formation.HERRINGBONE):
        assert march_speed_modifier(Formation.COLUMN) > march_speed_modifier(other)
        assert area_exposure_modifier(Formation.COLUMN) > area_exposure_modifier(other)


def test_line_gives_the_most_frontage_but_the_least_speed() -> None:
    line, wedge = coeffs_of(Formation.LINE), coeffs_of(Formation.WEDGE)
    assert line.fire_frontage_mult > wedge.fire_frontage_mult
    assert march_speed_modifier(Formation.LINE) < march_speed_modifier(Formation.WEDGE)


def test_herringbone_is_a_halt_formation() -> None:
    """魚骨是行軍**暫停**時的環形警戒——不是行軍隊形，故速度倍率最低。"""
    assert march_speed_modifier(Formation.HERRINGBONE) == min(
        march_speed_modifier(f) for f in Formation
    )


def test_dismounting_makes_you_harder_to_hit() -> None:
    """規格明列 dismounted target modifier × 0.8（相對於**明確宣告乘車**）。"""
    mounted = direct_fire_target_modifier(Formation.COLUMN, mounted=True)
    dismounted = direct_fire_target_modifier(Formation.COLUMN, mounted=False)
    assert dismounted == pytest.approx(mounted * DISMOUNTED_EXPOSURE)
    assert dismounted < mounted


def test_mounted_shooters_cannot_bring_full_firepower() -> None:
    """車內射擊受限——乘車時打不出全額火力。"""
    assert shooter_frontage_modifier(Formation.LINE, mounted=True) < shooter_frontage_modifier(
        Formation.LINE, mounted=False
    )


def test_formation_frontage_does_not_leak_into_target_exposure() -> None:
    """射手能發揚多少火力 ≠ 目標多好打。

    兩者放同一個數字，「我方展開成橫隊」會同時變成「敵人比較好打我」。
    """
    line_target = direct_fire_target_modifier(Formation.LINE, mounted=False)
    column_target = direct_fire_target_modifier(Formation.COLUMN, mounted=False)
    # LINE 散得開 → 面殺傷暴露較低；這與它的火力正面倍率（1.3 > 1.0）方向相反。
    assert line_target < column_target
    assert coeffs_of(Formation.LINE).fire_frontage_mult > 1.0


def test_crew_casualties_are_a_fraction_not_all_or_nothing() -> None:
    """[JTLS-F p.1058]：車被打掉時車上的人**不是全滅也不是沒事**。"""
    assert 0.0 < crew_casualties(10.0) < 10.0
    assert crew_casualties(0.0) == 0.0


# ---- 熱狀態接線 ----


def test_setting_only_mounted_leaves_the_formation_alone() -> None:
    """只想下車的令不該把隊形一起重設。"""
    hot = InMemoryHotState()
    hot.put_unit("u1", {})
    set_formation(hot, "u1", formation=Formation.WEDGE)
    set_formation(hot, "u1", mounted=True)
    assert read_formation(hot.get_unit("u1") or {}) == (Formation.WEDGE, True)


def test_setting_nothing_writes_nothing() -> None:
    hot = InMemoryHotState()
    hot.put_unit("u1", {})
    hot.drain_diff()
    set_formation(hot, "u1")
    assert hot.drain_diff() == {}


# ---- 驗收條文：縱隊遭砲擊傷亡 > 橫隊（同 seed 對照）----


def _howitzer() -> WeaponProfile:
    return WeaponProfile.from_base_stats(
        {
            "max_range_m": 20000,
            "ph_by_range_band": [[20000, 0.5]],
            "damage_by_armor_class": {"SOFT": 60.0},
            "pk_by_armor_class": {"SOFT": 0.6},
            "ammo_types": ["HE"],
            "dispersion_cep_m": 100.0,
            "lethal_radius_m": 50.0,
        }
    )


def _shell(formation: Formation) -> float:
    target = AreaTarget(
        unit_id="INF",
        faction="RED",
        lat=24.0,
        lng=121.0,
        armor_class="SOFT",
        current_strength=120.0,
        authorized_strength=120.0,
        platform_count=120,
        formation=formation.value,
    )
    result = resolve_area_fire(
        _howitzer(),
        (24.0, 121.0),
        [target],
        DeterministicRNG(master_seed=20260731, stream_id="area_fire"),
        tick=0,
        shooter_id="GUN",
        rounds=8,
    )
    return result.losses.get("INF", 0.0)


def test_column_takes_more_shelling_casualties_than_line() -> None:
    """驗收條文：COLUMN 遭砲擊傷亡 > LINE（**同 seed 對照**）。"""
    column, line = _shell(Formation.COLUMN), _shell(Formation.LINE)
    assert column > line
    # 倍率關係要與係數表對得上（0.7），不是隨便大一點。
    assert line == pytest.approx(column * area_exposure_modifier(Formation.LINE))


def test_default_formation_leaves_area_fire_unchanged() -> None:
    """沒宣告隊形的既有局，面射擊結果必須與加這張卡之前一模一樣。"""
    explicit = _shell(Formation.COLUMN)
    neutral = resolve_area_fire(
        _howitzer(),
        (24.0, 121.0),
        [
            AreaTarget(
                unit_id="INF",
                faction="RED",
                lat=24.0,
                lng=121.0,
                armor_class="SOFT",
                current_strength=120.0,
                authorized_strength=120.0,
                platform_count=120,
            )
        ],
        DeterministicRNG(master_seed=20260731, stream_id="area_fire"),
        tick=0,
        shooter_id="GUN",
        rounds=8,
    ).losses.get("INF", 0.0)
    assert neutral == explicit


# ---- FORMATION 令 ----


def test_formation_order_applies_and_completes(session_factory) -> None:  # type: ignore[no-untyped-def]
    from _order_fakes import FakeGateway, seed_world

    from app.orders.schemas import OrderRequest, OrderType
    from app.orders.service import OrderService

    world = seed_world(session_factory)
    db = session_factory()
    svc = OrderService(db, FakeGateway())
    resp = svc.submit(
        world.session_id,
        OrderRequest(
            unit_id=world.blue_unit_id,
            order_type=OrderType.FORMATION,
            payload={"formation": "WEDGE", "mounted": True},
        ),
        world.blue_issuer_id,
    )
    assert resp.status.value == "VALIDATED"

    hot = InMemoryHotState()
    hot.put_unit(world.blue_unit_id, {})
    assert drain_formation_orders(db, world.session_id, hot, tick=7) == 1
    assert read_formation(hot.get_unit(world.blue_unit_id) or {}) == (Formation.WEDGE, True)
    db.close()


def test_order_with_neither_field_is_rejected_at_submit(session_factory) -> None:  # type: ignore[no-untyped-def]
    """空令沒有任何意義——**在收令時就擋下**，不要等到 pre_tick 才靜靜作廢。"""
    from _order_fakes import seed_world
    from pydantic import ValidationError

    from app.orders.schemas import FormationPayload

    seed_world(session_factory)
    with pytest.raises(ValidationError):
        FormationPayload()


def test_unit_hot_state_keys_are_the_documented_ones() -> None:
    """熱狀態鍵名散在裁決/接線/廣播三處讀，寫錯一處就靜靜讀到預設值。"""
    assert (FORMATION_KEY, MOUNTED_KEY) == ("formation", "mounted")
