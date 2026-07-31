"""AAR：重播/統計/敘事/匯出（O8.1–O8.4，SPEC §14）——純函數。"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from app.aar.events import AarEvent
from app.aar.export import export_csv, export_json
from app.aar.narrative import generate_narrative, verify_citations
from app.aar.replay import (
    bookmarks,
    build_timeline,
    reconstruct_states,
    replay_summary,
    state_frames,
)
from app.aar.stats import STATS_VERSION, compute_metrics
from app.adjudication.aggregate import AggregateEnv, AggregateForce, resolve_aggregate_tick
from app.adjudication.area_fire import AreaTarget, resolve_area_fire
from app.adjudication.combined import CombinedWeapon, resolve_combined_engagement
from app.adjudication.effectiveness import effectiveness_pct
from app.adjudication.engagement import (
    EnvSnapshot,
    Resolution,
    Shooter,
    Target,
    resolve_engagement,
)
from app.adjudication.weapon import WeaponProfile
from app.engine.rng import DeterministicRNG
from app.state.ledger import LedgerEvent


def _ev(seq, tick, etype, **kw):  # type: ignore[no-untyped-def]
    return AarEvent(
        seq=seq,
        tick=tick,
        event_type=etype,
        initiator_id=kw.get("initiator"),
        target_id=kw.get("target"),
        ai_decision=kw.get("dec", {}),
        damage_calc=kw.get("dmg"),
        reasoning_chain=kw.get("cot"),
        detail=kw.get("detail", {}),
    )


def _events():  # type: ignore[no-untyped-def]
    """重播/敘事/匯出用的小事件流。

    `dec` 同時帶 `status` 與 `hit`——**單發路徑（`engagement.resolve_engagement`）
    真的是兩個都寫**。統計那一節不用這組手寫事件（見該節開頭的說明）。
    """
    return [
        _ev(
            1,
            5,
            "ENGAGEMENT_RESOLVED",
            initiator="B1",
            target="R1",
            dmg=40.0,
            dec={"status": "HIT", "hit": True, "target_health_after": 60.0},
        ),
        _ev(2, 5, "GUARDRAIL_INTERVENTION", dec={"check": "G4"}),
        _ev(
            3,
            8,
            "ENGAGEMENT_RESOLVED",
            initiator="B1",
            target="R1",
            dmg=60.0,
            dec={"status": "HIT", "hit": True, "target_health_after": 0.0},
        ),
        _ev(4, 10, "REINFORCEMENT", dec={"msel_id": "m1"}),
    ]


# ---- O8.1 replay ----


def test_timeline_groups_by_tick() -> None:
    frames = build_timeline(_events())
    assert [f.tick for f in frames] == [5, 8, 10]
    assert frames[0].event_types == ["ENGAGEMENT_RESOLVED", "GUARDRAIL_INTERVENTION"]


def test_bookmarks_key_events() -> None:
    bms = bookmarks(_events())
    assert {b.event_type for b in bms} == {
        "ENGAGEMENT_RESOLVED",
        "GUARDRAIL_INTERVENTION",
        "REINFORCEMENT",
    }


def test_reconstruct_state_matches_recorded_after() -> None:
    # tick 5：R1 掉到 60（第一次交戰後態）；tick 8：R1 到 0
    at5 = reconstruct_states(_events(), 5)
    assert at5["R1"].health == 60.0
    at8 = reconstruct_states(_events(), 8)
    assert at8["R1"].health == 0.0
    # up_to_tick 之後的事件不套用
    at5b = reconstruct_states(_events(), 5)
    assert at5b["R1"].health == 60.0  # 不受 tick 8 事件影響


def _agg_events():  # type: ignore[no-untyped-def]
    """聚合交戰：後態是**戰力點**，damage_calc 是雙方損失相加。"""
    return [
        _ev(
            1,
            3,
            "AGGREGATE_ENGAGEMENT_RESOLVED",
            initiator="B-BN",
            target="R-BN",
            dmg=130.0,  # a_loss 50 + b_loss 80，**不是**單側戰損
            dec={
                "initiator_loss": 50.0,
                "target_loss": 80.0,
                "initiator_strength_after": 450.0,
                "target_strength_after": 320.0,
            },
        ),
    ]


def test_aggregate_strength_is_not_written_into_health_pct() -> None:
    """量綱：戰力點不得直接當效能%（過去 450 點會顯示成 health 450）。"""
    st = reconstruct_states(_agg_events(), 3, authorized={"B-BN": 500.0, "R-BN": 400.0})
    assert st["B-BN"].strength == 450.0
    assert st["R-BN"].strength == 320.0
    # 效能% 由戰力比經效能曲線導出，必落在 0–100
    assert 0.0 <= st["B-BN"].health <= 100.0
    assert 0.0 <= st["R-BN"].health <= 100.0
    # 與活模擬同一條曲線
    assert st["B-BN"].health == effectiveness_pct(450.0 / 500.0)
    assert st["R-BN"].health == effectiveness_pct(320.0 / 400.0)
    # 損失較重的一方效能較低
    assert st["R-BN"].health < st["B-BN"].health


def test_aggregate_without_authorized_keeps_points_but_does_not_guess_pct() -> None:
    """缺滿編戰力時：戰力點照記，但不給一個錯刻度的效能%。"""
    st = reconstruct_states(_agg_events(), 3)
    assert st["B-BN"].strength == 450.0
    assert st["B-BN"].health == 100.0  # 維持預設，不是 450


def test_aggregate_damage_calc_not_charged_to_one_side() -> None:
    """聚合事件的 damage_calc 是雙方損失和，不得從守方單側扣（§4 第 23 列）。"""
    st = reconstruct_states(_agg_events(), 3, authorized={"B-BN": 500.0, "R-BN": 400.0})
    # 若走了 fallback，R-BN 的 health 會被 100-130 扣成 0
    assert st["R-BN"].health > 0.0
    assert st["R-BN"].health == effectiveness_pct(320.0 / 400.0)


def test_individual_engagement_health_pct_unchanged_by_the_fix() -> None:
    """個體交戰記的本來就是效能%，行為不得改變（回歸釘）。"""
    st = reconstruct_states(_events(), 5)
    assert st["R1"].health == 60.0
    assert st["R1"].strength is None


def test_position_reconstructed_from_detail_not_ai_decision() -> None:
    """移動類事件把 lat/lng 記在 `detail`（movement.py 全部走 detail=）。

    位置分支原本只看 `ai_decision`，於是對任何真實移動都不生效——
    地圖重播會是「所有單位都不動」。這條釘住真實事件形狀。
    """
    evs = [
        _ev(1, 2, "UNIT_MOVED", initiator="B1", detail={"lat": 24.1, "lng": 120.5}),
        _ev(2, 6, "UNIT_ARRIVED", initiator="B1", detail={"lat": 24.9, "lng": 121.2}),
    ]
    at2 = reconstruct_states(evs, 2)
    assert (at2["B1"].lat, at2["B1"].lng) == (24.1, 120.5)
    at6 = reconstruct_states(evs, 6)
    assert (at6["B1"].lat, at6["B1"].lng) == (24.9, 121.2)
    # up_to_tick 之前不套用後面的移動
    assert (reconstruct_states(evs, 5)["B1"].lat) == 24.1


def test_position_still_read_from_ai_decision_when_present() -> None:
    """裁決類事件若把位置記在 ai_decision，仍要能取到（向後相容）。"""
    evs = [_ev(1, 4, "SOME_EVENT", initiator="B2", dec={"lat": 23.0, "lng": 120.0})]
    st = reconstruct_states(evs, 4)
    assert (st["B2"].lat, st["B2"].lng) == (23.0, 120.0)


def test_state_frames_only_list_changed_units_and_fields() -> None:
    """逐 tick 差異：只列有動到的單位、只列真的變了的欄位。"""
    evs = [
        _ev(1, 2, "UNIT_MOVED", initiator="B1", detail={"lat": 24.1, "lng": 120.5}),
        _ev(2, 2, "GUARDRAIL_INTERVENTION", dec={"check": "G4"}),
        _ev(
            3,
            5,
            "ENGAGEMENT_RESOLVED",
            initiator="B1",
            target="R1",
            dmg=40.0,
            dec={"target_health_after": 60.0},
        ),
    ]
    frames = state_frames(evs)
    assert [f.tick for f in frames] == [2, 5]
    # tick 2：只有 B1 動了位置（護欄事件無單位）
    assert [c.unit_id for c in frames[0].changes] == ["B1"]
    assert (frames[0].changes[0].lat, frames[0].changes[0].lng) == (24.1, 120.5)
    assert frames[0].changes[0].health is None  # 血量沒變就不列
    # tick 5：只有 R1 掉血；B1 是攻擊方但狀態沒變 → 不列
    assert [c.unit_id for c in frames[1].changes] == ["R1"]
    assert frames[1].changes[0].health == 60.0
    assert frames[1].changes[0].lat is None


def test_state_frames_accumulate_to_same_result_as_reconstruct() -> None:
    """差異流累加後必須等於同一 tick 的 reconstruct_states——兩條路徑共用套用邏輯。"""
    evs = [
        _ev(1, 2, "UNIT_MOVED", initiator="B1", detail={"lat": 24.1, "lng": 120.5}),
        _ev(
            2,
            4,
            "AGGREGATE_ENGAGEMENT_RESOLVED",
            initiator="B-BN",
            target="R-BN",
            dmg=130.0,
            dec={"initiator_strength_after": 450.0, "target_strength_after": 320.0},
        ),
        _ev(3, 6, "UNIT_ARRIVED", initiator="B1", detail={"lat": 24.9, "lng": 121.2}),
    ]
    auth = {"B-BN": 500.0, "R-BN": 400.0}
    acc: dict[str, dict[str, float]] = {}
    for f in state_frames(evs, auth):
        if f.tick > 4:
            break
        for c in f.changes:
            cur = acc.setdefault(c.unit_id, {})
            for k in ("lat", "lng", "health", "strength"):
                v = getattr(c, k)
                if v is not None:
                    cur[k] = v
    direct = reconstruct_states(evs, 4, auth)
    assert acc["B1"]["lat"] == direct["B1"].lat
    assert acc["B-BN"]["strength"] == direct["B-BN"].strength
    assert acc["R-BN"]["health"] == direct["R-BN"].health


def test_reconstruct_survives_non_monotonic_ticks() -> None:
    """帳本依 seq 排，tick 未必單調——實測既有推演第一筆就是 tick 3700。

    原本一遇到 `tick > up_to_tick` 就 break，這種帳本會立刻中斷回空狀態（地圖全空）。
    """
    evs = [
        _ev(1, 3700, "TICK_OVERRUN", dec={"ms": 12}),  # seq 最小但 tick 最大
        _ev(
            2,
            5,
            "ENGAGEMENT_RESOLVED",
            initiator="B1",
            target="R1",
            dmg=40.0,
            dec={"target_health_after": 60.0},
        ),
        _ev(3, 8, "UNIT_MOVED", initiator="B1", detail={"lat": 24.5, "lng": 120.9}),
    ]
    st = reconstruct_states(evs, 10)
    assert st["R1"].health == 60.0, "被 tick 3700 提前 break 了"
    assert st["B1"].lat == 24.5
    # 差異流也要照 tick 排
    assert [f.tick for f in state_frames(evs)] == [5, 8, 3700]


def test_recorded_health_wins_over_derived() -> None:
    """事件同時記 health% 與戰力點時，**記錄值是權威**，不得被導出值覆寫。

    目前兩者用同一條公式（engagement.py）所以數值相同；這條釘住的是**優先順序**，
    以免日後效能曲線分歧時，重播悄悄顯示與帳本不同的數字。
    """
    evs = [
        _ev(
            1,
            4,
            "ENGAGEMENT_RESOLVED",
            initiator="B1",
            target="R1",
            dec={"target_health_after": 77.0, "target_strength_after": 30.0},
        ),
    ]
    st = reconstruct_states(evs, 4, authorized={"R1": 100.0})
    assert st["R1"].health == 77.0, "被 effectiveness_pct(30/100) 覆寫了"
    assert st["R1"].strength == 30.0


def test_replay_summary() -> None:
    s = replay_summary(_events())
    assert s.total_events == 4 and s.max_tick == 10 and len(s.bookmarks) == 4


# ---- O8.2 stats ----
#
# ⚠ **本節的事件一律由真裁決函式產生**（WP-D6.2）。
#
# 這裡原本餵的是手寫合成事件（帶 `hit` 鍵），而生產事件裡只有單發路徑寫 `hit`：
# `engagement.py` 的閘門是「建制數 >1 且目標有 strength → 齊射」（幾乎所有班/排/連都成立）、
# `adjudicator` 則在射手持 ≥2 武器系統時走聯合兵種——這兩條主力路徑只寫 `status`。
# 於是「分子只認 `hit`」的 bug 讓真實推演的命中率恆偏低甚至為 0，
# 而測試因為餵的是自己捏的形狀，斷言 `hit_rate == 1.0` 一路綠燈。
# 修數字只值一次，修測試的資料來源值一輩子：形狀從此由裁決層決定，不由測試決定。

# 步槍：近距 ph=0.5、對 INFANTRY pk=0.4、每 tick 射速 3（同 test_engagement_volley 的設定）。
_RIFLE = WeaponProfile.from_base_stats(
    {
        "max_range_m": 500,
        "ph_by_range_band": [[100, 0.5], [500, 0.5]],
        "damage_by_armor_class": {"INFANTRY": 50},
        "pk_by_armor_class": {"INFANTRY": 0.4},
        "ammo_types": ["5.56"],
        "rate_per_tick": 3.0,
    }
)
# 反戰車飛彈：長射程、只殺裝甲（對 INFANTRY pk 缺→0）——用來確認「有武器發射但打不動」
# 仍算一次實射（MISS），不是被拒。
_ATGM = WeaponProfile.from_base_stats(
    {
        "max_range_m": 4000,
        "ph_by_range_band": [[500, 0.9], [4000, 0.6]],
        "damage_by_armor_class": {"ARMOR": 200},
        "pk_by_armor_class": {"ARMOR": 0.8},
        "ammo_types": ["ATGM"],
        "rate_per_tick": 1.0,
        "kinetic_kind": "ATGM",
    }
)
# 榴彈砲：面射擊用（cep/lethal_radius 才是這條路徑的物理）。
_HOWITZER = WeaponProfile.from_base_stats(
    {
        "max_range_m": 15000,
        "ph_by_range_band": [[1000, 0.5], [15000, 0.3]],
        "damage_by_armor_class": {"SOFT": 60},
        "pk_by_armor_class": {"SOFT": 0.6},
        "ammo_types": ["155MM"],
        "indirect_fire": True,
        "dispersion_cep_m": 30.0,
        "lethal_radius_m": 50.0,
    }
)


def _rng(seed: int = 7) -> DeterministicRNG:
    return DeterministicRNG(seed, "adjudication")


def _infantry_target(unit_id: str = "R1", strength: float = 100.0) -> Target:
    return Target(
        unit_id=unit_id,
        armor_class="INFANTRY",
        current_strength=strength,
        authorized_strength=100.0,
        platform_count=9,
    )


def _as_aar(events: Sequence[LedgerEvent], start_seq: int = 1) -> list[AarEvent]:
    """LedgerEvent → AarEvent：與 `aar/events._to_aar` 同一條投影（那支是從 DB row 讀）。

    測試與生產之間只允許差在「事件從哪裡來」（記憶體 vs DB），
    **不允許差在事件長什麼樣**——那正是本卡要堵的洞。
    """
    return [
        AarEvent(
            seq=start_seq + i,
            tick=e.tick,
            event_type=e.event_type,
            initiator_id=e.initiator_id,
            target_id=e.target_id,
            ai_decision=dict(e.ai_decision or {}),
            damage_calc=e.damage_calc,
            reasoning_chain=e.reasoning_chain,
            detail=dict(e.detail or {}),
        )
        for i, e in enumerate(events)
    ]


def _volley(tick: int = 5, shooter: str = "B1", target: str = "R1") -> LedgerEvent:
    """真實齊射事件：建制數 7 → 走 `_resolve_volley`（`engagement.py` 的閘門）。"""
    res = resolve_engagement(
        _RIFLE,
        Shooter(shooter, ammo_count=999, quantity=7),
        _infantry_target(target),
        EnvSnapshot(range_m=200, los_clear=True),
        _rng(),
        tick,
    )
    assert res.status is Resolution.HIT  # 前提沒站住的話，後面的斷言都沒有意義
    return res.events[0]


def _combined(tick: int = 6, shooter: str = "B2", target: str = "R1") -> LedgerEvent:
    """真實聯合兵種事件：兩件武器 → 走 `resolve_combined_engagement`。"""
    res = resolve_combined_engagement(
        [
            CombinedWeapon("w-rifle", _RIFLE, quantity=7, ammo=100),
            CombinedWeapon("w-atgm", _ATGM, quantity=2, ammo=8),
        ],
        shooter_id=shooter,
        shooter_effectiveness=1.0,
        target=_infantry_target(target),
        env_for=lambda _p: EnvSnapshot(range_m=300, los_clear=True),
        rng=_rng(),
        tick=tick,
    )
    assert res.status is Resolution.HIT
    return res.events[0]


def _out_of_range(tick: int = 7, shooter: str = "B3", target: str = "R1") -> LedgerEvent:
    """真實被拒事件：射程外 → `_rejected`（不擲骰、不耗彈、一發未發）。"""
    res = resolve_engagement(
        _RIFLE,
        Shooter(shooter, ammo_count=999, quantity=7),
        _infantry_target(target),
        EnvSnapshot(range_m=9000, los_clear=True),
        _rng(),
        tick,
    )
    assert res.status is Resolution.REJECTED and res.reason == "OUT_OF_RANGE"
    return res.events[0]


def _aggregate(tick: int = 9):  # type: ignore[no-untyped-def]
    """真實聚合交戰：Lanchester 一個 tick，雙方同時消耗。"""
    return resolve_aggregate_tick(
        AggregateForce("B-BN", "BLUE", strength=500.0, lethality=0.02),
        AggregateForce("R-BN", "RED", strength=400.0, lethality=0.03),
        AggregateEnv(variance=0.2),
        _rng(11),
        tick,
    )


_FACTIONS = {"B1": "BLUE", "B2": "BLUE", "B3": "BLUE", "R1": "RED", "B-BN": "BLUE", "R-BN": "RED"}


# -- 前提：生產事件真的長這樣（斷言的地基，不是重複測裁決層） --


def test_real_volley_and_combined_events_carry_status_but_no_hit_key() -> None:
    """齊射與聯合兵種**不寫 `hit`**——這正是舊分子失效的原因，釘住它。

    這條若變紅：要嘛裁決層開始寫 `hit`（那 `_resolution` 的相容分支要重新檢視），
    要嘛 `status` 不見了（那 AAR 全部的命中統計會靜默歸零）。兩種都必須有人看到。
    """
    for event in (_volley(), _combined()):
        assert event.ai_decision["status"] == Resolution.HIT.value
        assert "hit" not in event.ai_decision


def test_real_aggregate_damage_calc_is_both_sides_summed() -> None:
    """聚合的 `damage_calc = a_loss + b_loss`，而 `target_id` 只指守方。

    **這是「不能拿 damage_calc 歸帳」的唯一理由**，所以它要由真函式驗，
    不能靠註解流傳。寫入端不改（改了會動 ledger canonical payload → 雜湊鏈 → 既有局驗不過），
    只能在讀端改判。
    """
    res = _aggregate()
    event = res.events[0]
    assert event.damage_calc == pytest.approx(res.a_loss + res.b_loss)
    assert res.a_loss > 0.0 and res.b_loss > 0.0
    assert event.target_id == "R-BN"  # 只有守方掛在 target_id 上
    assert event.ai_decision["initiator_loss"] == pytest.approx(res.a_loss)
    assert event.ai_decision["target_loss"] == pytest.approx(res.b_loss)


# -- 分子：status 才是權威 --


def test_hits_count_volley_and_combined_engagements() -> None:
    """齊射與聯合兵種的命中要計進分子（舊版只認 `hit` → 這兩筆全漏，命中率 0）。"""
    m = compute_metrics(_as_aar([_volley(), _combined()]), _FACTIONS)
    assert m.hits == 2
    assert m.engagements_fired == 2
    assert m.hit_rate == 1.0


def test_hit_rate_is_zero_when_the_only_shots_all_miss() -> None:
    """反向釘：真的沒打中時分子必須是 0——避免「把 status 當真值判斷」之類的假修法。

    反戰車飛彈對步兵 pk=0 → 有發射、有耗彈，但毀傷 0 → MISS（不是 REJECTED）。
    """
    res = resolve_combined_engagement(
        [CombinedWeapon("w-atgm", _ATGM, quantity=2, ammo=8)],
        shooter_id="B2",
        shooter_effectiveness=1.0,
        target=_infantry_target(),
        env_for=lambda _p: EnvSnapshot(range_m=1000, los_clear=True),
        rng=_rng(),
        tick=4,
    )
    assert res.status is Resolution.MISS
    m = compute_metrics(_as_aar(res.events), _FACTIONS)
    assert m.hits == 0
    assert m.engagements_fired == 1  # 射出去了，只是沒打動——分母算它
    assert m.hit_rate == 0.0


def test_legacy_ledger_with_only_the_hit_key_still_counts() -> None:
    """舊帳本/封存包可能寫於 `status` 之前——相容分支不得退化。"""
    legacy = [
        _ev(1, 2, "ENGAGEMENT_RESOLVED", initiator="B1", target="R1", dmg=9.0, dec={"hit": True}),
        _ev(2, 3, "ENGAGEMENT_RESOLVED", initiator="B1", target="R1", dmg=0.0, dec={"hit": False}),
    ]
    m = compute_metrics(legacy, _FACTIONS)
    assert m.hits == 1 and m.engagements_fired == 2 and m.hit_rate == 0.5


# -- 分母：被拒的交戰一發未發 --


def test_rejected_engagements_are_excluded_from_hit_rate_denominator() -> None:
    """超射程／無彈／無視線／ROE 都是一發未發，不該稀釋火力效益。

    舊版分母是 `ENGAGEMENT_RESOLVED` 的**全部**筆數：這裡 1 中 1 拒會被算成 50%。
    """
    m = compute_metrics(_as_aar([_volley(), _out_of_range()]), _FACTIONS)
    assert m.attempts == 2  # 下了兩次令
    assert m.engagements_fired == 1  # 只有一次射得出去
    assert m.hits == 1
    assert m.hit_rate == 1.0


def test_attempts_and_fired_are_separate_numbers() -> None:
    """兩個語意要有兩個欄位——一個數字承載兩種語意正是這個 bug 的成因。"""
    m = compute_metrics(_as_aar([_volley(), _combined(), _out_of_range()]), _FACTIONS)
    assert (m.attempts, m.engagements_fired) == (3, 2)
    assert m.attempts - m.engagements_fired == 1  # 被拒次數推得出來


def test_all_rejected_reports_no_hit_rate_rather_than_zero() -> None:
    """一發未發時 hit_rate 是「無從計算」，分母為 0 不得爆掉。"""
    m = compute_metrics(_as_aar([_out_of_range()]), _FACTIONS)
    assert m.engagements_fired == 0 and m.hit_rate == 0.0


def test_aggregate_engagements_do_not_enter_the_hit_rate() -> None:
    """聚合是 Lanchester 消耗，沒有命中/失手可言——只進 `engagements`，不進分子分母。"""
    m = compute_metrics(_as_aar(_aggregate().events), _FACTIONS)
    assert m.engagements == 1
    assert m.attempts == 0 and m.engagements_fired == 0 and m.hits == 0


# -- 聚合戰損：雙側分別歸帳 --


def test_aggregate_losses_are_charged_to_each_side_separately() -> None:
    """守方不得被記上雙方總損失（`damage_calc = a_loss + b_loss`，D6.1 已在 replay 修過）。"""
    res = _aggregate()
    m = compute_metrics(_as_aar(res.events), _FACTIONS)
    assert m.damage_by_faction["BLUE"] == pytest.approx(res.a_loss, abs=1e-3)
    assert m.damage_by_faction["RED"] == pytest.approx(res.b_loss, abs=1e-3)
    # 舊版的症狀：RED 拿到 a_loss + b_loss
    assert m.damage_by_faction["RED"] != pytest.approx(res.a_loss + res.b_loss, abs=1e-3)
    # 兩側相加仍等於全場總戰損（能量守恆，沒有憑空多出來也沒有漏掉）
    assert sum(m.damage_by_faction.values()) == pytest.approx(m.total_damage, abs=1e-3)


def test_individual_engagement_damage_still_charged_to_the_target() -> None:
    """個體交戰的 `damage_calc` 就是目標單側承受的量——回歸釘，別把它一起改壞。"""
    event = _volley()
    m = compute_metrics(_as_aar([event]), _FACTIONS)
    assert m.damage_by_faction == {"RED": pytest.approx(event.damage_calc, abs=1e-3)}


def test_area_fire_losses_counted_once_not_twice() -> None:
    """面射擊由 `losses_by_unit` 逐單位歸帳；`damage_calc` 不得再記一次。"""
    res = resolve_area_fire(
        _HOWITZER,
        (24.0, 121.0),
        [
            AreaTarget("R1", "RED", 24.0, 121.0, current_strength=100.0, authorized_strength=100.0),
            AreaTarget(
                "R2", "RED", 24.0001, 121.0001, current_strength=80.0, authorized_strength=100.0
            ),
        ],
        _rng(3),
        tick=12,
        shooter_id="B1",
    )
    total = sum(res.losses.values())
    assert total > 0.0  # 前提：真的打到了，否則這條測不到重複計
    m = compute_metrics(_as_aar([res.event]), {"R1": "RED", "R2": "RED", "B1": "BLUE"})
    assert m.damage_by_faction["RED"] == pytest.approx(total, abs=1e-3)


# -- 口徑版本 --


def test_metrics_carry_the_stats_version() -> None:
    """封存包會把數字寫進歷史演習；沒有版本號就分不出新舊口徑（見 stats.py 的說明）。"""
    assert compute_metrics([]).stats_version == STATS_VERSION
    # 降回 1 等於宣稱「這是舊口徑算的」——舊封存就分不出來了。
    assert STATS_VERSION >= 2


# -- 綜合：一整場的形狀 --


def test_metrics_from_real_mixed_ledger() -> None:
    """齊射 + 聯合兵種 + 被拒 + 聚合 + 護欄，一次走完。"""
    agg = _aggregate()
    events = _as_aar([_volley(), _combined(), _out_of_range(), *agg.events])
    events.append(_ev(99, 10, "GUARDRAIL_INTERVENTION", dec={"check": "G4"}))
    m = compute_metrics(events, _FACTIONS)
    assert m.total_events == 5
    assert m.engagements == 4  # 3 個體 + 1 聚合
    assert (m.attempts, m.engagements_fired, m.hits) == (3, 2, 2)
    assert m.hit_rate == 1.0
    assert m.guardrail_blocks == 1
    assert m.max_tick == 10
    assert set(m.damage_by_faction) == {"BLUE", "RED"}
    assert m.damage_by_faction["BLUE"] == pytest.approx(agg.a_loss, abs=1e-3)


# ---- O8.3 narrative ----


def test_narrative_cites_only_real_seqs() -> None:
    events = _events()
    narr = generate_narrative(events)
    assert verify_citations(narr, events) == []  # 引用全部存在
    assert all(s in {e.seq for e in events} for s in narr.all_cited_seqs)
    assert narr.lessons  # 有教訓


def test_verify_citations_catches_fabricated() -> None:
    events = _events()
    from app.aar.narrative import AarNarrative, NarrativeParagraph

    bad = AarNarrative(
        summary="x", paragraphs=[NarrativeParagraph("捏造", cited_seqs=[999])], lessons=[]
    )
    assert verify_citations(bad, events) == [999]


# ---- O8.4 export ----


def test_export_json_and_csv() -> None:
    events = _events()
    js = export_json(events)
    assert '"seq": 1' in js and "target_health_after" in js  # 完整含 ai_decision
    csv_out = export_csv(events)
    assert "seq,tick,event_type" in csv_out and "ENGAGEMENT_RESOLVED" in csv_out


def test_anonymize_strips_unit_names_and_cot() -> None:
    events = [
        _ev(
            1,
            5,
            "ENGAGEMENT_RESOLVED",
            initiator="B-1PLT",
            target="R-CO",
            dmg=10.0,
            cot="機密：指揮官張三下令",
        ),
    ]
    js = export_json(events, anonymize=True)
    assert "B-1PLT" not in js and "R-CO" not in js  # 單位真名去除
    assert "UNIT-1" in js and "UNIT-2" in js  # 匿名標籤
    assert "張三" not in js and "reasoning_chain" not in js  # CoT 去除
