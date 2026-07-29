"""聚合裁決（Lanchester）property tests（O3.5）——SPEC §7.1 末段。

驗收（TASKS O3.5）：能量守恆（雙方總戰損 ≤ 初始戰力）+ 同 seed 同結果。
另含：戰損隨敵方戰力遞增、強者勝、湮滅夾 0、閾值判定、事件內容。
"""

from __future__ import annotations

from dataclasses import replace

from hypothesis import given
from hypothesis import strategies as st

from app.adjudication.aggregate import (
    AggregateEnv,
    AggregateForce,
    resolve_aggregate_tick,
    resolve_multiway_tick,
    should_aggregate,
)
from app.engine.rng import DeterministicRNG
from app.factions import FactionRelations, Relation
from app.models.enums import UnitLevel


def _rng() -> DeterministicRNG:
    return DeterministicRNG(20260720, "adjudication")


def _blue(strength: float, lethality: float = 1.0) -> AggregateForce:
    return AggregateForce("b", "BLUE", strength, lethality)


def _red(strength: float, lethality: float = 1.0) -> AggregateForce:
    return AggregateForce("r", "RED", strength, lethality)


# ---------------- 能量守恆 ----------------


@given(
    bs=st.floats(min_value=1, max_value=1000),
    rs=st.floats(min_value=1, max_value=1000),
    bl=st.floats(min_value=0, max_value=5),
    rl=st.floats(min_value=0, max_value=5),
    variance=st.floats(min_value=0, max_value=1),
    aimed=st.floats(min_value=0, max_value=1),
    ticks=st.integers(min_value=1, max_value=20),
)
def test_energy_conservation(
    bs: float, rs: float, bl: float, rl: float, variance: float, aimed: float, ticks: int
) -> None:
    blue, red = _blue(bs, bl), _red(rs, rl)
    env = AggregateEnv(aimed_fraction=aimed, variance=variance)
    rng = _rng()
    for t in range(ticks):
        res = resolve_aggregate_tick(blue, red, env, rng, t)
        # 每 tick：戰力不負、單調不增（戰損 ≤ 當前戰力）
        assert 0.0 <= res.a_strength_after <= blue.strength + 1e-9
        assert 0.0 <= res.b_strength_after <= red.strength + 1e-9
        blue = replace(blue, strength=res.a_strength_after)
        red = replace(red, strength=res.b_strength_after)
    # 累計總戰損 ≤ 初始總戰力
    assert (bs - blue.strength) + (rs - red.strength) <= bs + rs + 1e-6


# ---------------- 決定性 ----------------


def test_deterministic_same_seed() -> None:
    env = AggregateEnv(variance=0.5)
    a = resolve_aggregate_tick(_blue(500), _red(400), env, _rng(), 0)
    b = resolve_aggregate_tick(_blue(500), _red(400), env, _rng(), 0)
    assert a.a_loss == b.a_loss
    assert a.b_loss == b.b_loss


def test_variance_zero_is_deterministic_without_seed_dependence() -> None:
    # variance=0 → 無隨機化：結果與 rng 抽到的值無關
    env = AggregateEnv(variance=0.0)
    a = resolve_aggregate_tick(_blue(500), _red(400), env, DeterministicRNG(1, "adjudication"), 0)
    b = resolve_aggregate_tick(_blue(500), _red(400), env, DeterministicRNG(999, "adjudication"), 0)
    assert a.a_loss == b.a_loss
    assert a.b_loss == b.b_loss


# ---------------- Lanchester 行為 ----------------


def test_loss_increases_with_enemy_strength() -> None:
    # aimed, variance=0：藍方戰損 = 紅方戰力 × 紅方 lethality（夾上限前）
    env = AggregateEnv(aimed_fraction=1.0, variance=0.0)
    weak = resolve_aggregate_tick(_blue(10_000), _red(100), env, _rng(), 0)
    strong = resolve_aggregate_tick(_blue(10_000), _red(300), env, _rng(), 0)
    assert strong.a_loss > weak.a_loss  # 敵方越強 → 我方戰損越大


def test_stronger_side_wins() -> None:
    env = AggregateEnv(aimed_fraction=1.0, variance=0.0)
    res = resolve_aggregate_tick(_blue(1000), _red(100), env, _rng(), 0)
    # 藍遠強於紅 → 紅一 tick 內覆滅、藍尚存
    assert res.b_strength_after == 0.0
    assert res.a_strength_after > 0.0


def test_annihilation_clamps_at_zero() -> None:
    env = AggregateEnv(aimed_fraction=1.0, variance=0.0)
    blue, red = _blue(1000, lethality=100), _red(1, lethality=0)
    rng = _rng()
    res = resolve_aggregate_tick(blue, red, env, rng, 0)
    assert res.b_strength_after == 0.0  # 覆滅
    # 再打一 tick 仍為 0（不轉負）
    red2 = replace(red, strength=res.b_strength_after)
    res2 = resolve_aggregate_tick(blue, red2, env, rng, 1)
    assert res2.b_strength_after == 0.0
    assert res2.b_loss == 0.0


# ---------------- 閾值 ----------------


def test_should_aggregate_threshold() -> None:
    # 預設 BATTALION：營級(含)以上聚合、以下個體
    assert should_aggregate(UnitLevel.BATTALION)
    assert should_aggregate(UnitLevel.BRIGADE)
    assert should_aggregate(UnitLevel.DIVISION)
    assert not should_aggregate(UnitLevel.COMPANY)
    assert not should_aggregate(UnitLevel.PLATOON)


def test_should_aggregate_custom_threshold() -> None:
    # 自訂閾值 BRIGADE：營級改走個體
    assert not should_aggregate(UnitLevel.BATTALION, UnitLevel.BRIGADE)
    assert should_aggregate(UnitLevel.BRIGADE, UnitLevel.BRIGADE)
    assert should_aggregate(UnitLevel.CORPS, UnitLevel.BRIGADE)


# ---------------- 事件 ----------------


def test_event_records_losses() -> None:
    res = resolve_aggregate_tick(_blue(500), _red(400), AggregateEnv(variance=0.0), _rng(), 7)
    assert len(res.events) == 1
    ev = res.events[0]
    assert ev.event_type == "AGGREGATE_ENGAGEMENT_RESOLVED"
    assert ev.tick == 7
    assert ev.initiator_id == "b" and ev.target_id == "r"
    assert ev.damage_calc == res.a_loss + res.b_loss
    assert ev.ai_decision["initiator_loss"] == res.a_loss


# ---- N 方混戰（O6.9，§12.1）----


def _force(uid: str, faction: str, strength: float, lethality: float = 1.0) -> AggregateForce:
    return AggregateForce(uid, faction, strength, lethality)


def test_multiway_only_hostile_pairs_fight() -> None:
    """三方：A-B 敵對、B-C 敵對、A-C 中立 → 只裁 2 組配對；中立方不互損。"""
    # lethality 低（0.1）→ 單 tick 戰損 < 戰力，避免全數覆滅遮蔽「合圍者掉血最多」效應。
    forces = [
        _force("A1", "ALPHA", 500, 0.1),
        _force("B1", "BRAVO", 500, 0.1),
        _force("C1", "CHARLIE", 500, 0.1),
    ]
    rel = FactionRelations([("ALPHA", "CHARLIE", Relation.NEUTRAL)])  # A-C 中立；其餘預設 HOSTILE
    res = resolve_multiway_tick(forces, rel, AggregateEnv(variance=0.0), _rng(), 0)
    assert len(res.events) == 2  # A-B, B-C（A-C 中立不裁）
    # B 同時對 A、C 作戰 → 承受兩方戰損，掉血最多
    a_lost = 500 - res.strength_after["A1"]
    b_lost = 500 - res.strength_after["B1"]
    c_lost = 500 - res.strength_after["C1"]
    assert b_lost > a_lost and b_lost > c_lost
    assert all(s >= 0.0 for s in res.strength_after.values())  # 能量守恆


def test_multiway_all_allied_no_combat() -> None:
    forces = [_force("A1", "ALPHA", 500), _force("B1", "BRAVO", 500)]
    rel = FactionRelations([("ALPHA", "BRAVO", Relation.ALLIED)])
    res = resolve_multiway_tick(forces, rel, AggregateEnv(), _rng(), 0)
    assert res.events == [] and res.strength_after == {"A1": 500.0, "B1": 500.0}


def test_multiway_deterministic() -> None:
    forces = [_force("A1", "ALPHA", 500), _force("B1", "BRAVO", 400), _force("C1", "CHARLIE", 300)]
    a = resolve_multiway_tick(forces, FactionRelations(), AggregateEnv(variance=0.3), _rng(), 0)
    b = resolve_multiway_tick(forces, FactionRelations(), AggregateEnv(variance=0.3), _rng(), 0)
    assert a.strength_after == b.strength_after


# ---------------- WP-C1 壓制與姿態 ----------------
#
# 營級以上單位掘壕與被壓制，效果不該因為換了裁決路徑（逐平台 → 聚合）就消失。
# 這幾條釘住聚合路徑的三件事：中性不變、壓制折減輸出、姿態折減承受。


# 低殺傷係數：戰損要遠小於戰力，否則雙方每 tick 都被夾到 0，比例關係全被夾掉看不出來。
def _small(uid: str, faction: str, **kw: object) -> AggregateForce:
    return replace(AggregateForce(uid, faction, 100.0, 0.05), **kw)  # type: ignore[arg-type]


def _agg(a: AggregateForce, b: AggregateForce):  # type: ignore[no-untyped-def]
    return resolve_aggregate_tick(a, b, AggregateEnv(), _rng(), tick=1)


def test_neutral_c1_values_leave_the_result_bit_identical() -> None:
    """既有局沒有這兩個欄位——結果必須連 coefficients 的鍵都一模一樣。

    無條件加四個 1.0 進 coefficients 會改掉每一則 AGGREGATE_ENGAGEMENT_RESOLVED 的序列化
    內容，連帶改掉 ledger 雜湊鏈：等於為了恆為 1 的欄位重錄所有 golden。
    """
    base = _agg(_small("b", "BLUE"), _small("r", "RED"))
    explicit = _agg(
        _small("b", "BLUE", suppression=0.0, posture="MOVING"),
        _small("r", "RED", suppression=0.0, posture="MOVING"),
    )
    assert (base.a_loss, base.b_loss) == (explicit.a_loss, explicit.b_loss)
    assert base.coefficients == explicit.coefficients
    assert "initiator_suppression" not in base.coefficients


def test_suppressed_attacker_inflicts_less() -> None:
    clean = _agg(_small("b", "BLUE"), _small("r", "RED"))
    suppressed = _agg(_small("b", "BLUE", suppression=1.0), _small("r", "RED"))
    # 滿壓制 → 射擊效能剩 40%（SUPPRESSION_FIRE_PENALTY=0.6）。承受端（a_loss）不受影響。
    assert suppressed.b_loss == clean.b_loss * 0.4
    assert suppressed.a_loss == clean.a_loss
    assert suppressed.coefficients["initiator_suppression"] == 0.4


def test_dug_in_defender_takes_less() -> None:
    clean = _agg(_small("b", "BLUE"), _small("r", "RED"))
    dug_in = _agg(_small("b", "BLUE"), _small("r", "RED", posture="DUG_IN"))
    assert dug_in.b_loss == clean.b_loss * 0.5
    assert dug_in.a_loss == clean.a_loss  # 掘壕不會讓它打得更差，只是更難被打中
    assert dug_in.coefficients["target_posture"] == 0.5


def test_unknown_posture_string_is_neutral_not_a_crash() -> None:
    """裁決層不因為資料髒就炸掉——未知姿態一律當 MOVING。"""
    weird = _agg(_small("b", "BLUE"), _small("r", "RED", posture="ENTRENCHED_LOL"))
    assert weird.b_loss == _agg(_small("b", "BLUE"), _small("r", "RED")).b_loss


def test_multiway_suppression_is_per_force_not_per_env() -> None:
    """多方混戰裡每支部隊被壓制的程度都不一樣——放進 env 會攤平成全場一個值。"""
    forces = [
        _small("b", "BLUE", suppression=1.0),
        _small("r", "RED"),
        _small("g", "GREEN"),
    ]
    rel = FactionRelations(
        [
            ("BLUE", "RED", Relation.HOSTILE),
            ("BLUE", "GREEN", Relation.HOSTILE),
            ("RED", "GREEN", Relation.HOSTILE),
        ]
    )
    result = resolve_multiway_tick(forces, rel, AggregateEnv(), _rng(), tick=1)
    clean = resolve_multiway_tick(
        [replace(f, suppression=0.0) for f in forces], rel, AggregateEnv(), _rng(), tick=1
    )
    # 被壓制的藍軍打得少 → 紅/綠活得多；藍軍自己承受的沒變（壓制不增加被命中率）。
    assert result.strength_after["r"] > clean.strength_after["r"]
    assert result.strength_after["g"] > clean.strength_after["g"]
    assert result.strength_after["b"] == clean.strength_after["b"]
