"""Guardrail Gateway G1–G6（O6.2，SPEC_FULL §10）：每個 G 至少一個「必攔」案例 + 模式感知 G5。"""

from __future__ import annotations

from typing import Any

from app.guardrails import GuardrailGateway, NoRagCitationVerifier, intervention_events
from app.guardrails.profiles import GuardrailProfile
from app.models.enums import AiMode

_COT = (
    "1. 藍軍已於 H-45 高地建立觀測所，控制主要接近路線與火力視野。\n"
    "2. 我方兵力居於相對劣勢，正面決戰不利，須避免被固定。\n"
    "3. 依遲滯準則採打了就走，於天然隘口逐線遲滯敵之前進節奏。"
)


def _valid_opfor(**over: Any) -> dict[str, Any]:
    base = {
        "reasoning_chain": _COT,
        "confidence": 0.7,
        "intent": "delay_and_attrit",
        "orders": [{"unit_id": "R1", "order_type": "MOVE", "target_h3": "8a11"}],
        "ihl_self_check": {"civilian_risk_assessed": True},
    }
    base.update(over)
    return base


GW = GuardrailGateway()


def test_g1_blocks_schema_violation() -> None:
    bad = _valid_opfor()
    del bad["ihl_self_check"]  # opfor_decision 必填
    out = GW.evaluate(bad, schema_ref="opfor_decision", mode=AiMode.AI_BARE)
    assert not out.accepted
    assert any(f.check == "G1" and f.blocked for f in out.findings)


def test_g2_blocks_thin_cot() -> None:
    # 夠長（過 G1 minLength 80）但無分步 → G2 判定步驟不足。
    thin = (
        "敵軍出現於東側林線我方應立即全線展開強攻以火力壓制並迅速奪取該高地確保後續機動不受威脅"
        "鞏固側翼安全同時保持與友鄰單位橫向聯繫避免暴露翼側於敵直射火力之下並確保補給線暢通無阻"
    )
    out = GW.evaluate(
        _valid_opfor(reasoning_chain=thin),
        schema_ref="opfor_decision",
        mode=AiMode.AI_BARE,
    )
    assert not out.accepted
    assert any(f.check == "G2" and f.blocked for f in out.findings)


def test_g3_removes_infeasible_orders() -> None:
    class Blocker:
        def is_feasible(self, order: dict[str, Any]) -> tuple[bool, str]:
            return (order["unit_id"] != "R1", "unreachable")

    out = GW.evaluate(
        _valid_opfor(), schema_ref="opfor_decision", mode=AiMode.AI_BARE, feasibility=Blocker()
    )
    assert out.accepted  # G3 非致命
    assert out.sanitized is not None and out.sanitized["orders"] == []
    assert any(f.check == "G3" and f.blocked for f in out.findings)


def test_g4_hard_blocks_no_strike_target() -> None:
    out = GW.evaluate(
        _valid_opfor(orders=[{"unit_id": "R1", "order_type": "ENGAGE", "target_h3": "HOSPITAL"}]),
        schema_ref="opfor_decision",
        mode=AiMode.AI_BARE,
        no_strike_hexes=frozenset({"HOSPITAL"}),
    )
    assert not out.accepted  # 硬阻擋
    assert out.escalate_white_cell
    assert out.sanitized is not None and out.sanitized["orders"] == []  # 違規 order 移除
    assert any(f.check == "G4" and f.blocked for f in out.findings)


def test_g5_strips_fabricated_citation_in_bare_mode() -> None:
    out = GW.evaluate(
        _valid_opfor(cited_documents=["doctrine_red/x.md#A"]),
        schema_ref="opfor_decision",
        mode=AiMode.AI_BARE,
    )
    assert out.accepted
    assert out.sanitized is not None and out.sanitized["cited_documents"] == []
    assert any(f.check == "G5" and f.blocked for f in out.findings)


def test_g5_empty_citation_passes_in_bare_mode() -> None:
    out = GW.evaluate(_valid_opfor(), schema_ref="opfor_decision", mode=AiMode.AI_BARE)
    assert out.accepted
    assert not any(f.check == "G5" and f.blocked for f in out.findings)


def test_g5_full_mode_flags_unverified_and_lowers_confidence() -> None:
    class Verifier:
        def verify(self, citation: str) -> bool:
            return citation == "real.md#OK"

        @property
        def index_empty(self) -> bool:
            return False

    out = GW.evaluate(
        _valid_opfor(cited_documents=["real.md#OK", "fake.md#NO"], confidence=0.8),
        schema_ref="opfor_decision",
        mode=AiMode.AI_FULL,
        citation_verifier=Verifier(),
    )
    assert out.accepted
    assert out.sanitized is not None
    assert out.sanitized["confidence"] == 0.4  # 0.8 * 0.5
    assert out.sanitized["citation_unverified"] == ["fake.md#NO"]


def test_g5_full_mode_empty_index_falls_back_to_bare() -> None:
    out = GW.evaluate(
        _valid_opfor(cited_documents=["x.md#A"]),
        schema_ref="opfor_decision",
        mode=AiMode.AI_FULL,
        citation_verifier=NoRagCitationVerifier(),  # 空庫
    )
    assert out.sanitized is not None and out.sanitized["cited_documents"] == []  # 按 AI_BARE 語義


def test_g6_quantized_escalates_engage_orders() -> None:
    gw = GuardrailGateway(GuardrailProfile(adapter_quantized=True))
    out = gw.evaluate(
        _valid_opfor(orders=[{"unit_id": "R1", "order_type": "ENGAGE", "target_h3": "8a11"}]),
        schema_ref="opfor_decision",
        mode=AiMode.AI_BARE,
    )
    assert out.escalate_white_cell
    assert any(f.check == "G6" and f.blocked for f in out.findings)


def test_clean_output_passes_all() -> None:
    out = GW.evaluate(_valid_opfor(), schema_ref="opfor_decision", mode=AiMode.AI_BARE)
    assert out.accepted and not out.escalate_white_cell
    assert not any(f.blocked for f in out.findings)


def _valid_coa(**over: Any) -> dict[str, Any]:
    base = {
        "reasoning_chain": _COT,
        "confidence": 0.6,
        "courses_of_action": [
            {
                "name": "COA-1",
                "summary": "遲滯",
                "draft_orders": [
                    {"unit_id": "B1", "order_type": "MOVE", "target_h3": "8a11"},
                    {"unit_id": "B2", "order_type": "ENGAGE", "target_h3": "HOSP"},
                ],
                "risks": ["兵力不足"],
            }
        ],
    }
    base.update(over)
    return base


def test_coa_recommendation_g4_filters_draft_orders() -> None:
    out = GW.evaluate(
        _valid_coa(),
        schema_ref="coa_recommendation",
        mode=AiMode.AI_BARE,
        no_strike_hexes=frozenset({"HOSP"}),
    )
    assert not out.accepted and out.escalate_white_cell
    assert out.sanitized is not None
    kept = out.sanitized["courses_of_action"][0]["draft_orders"]
    assert [o["unit_id"] for o in kept] == ["B1"]  # ENGAGE@HOSP 被移除


def test_g1_unknown_schema_ref_blocks() -> None:
    out = GW.evaluate(_valid_opfor(), schema_ref="nonexistent", mode=AiMode.AI_BARE)
    assert not out.accepted
    assert any(f.check == "G1" and "未知" in f.reason for f in out.findings)


def test_no_rag_verifier_rejects_everything() -> None:
    v = NoRagCitationVerifier()
    assert v.index_empty is True
    assert v.verify("anything.md#X") is False


def test_full_mode_all_verified_passes_clean() -> None:
    class AllGood:
        def verify(self, citation: str) -> bool:
            return True

        @property
        def index_empty(self) -> bool:
            return False

    out = GW.evaluate(
        _valid_opfor(cited_documents=["a.md#1"]),
        schema_ref="opfor_decision",
        mode=AiMode.AI_FULL,
        citation_verifier=AllGood(),
    )
    assert out.accepted
    assert not any(f.check == "G5" and f.blocked for f in out.findings)


def test_profile_quantized_override(tmp_path: Any) -> None:
    from app.guardrails.profiles import load_profile

    p = tmp_path / "gp.yaml"
    p.write_text("cot_min_steps: 4\nadapter_quantized: false\n", encoding="utf-8")
    prof = load_profile(p, quantized_override=True)
    assert prof.cot_min_steps == 4 and prof.adapter_quantized is True


def test_interventions_become_ledger_events() -> None:
    out = GW.evaluate(
        _valid_opfor(cited_documents=["fake.md#A"]),
        schema_ref="opfor_decision",
        mode=AiMode.AI_BARE,
    )
    events = intervention_events(out.findings, tick=5, initiator_id="R1")
    assert all(e.event_type == "GUARDRAIL_INTERVENTION" and e.tick == 5 for e in events)
    assert any(e.ai_decision["check"] == "G5" for e in events)
