"""檢索評測：hit-rate（WP-F1）。

規格：「ingest 每批附 5–10 條 QA 對 → `ai/evals` 增 retrieval hit-rate 指標
——**語料品質從第一天就被量測**」。

## 為什麼是 hit-rate 而不是更精緻的指標

hit@k 只問一件事：**期望的那份文件有沒有被撈進前 k 名**。它不需要相關性分級、
不需要標註者一致性、不需要一大批標註——而本專案的資料現實是語料與 eval 長期不足。
一個能在 5 條 QA 對上就給出訊號的指標，比一個要 500 條才有意義的指標有用得多。

## 空語料回 0.0 而不是拋

RAG 目前是空的，而且**那是設計前提**（`AI_BARE` 模式本來就要能跑）。
評測在空語料上回 `hit_rate=0.0, total=0` 讓 CI 看得到「還沒有語料」這個事實，
拋例外只會讓人把整個評測關掉。

⚠ **`total=0` 時不可以說「通過」**。`passed` 對空語料回 False——
「沒有東西可測」與「測了而且過了」是完全不同的兩件事，混為一談就等於自欺。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class Retriever(Protocol):
    """`(query, k) -> [doc_path, …]`（依相關性排序）。"""

    def search(self, query: str, k: int) -> list[str]: ...  # pragma: no cover


@dataclass(frozen=True, slots=True)
class RetrievalCase:
    """一條 QA 對。`expected` 是**期望被撈到的 doc_path**（可多個，命中任一即算）。"""

    query: str
    expected: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RetrievalReport:
    total: int
    hits: int
    k: int

    @property
    def hit_rate(self) -> float:
        return 0.0 if self.total == 0 else self.hits / self.total

    @property
    def passed(self) -> bool:
        """⚠ **空語料不算通過**（見模組說明）。"""
        return self.total > 0 and self.hit_rate >= MIN_HIT_RATE


# v0 門檻。低是刻意的——語料從零開始長，一開始就設 0.8 只會逼人關掉這個關卡。
MIN_HIT_RATE = 0.5


def evaluate_retrieval(
    retriever: Retriever, cases: list[RetrievalCase], k: int = 5
) -> RetrievalReport:
    """跑 hit@k。任一 `expected` 出現在前 k 名即算命中。

    檢索本身丟例外 → **算未命中而不是往上拋**：一條壞 query 不該讓整批評測沒有結果。
    """
    hits = 0
    for case in cases:
        try:
            got = retriever.search(case.query, k)
        except Exception:
            continue
        if any(doc in got[:k] for doc in case.expected):
            hits += 1
    return RetrievalReport(total=len(cases), hits=hits, k=k)


def load_cases(raw: list[dict[str, Any]]) -> list[RetrievalCase]:
    """JSON → QA 對。形狀不對的條目**跳過而不是拋**（一條壞資料不該擋掉整批）。"""
    out: list[RetrievalCase] = []
    for item in raw:
        query = item.get("query")
        expected = item.get("expected")
        if not isinstance(query, str) or not query:
            continue
        if isinstance(expected, str):
            expected = [expected]
        if not isinstance(expected, list) or not expected:
            continue
        out.append(RetrievalCase(query=query, expected=tuple(str(e) for e in expected)))
    return out


__all__ = [
    "MIN_HIT_RATE",
    "RetrievalCase",
    "RetrievalReport",
    "Retriever",
    "evaluate_retrieval",
    "load_cases",
]
