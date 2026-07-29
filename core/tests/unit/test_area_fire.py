"""面目標射擊裁決（WP-C10.2）——純函數，決定性。"""

from __future__ import annotations

from app.adjudication.area_fire import (
    AreaTarget,
    resolve_area_fire,
    sample_impact,
)
from app.adjudication.weapon import WeaponProfile
from app.engine.rng import DeterministicRNG

_AIM = (24.0, 121.0)


def _weapon(cep: float = 100.0, lethal: float = 50.0) -> WeaponProfile:
    return WeaponProfile.from_base_stats(
        {
            "max_range_m": 15000,
            "ph_by_range_band": [[15000, 0.5]],
            "damage_by_armor_class": {"SOFT": 60.0},
            "pk_by_armor_class": {"SOFT": 0.6},
            "ammo_types": ["HE"],
            "dispersion_cep_m": cep,
            "lethal_radius_m": lethal,
        }
    )


def _rng(stream: str = "s") -> DeterministicRNG:
    return DeterministicRNG(master_seed=42, stream_id=stream)


def _target(uid: str, lat: float, lng: float, faction: str = "RED") -> AreaTarget:
    return AreaTarget(
        unit_id=uid,
        faction=faction,
        lat=lat,
        lng=lng,
        armor_class="SOFT",
        authorized_strength=100.0,
        platform_count=1,
    )


def test_impact_is_deterministic() -> None:
    """**同一顆種子必得同一個落點**——決定性重播的前提（紅線 1）。"""
    a = sample_impact(*_AIM, 100.0, _rng())
    b = sample_impact(*_AIM, 100.0, _rng())
    assert a == b


def test_different_streams_give_different_impacts() -> None:
    a = sample_impact(*_AIM, 100.0, _rng("a"))
    b = sample_impact(*_AIM, 100.0, _rng("b"))
    assert a != b


def test_zero_cep_hits_the_aim_point() -> None:
    """未提供散布資料 → 退化成點命中，不崩潰也不亂猜。"""
    assert sample_impact(*_AIM, 0.0, _rng()) == _AIM


def test_unit_on_target_takes_loss() -> None:
    w = _weapon(cep=0.0)  # 無散布 → 落點即瞄準點
    out = resolve_area_fire(w, _AIM, [_target("R1", *_AIM)], _rng(), 5, shooter_id="B1")
    assert out.losses["R1"] > 0


def test_unit_outside_lethal_radius_untouched() -> None:
    w = _weapon(cep=0.0, lethal=50.0)
    far = (_AIM[0] + 0.01, _AIM[1])  # 約 1.1 km
    out = resolve_area_fire(w, _AIM, [_target("R1", *far)], _rng(), 5, shooter_id="B1")
    assert out.losses == {}


def test_closer_unit_takes_more_loss() -> None:
    """中心滿額、邊緣為零的單調遞減。"""
    w = _weapon(cep=0.0, lethal=200.0)
    near = _target("NEAR", _AIM[0] + 0.0002, _AIM[1])  # ~22m
    far = _target("FAR", _AIM[0] + 0.0015, _AIM[1])  # ~167m
    out = resolve_area_fire(w, _AIM, [near, far], _rng(), 5, shooter_id="B1")
    assert out.losses["NEAR"] > out.losses["FAR"] > 0


def test_friendly_units_are_also_hit() -> None:
    """**面射擊不分敵我**——砲彈不會挑人。

    這不是疏漏：火力協調之所以要有核准鏈與禁射區，正是因為這件事成立。
    """
    w = _weapon(cep=0.0)
    out = resolve_area_fire(
        w,
        _AIM,
        [_target("R1", *_AIM, faction="RED"), _target("B9", *_AIM, faction="BLUE")],
        _rng(),
        5,
        shooter_id="B1",
    )
    assert out.losses["B9"] > 0, "友軍站在落點上卻毫髮無傷"


def test_multiple_rounds_each_get_own_impact() -> None:
    """齊射每發各自抽落點——用一發乘 N 會讓散布消失（等於打得比實際準）。"""
    w = _weapon(cep=150.0)
    out = resolve_area_fire(w, _AIM, [_target("R1", *_AIM)], _rng(), 5, shooter_id="B1", rounds=4)
    impacts = out.event.ai_decision["impacts"]  # type: ignore[union-attr]
    assert len(impacts) == 4
    assert len({tuple(p) for p in impacts}) > 1, "四發落在同一點＝散布沒有生效"


def test_impacts_are_evidence_not_diagnostics() -> None:
    """落點放 `ai_decision`（入 hash chain），不放 `detail`——那裡刻意不入鏈，改得掉。"""
    w = _weapon(cep=100.0)
    out = resolve_area_fire(w, _AIM, [], _rng(), 5, shooter_id="B1", rounds=2)
    assert "impacts" in (out.event.ai_decision or {})  # type: ignore[union-attr]
    assert not (out.event.detail or {})  # type: ignore[union-attr]


def test_losses_are_capped_at_the_target_current_strength() -> None:
    """齊射累加很容易超過殘存戰力——不封頂的話帳本上的傷亡比實際被扣掉的還多。"""
    w = _weapon(cep=0.0)
    weak = AreaTarget(
        unit_id="R1",
        faction="RED",
        lat=_AIM[0],
        lng=_AIM[1],
        armor_class="SOFT",
        current_strength=5.0,
        authorized_strength=100.0,
        platform_count=1,
    )
    out = resolve_area_fire(w, _AIM, [weak], _rng(), 5, shooter_id="B1", rounds=20)
    assert out.losses["R1"] == 5.0


def test_friendly_losses_are_named_when_shooter_faction_is_known() -> None:
    """誤傷要能事後追究；射手自己不算在「誤傷友軍」裡。"""
    w = _weapon(cep=0.0)
    out = resolve_area_fire(
        w,
        _AIM,
        [_target("R1", *_AIM, faction="RED"), _target("B9", *_AIM, faction="BLUE")],
        _rng(),
        5,
        shooter_id="B1",
        shooter_faction="BLUE",
    )
    assert out.event.ai_decision["friendly_losses"] == ["B9"]  # type: ignore[union-attr]


def test_event_carries_aim_and_impact() -> None:
    """AAR 要能同時看到「打哪裡」與「落在哪」——兩者的差就是散布。"""
    w = _weapon(cep=120.0)
    out = resolve_area_fire(w, _AIM, [], _rng(), 7, shooter_id="B1")
    dec = out.event.ai_decision  # type: ignore[union-attr]
    assert (dec["aim_lat"], dec["aim_lng"]) == _AIM
    assert "impact_lat" in dec and "impact_lng" in dec


def test_no_lethal_radius_means_no_losses() -> None:
    """沒有殺傷半徑資料的武器不該憑空造成損失。"""
    w = _weapon(cep=0.0, lethal=0.0)
    out = resolve_area_fire(w, _AIM, [_target("R1", *_AIM)], _rng(), 5, shooter_id="B1")
    assert out.losses == {}
