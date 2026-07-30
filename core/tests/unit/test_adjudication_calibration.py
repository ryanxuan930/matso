"""聚合律與壓制衰減的**校準耦合**守門（低優先清理批次 L1/L2）。

這批不改行為，改的是「為什麼是這個數字」的可查證性。兩條被釘住的事：

1. `AggregateEnv.aimed_fraction` 恆為 1.0 → linear-law 項從未被行使 → 沒有人發現
   `_AREA_SCALE = 100.0`（自標「v0 佔位」）其實決定了整條 linear 項的量級。
2. `SUPPRESSION_DECAY = 0.7` 與契約敘述的 0.85 不一致，而那個差別是「13 分鐘」對「29 分鐘」。
"""

from __future__ import annotations

from _order_fakes import FakeGateway, seed_world
from sqlalchemy.orm import Session, sessionmaker

from app.adjudication.adjudicator import EngagementAdjudicator, EngageOrderSource
from app.adjudication.aggregate import (
    _AREA_SCALE,
    AggregateEnv,
    AggregateForce,
    resolve_aggregate_tick,
)
from app.adjudication.engagement import EnvSnapshot
from app.adjudication.suppression import MAX_SUPPRESSION, SUPPRESSION_DECAY, decay_suppression
from app.adjudication.weapon import WeaponProfile
from app.engine.clock import SimTime
from app.engine.rng import DeterministicRNG
from app.models.enums import UnitLevel
from app.models.tables import TacticalUnit
from app.orders.schemas import OrderRequest, OrderType
from app.orders.service import OrderService
from app.state.hot_state import InMemoryHotState

_WEAPON = WeaponProfile.from_base_stats(
    {
        "max_range_m": 5000,
        "ph_by_range_band": [[100, 1.0], [5000, 1.0]],
        "damage_by_armor_class": {"INFANTRY": 40},
        "ammo_types": ["X"],
    }
)


def _rng() -> DeterministicRNG:
    return DeterministicRNG(20260730, "adjudication")


def _loss_on_defender(attacker_strength: float, defender_strength: float, aimed: float) -> float:
    """守方本 tick 承受的戰損（variance=0 → 確定性，與 RNG 無關）。"""
    attacker = AggregateForce("a", "BLUE", attacker_strength, lethality=0.01)
    defender = AggregateForce("d", "RED", defender_strength, lethality=0.0)
    env = AggregateEnv(aimed_fraction=aimed, variance=0.0)
    return resolve_aggregate_tick(attacker, defender, env, _rng(), 0).b_loss


# ── L1：linear-law 的量級由一個沒有來源的常數決定 ──────────────────────────


def test_area_fire_losses_scale_with_defender_strength_over_area_scale() -> None:
    """抓的病：`aimed_fraction` 從未被傳過非 1.0 的值，於是 linear 項一次都沒被行使過，
    也就沒有人發現 `_AREA_SCALE = 100.0` 的實質意義是「linear 與 square 等值時的守方戰力」。

    照現值把 aimed_fraction 接成 0（間瞄＝面射擊），倍率就是「守方戰力 ÷ 100」：
    守方 500 的營變 5 倍、守方 30 的連反而剩 0.3 倍——**方向隨目標大小翻轉**。
    這條把那個耦合釘成可讀的數字；動 `_AREA_SCALE` 而沒重新想過語義時會轉紅。
    """
    for defender in (500.0, 100.0, 30.0):
        square = _loss_on_defender(100.0, defender, aimed=1.0)
        area = _loss_on_defender(100.0, defender, aimed=0.0)
        assert area == square * (defender / _AREA_SCALE)

    # 具體到「接上去會發生什麼」：營級目標 5 倍、連級目標 0.3 倍。
    assert _loss_on_defender(100.0, 500.0, 0.0) == 5.0 * _loss_on_defender(100.0, 500.0, 1.0)
    assert _loss_on_defender(100.0, 30.0, 0.0) == 0.3 * _loss_on_defender(100.0, 30.0, 1.0)


async def test_live_aggregate_engagement_is_pure_square_law(
    session_factory: sessionmaker[Session],
) -> None:
    """抓的病：`adjudicator._resolve_aggregate` 建 `AggregateEnv` 時不傳 `aimed_fraction`。

    本輪查證後裁定**刻意不接**（見該處註解：`_AREA_SCALE` 未校準，硬接等於讓佔位常數
    開始產生戰損）。這條把那個決定釘成事件裡看得到的事實：哪天有人把它接上去，
    這裡轉紅，逼他一併處理 `aggregate.py` 的校準與 adjudicator 的說明，
    而不是靜悄悄地讓所有既有想定的營級交戰換一套數字。
    """
    world = seed_world(session_factory)
    with session_factory() as db:
        blue = db.get(TacticalUnit, world.blue_unit_id)
        assert blue is not None
        blue.unit_level = UnitLevel.BATTALION
        db.commit()
        OrderService(db, FakeGateway(visible=True)).submit(
            world.session_id,
            OrderRequest(
                unit_id=world.blue_unit_id,
                order_type=OrderType.ENGAGE,
                payload={"target_unit_id": world.red_unit_id},
            ),
            world.blue_issuer_id,
        )
        (cmd,) = await EngageOrderSource(db, world.session_id).drain()
        hot = InMemoryHotState()
        hot.put_unit(
            world.blue_unit_id, {"ammo": 999, "strength": 500.0, "authorized_strength": 500.0}
        )
        hot.put_unit(
            world.red_unit_id,
            {"strength": 400.0, "authorized_strength": 400.0, "armor_class": "INFANTRY"},
        )
        events = EngagementAdjudicator(
            db,
            hot,
            _rng(),
            lambda _cmd: _WEAPON,
            lambda _s, _t, _indirect=False: EnvSnapshot(range_m=500.0, los_clear=True),
        ).resolve(cmd, SimTime(0, 0))

    (agg,) = [e for e in events if e.event_type == "AGGREGATE_ENGAGEMENT_RESOLVED"]
    assert agg.ai_decision["coefficients"]["aimed_fraction"] == 1.0


# ── L2：壓制衰減率 0.7 vs 契約敘述的 0.85 ─────────────────────────────────


def _ticks_to_clear(decay: float) -> int:
    """滿壓制自然衰減到歸零所需的 tick 數（1 tick = 1 分鐘）。"""
    level, ticks = MAX_SUPPRESSION, 0
    while level > 0.0:
        level = decay_suppression(level, decay)
        ticks += 1
    return ticks


def test_full_suppression_clears_in_a_quarter_hour_not_half_an_hour() -> None:
    """抓的病：`contracts/core_api.yaml` 的 `suppression_decay` 說明寫「預設 0.85」，
    程式是 0.7——兩份文件對同一個係數給了不同的數字，讀契約寫 client 的人會算錯。

    差別不是小數點：0.7 ＝停火後 13 分鐘壓制清乾淨，0.85 ＝29 分鐘。後者長得像戰損，
    而「壓制可逆、戰損不可逆」正是砲兵戰術功能的全部意義。這條把 0.7 的理由
    （抬頭、重新據槍是分鐘級的事）變成可驗證的斷言，讓那個數字不再只是被挑出來的常數。
    """
    assert SUPPRESSION_DECAY == 0.7
    assert _ticks_to_clear(SUPPRESSION_DECAY) == 13
    assert _ticks_to_clear(0.85) == 29
