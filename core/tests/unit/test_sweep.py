"""偵測掃描（O3.3）：H3 k-ring 預過濾正確性（vs 暴力全配對）+ 決定性 + 敵我/LOS。"""

from __future__ import annotations

from app.engine.rng import DeterministicRNG
from app.intel.seed_sensors import SEED_SENSORS
from app.intel.sensor import DetectionEnv, SensorProfile
from app.intel.sweep import (
    Contact,
    SensorUnit,
    TargetUnit,
    _haversine_m,
    sweep,
)
from app.models.enums import Faction

_RADAR = SensorProfile.from_base_stats(SEED_SENSORS["GROUND_RADAR"])  # 8km


def _rng() -> DeterministicRNG:
    return DeterministicRNG(20260720, "sensors")


def _env_clear(_o: SensorUnit, _t: TargetUnit) -> DetectionEnv:
    return DetectionEnv(los_clear=True)


def _brute_sweep(
    observers: list[SensorUnit],
    candidates: list[TargetUnit],
    rng: DeterministicRNG,
    tick: int = 0,
) -> list[Contact]:
    """參考實作：暴力全配對（同排序 → 同擲骰序列），供交叉驗證 k-ring 不漏不多。"""
    from app.intel.sensor import ERROR_RADIUS_M, detect_probability, fidelity_for

    out: list[Contact] = []
    for o in sorted(observers, key=lambda x: x.unit_id):
        for t in sorted(candidates, key=lambda x: x.unit_id):
            if t.faction == o.faction:
                continue
            r = _haversine_m(o.lat, o.lng, t.lat, t.lng)
            if r > o.sensor.max_range_m:
                continue
            p = detect_probability(o.sensor, r, _env_clear(o, t))
            roll = rng.random()
            if roll < p:
                fid = fidelity_for(p)
                out.append(
                    Contact(o.faction, t.unit_id, fid, tick, t.lat, t.lng, ERROR_RADIUS_M[fid])
                )
    return out


def _grid_targets() -> list[TargetUnit]:
    # 以 (23.75,121.25) 為中心，佈署一圈近距 RED 目標 + 幾個遠距（>8km）目標
    targets: list[TargetUnit] = []
    for i in range(12):
        # 每個約 +0.01° lat（~1.1km），前 8 個在射程內、後面漸出
        targets.append(
            TargetUnit(unit_id=f"r{i:02d}", faction=Faction.RED, lat=23.75 + i * 0.01, lng=121.25)
        )
    # 遠在 55km 外（絕對出 k-ring 與射程）
    targets.append(TargetUnit(unit_id="far", faction=Faction.RED, lat=24.25, lng=121.25))
    return targets


def _observer() -> SensorUnit:
    return SensorUnit(unit_id="s0", faction=Faction.BLUE, lat=23.75, lng=121.25, sensor=_RADAR)


def test_kring_matches_bruteforce() -> None:
    obs = [_observer()]
    cands = _grid_targets()
    got = sweep(obs, cands, _env_clear, _rng(), tick=0)
    ref = _brute_sweep(obs, cands, _rng(), tick=0)
    assert got == ref  # k-ring 不漏、不多、擲骰序列一致


def test_far_target_never_detected() -> None:
    got = sweep([_observer()], _grid_targets(), _env_clear, _rng(), tick=0)
    assert all(c.target_unit_id != "far" for c in got)  # 55km 外必不在結果


def test_deterministic_same_seed() -> None:
    obs, cands = [_observer()], _grid_targets()
    a = sweep(obs, cands, _env_clear, _rng(), tick=0)
    b = sweep(obs, cands, _env_clear, _rng(), tick=0)
    assert a == b


def test_only_enemies_detected() -> None:
    # 加入一個己方（BLUE）近距單位 → 不得成為 contact
    friend = TargetUnit(unit_id="b_friend", faction=Faction.BLUE, lat=23.751, lng=121.25)
    got = sweep([_observer()], [friend, *_grid_targets()], _env_clear, _rng(), tick=0)
    assert all(c.target_unit_id != "b_friend" for c in got)


def test_no_los_blinds_radar() -> None:
    def blocked(_o: SensorUnit, _t: TargetUnit) -> DetectionEnv:
        return DetectionEnv(los_clear=False)

    got = sweep([_observer()], _grid_targets(), blocked, _rng(), tick=0)
    assert got == []  # RADAR 需 LOS，全遭阻擋 → 無 contact
