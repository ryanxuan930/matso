"""Guardrail Gateway G1–G6（SPEC_FULL §10）——強制、依序、可測。

G1 JSON Schema · G2 CoT 存在與最小長度 · G3 物理可行性 · G4 IHL/ROE · G5 引用查核（模式感知）·
G6 量化加嚴。攔截 → GuardrailFinding（blocked=True），由呼叫端轉 GUARDRAIL_INTERVENTION 事件。

不可 bypass（紅線 3）：Gateway 沒有「跳過某 G」的參數。嚴格度只由 GuardrailProfile 調。
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

from jsonschema import Draft202012Validator

from app.guardrails.profiles import GuardrailProfile, load_profile
from app.guardrails.schemas import CitationVerifier, GuardrailFinding, GuardrailOutcome
from app.models.enums import AiMode

_SCHEMA_PATH = Path(__file__).resolve().parents[3] / "contracts" / "ai_output.schema.json"
_STEP_RE = re.compile(r"(?m)^\s*\d+[.、)]")


class OrderFeasibilityChecker(Protocol):
    """G3 物理可行性介面（真實現＝Terrain/Comms 預檢，注入於 O6.5）。"""

    def is_feasible(self, order: dict[str, Any]) -> tuple[bool, str]: ...  # pragma: no cover


@lru_cache(maxsize=1)
def _schema_defs() -> dict[str, Any]:
    doc = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    defs: dict[str, Any] = doc["$defs"]
    return defs


def _count_cot_steps(chain: str) -> int:
    numbered = len(_STEP_RE.findall(chain))
    if numbered:
        return numbered
    return len([ln for ln in chain.splitlines() if ln.strip()])


def _orders_of(output: dict[str, Any]) -> list[dict[str, Any]]:
    """讀取（唯讀）AI 輸出中的所有 order（相容 opfor_decision 與 coa_recommendation）。"""
    if isinstance(output.get("orders"), list):
        return [o for o in output["orders"] if isinstance(o, dict)]
    out: list[dict[str, Any]] = []
    for coa in output.get("courses_of_action", []) or []:
        if isinstance(coa, dict):
            out.extend(o for o in coa.get("draft_orders", []) or [] if isinstance(o, dict))
    return out


def _filter_orders(output: dict[str, Any], keep: Callable[[dict[str, Any]], bool]) -> int:
    """就地移除 keep()=False 的 order；回傳移除數。"""
    removed = 0
    if isinstance(output.get("orders"), list):
        before = len(output["orders"])
        output["orders"] = [o for o in output["orders"] if not isinstance(o, dict) or keep(o)]
        removed += before - len(output["orders"])
    for coa in output.get("courses_of_action", []) or []:
        if isinstance(coa, dict) and isinstance(coa.get("draft_orders"), list):
            before = len(coa["draft_orders"])
            coa["draft_orders"] = [
                o for o in coa["draft_orders"] if not isinstance(o, dict) or keep(o)
            ]
            removed += before - len(coa["draft_orders"])
    return removed


class GuardrailGateway:
    def __init__(self, profile: GuardrailProfile | None = None) -> None:
        self._profile = profile or load_profile()

    def evaluate(
        self,
        ai_output: dict[str, Any],
        *,
        schema_ref: str,
        mode: AiMode,
        no_strike_hexes: frozenset[str] = frozenset(),
        feasibility: OrderFeasibilityChecker | None = None,
        citation_verifier: CitationVerifier | None = None,
    ) -> GuardrailOutcome:
        findings: list[GuardrailFinding] = []

        # G1 — JSON Schema。不過即無法信任結構 → 不接受（呼叫端重試 ≤2 → fallback）。
        g1 = self._g1_schema(ai_output, schema_ref)
        findings.append(g1)
        if g1.blocked:
            return GuardrailOutcome(accepted=False, sanitized=None, findings=findings)

        sanitized = copy.deepcopy(ai_output)

        # G2 — CoT 存在與最小步驟。不過 → 退回重生成（不接受）。
        g2 = self._g2_cot(sanitized)
        findings.append(g2)
        if g2.blocked:
            return GuardrailOutcome(accepted=False, sanitized=None, findings=findings)

        # G3 — 物理可行性：逐條剔除不可行 order（非致命）。
        findings.append(self._g3_physics(sanitized, feasibility))

        # G4 — IHL/ROE：打擊保護目標 → 硬阻擋 + 升 White Cell。
        g4 = self._g4_ihl_roe(sanitized, no_strike_hexes)
        findings.append(g4)

        # G6 — 量化加嚴：ENGAGE 令改白軍逐條確認（升 White Cell，不移除）。
        g6 = self._g6_quantized(sanitized)
        findings.append(g6)

        # G5 — 引用查核（模式感知）。
        findings.append(self._g5_citations(sanitized, mode, citation_verifier))

        escalate = g4.blocked or g6.blocked
        accepted = not g4.blocked  # G4 硬阻擋 → fallback；其餘（G3/G5/G6）為清洗/標記
        return GuardrailOutcome(
            accepted=accepted, sanitized=sanitized, findings=findings, escalate_white_cell=escalate
        )

    # ---- 各檢查 ----

    def _g1_schema(self, output: dict[str, Any], schema_ref: str) -> GuardrailFinding:
        defs = _schema_defs()
        if schema_ref not in defs:
            return GuardrailFinding("G1", True, f"未知的 schema_ref：{schema_ref}")
        validator = Draft202012Validator({"$defs": defs, "$ref": f"#/$defs/{schema_ref}"})
        errors = [e.message for e in validator.iter_errors(output)]
        if errors:
            return GuardrailFinding("G1", True, "輸出不符 schema", {"errors": errors[:5]})
        return GuardrailFinding("G1", False, "schema ok")

    def _g2_cot(self, output: dict[str, Any]) -> GuardrailFinding:
        chain = output.get("reasoning_chain")
        if not isinstance(chain, str) or not chain.strip():
            return GuardrailFinding("G2", True, "reasoning_chain 缺失或為空")
        steps = _count_cot_steps(chain)
        if steps < self._profile.cot_min_steps:
            return GuardrailFinding(
                "G2",
                True,
                f"CoT 步驟不足（{steps} < {self._profile.cot_min_steps}）",
                {"steps": steps},
            )
        return GuardrailFinding("G2", False, f"CoT ok（{steps} 步）")

    def _g3_physics(
        self, output: dict[str, Any], feasibility: OrderFeasibilityChecker | None
    ) -> GuardrailFinding:
        if feasibility is None:
            return GuardrailFinding("G3", False, "無物理檢查器（略過；O6.5 注入）")
        infeasible: list[str] = []

        def keep(order: dict[str, Any]) -> bool:
            ok, reason = feasibility.is_feasible(order)
            if not ok:
                infeasible.append(f"{order.get('unit_id', '?')}:{reason}")
            return ok

        removed = _filter_orders(output, keep)
        if removed:
            return GuardrailFinding(
                "G3", True, f"剔除 {removed} 筆不可行 order", {"infeasible": infeasible}
            )
        return GuardrailFinding("G3", False, "所有 order 物理可行")

    def _g4_ihl_roe(
        self, output: dict[str, Any], no_strike_hexes: frozenset[str]
    ) -> GuardrailFinding:
        violations = [
            o.get("target_h3") for o in _orders_of(output) if o.get("target_h3") in no_strike_hexes
        ]
        if violations:
            # 硬阻擋：移除違規 order 並升 White Cell。
            _filter_orders(output, lambda o: o.get("target_h3") not in no_strike_hexes)
            return GuardrailFinding(
                "G4",
                True,
                "打擊保護目標（No-Strike）——硬阻擋，升 White Cell",
                {"targets": violations},
            )
        return GuardrailFinding("G4", False, "無 IHL/ROE 違規")

    def _g6_quantized(self, output: dict[str, Any]) -> GuardrailFinding:
        if not self._profile.adapter_quantized:
            return GuardrailFinding("G6", False, "非量化部署，不加嚴")
        engage = [o.get("unit_id") for o in _orders_of(output) if o.get("order_type") == "ENGAGE"]
        if engage:
            return GuardrailFinding(
                "G6", True, "量化部署：ENGAGE 令改白軍逐條確認", {"units": engage}
            )
        return GuardrailFinding("G6", False, "量化部署但無 ENGAGE 令")

    def _g5_citations(
        self, output: dict[str, Any], mode: AiMode, verifier: CitationVerifier | None
    ) -> GuardrailFinding:
        cited = [c for c in output.get("cited_documents", []) or [] if isinstance(c, str)]
        empty_index = verifier is None or verifier.index_empty
        bare = mode != AiMode.AI_FULL or empty_index

        if bare:
            # AI_BARE / 空庫：引用 MUST 為空；非空即捏造 → 剔除 + 記事件。
            if cited:
                output["cited_documents"] = []
                return GuardrailFinding(
                    "G5", True, "AI_BARE/空庫下出現引用——判為捏造，已剔除", {"stripped": cited}
                )
            return GuardrailFinding("G5", False, "AI_BARE/空庫：無引用（符合）")

        # AI_FULL：逐筆查核；未過者標記並降信心度（不剔除）。
        assert verifier is not None
        unverified = [c for c in cited if not verifier.verify(c)]
        if unverified:
            conf = output.get("confidence")
            if isinstance(conf, int | float):
                output["confidence"] = round(float(conf) * 0.5, 3)
            output["citation_unverified"] = unverified
            return GuardrailFinding(
                "G5", True, "有無法查核的引用——降信心度", {"unverified": unverified}
            )
        return GuardrailFinding("G5", False, "引用全數查核通過")
