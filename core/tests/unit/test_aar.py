"""AAR：重播/統計/敘事/匯出（O8.1–O8.4，SPEC §14）——純函數。"""

from __future__ import annotations

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
from app.aar.stats import compute_metrics
from app.adjudication.effectiveness import effectiveness_pct


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
    return [
        _ev(
            1,
            5,
            "ENGAGEMENT_RESOLVED",
            initiator="B1",
            target="R1",
            dmg=40.0,
            dec={"hit": True, "target_health_after": 60.0},
        ),
        _ev(2, 5, "GUARDRAIL_INTERVENTION", dec={"check": "G4"}),
        _ev(
            3,
            8,
            "ENGAGEMENT_RESOLVED",
            initiator="B1",
            target="R1",
            dmg=60.0,
            dec={"hit": True, "target_health_after": 0.0},
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


def test_metrics_from_ledger() -> None:
    m = compute_metrics(_events(), {"R1": "RED", "B1": "BLUE"})
    assert m.engagements == 2 and m.hits == 2 and m.hit_rate == 1.0
    assert m.total_damage == 100.0
    assert m.guardrail_blocks == 1
    assert m.damage_by_faction == {"RED": 100.0}  # R1 承受全部


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
