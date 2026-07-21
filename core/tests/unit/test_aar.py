"""AAR：重播/統計/敘事/匯出（O8.1–O8.4，SPEC §14）——純函數。"""

from __future__ import annotations

from app.aar.events import AarEvent
from app.aar.export import export_csv, export_json
from app.aar.narrative import generate_narrative, verify_citations
from app.aar.replay import bookmarks, build_timeline, reconstruct_states, replay_summary
from app.aar.stats import compute_metrics


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
