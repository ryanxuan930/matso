"""AI 敘事報告（O8.3，SPEC_FULL §14.2）——AAR_ANALYST 從 Ledger 產敘事，逐段引用 event seq。

**引用查核**：報告引用的 seq MUST 全部存在於 Ledger（§14.2「逐段引用 event id 供查證」，防杜撰）。
無真模型時以 fallback 由統計 + 書籤產結構化敘事（只引真實 seq）；真模型接線屬部署層。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from app.aar.events import AarEvent
from app.aar.replay import bookmarks
from app.aar.stats import compute_metrics


@dataclass(frozen=True, slots=True)
class NarrativeParagraph:
    text: str
    cited_seqs: list[int] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class AarNarrative:
    summary: str
    paragraphs: list[NarrativeParagraph]
    lessons: list[str]

    @property
    def all_cited_seqs(self) -> list[int]:
        return [s for p in self.paragraphs for s in p.cited_seqs]


Narrator = Callable[[Sequence[AarEvent]], AarNarrative]


def fallback_narrative(events: Sequence[AarEvent]) -> AarNarrative:
    """無真模型時的結構化敘事：由統計 + 關鍵書籤生成，只引真實 seq。"""
    m = compute_metrics(events)
    bms = bookmarks(events)
    paragraphs: list[NarrativeParagraph] = [
        NarrativeParagraph(
            text=f"本場推演共 {m.total_events} 起事件、{m.engagements} 次交戰，"
            f"最終推進至 tick {m.max_tick}。",
            cited_seqs=[],
        )
    ]
    # 關鍵轉折點：取前幾個書籤事件並引用其 seq。
    for bm in bms[:5]:
        paragraphs.append(
            NarrativeParagraph(text=f"tick {bm.tick}：{bm.label}。", cited_seqs=[bm.seq])
        )
    lessons = []
    if m.guardrail_blocks:
        lessons.append(f"護欄攔截 {m.guardrail_blocks} 次——AI 輸出需檢視（sustain 護欄機制）。")
    if m.hit_rate:
        lessons.append(f"命中率 {m.hit_rate:.0%}——火力效益可作後續改進基準。")
    if not lessons:
        lessons.append("樣本不足以提煉教訓（事件過少）。")
    return AarNarrative(
        summary=f"{m.engagements} 次交戰、承受總戰損 {m.total_damage}。",
        paragraphs=paragraphs,
        lessons=lessons,
    )


def generate_narrative(
    events: Sequence[AarEvent], narrator: Narrator | None = None
) -> AarNarrative:
    return (narrator or fallback_narrative)(events)


def verify_citations(narrative: AarNarrative, events: Sequence[AarEvent]) -> list[int]:
    """回傳**不存在**於 Ledger 的引用 seq（空＝全部有效）。§14.2 引用查核。"""
    valid = {e.seq for e in events}
    return [s for s in narrative.all_cited_seqs if s not in valid]
