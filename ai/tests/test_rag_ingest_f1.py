"""WP-F1：bge-m3 惰性載入、降級可見、manifest 留痕、檢索 hit-rate。"""

from __future__ import annotations

import json

from matso_ai.evals.retrieval import (
    MIN_HIT_RATE,
    RetrievalCase,
    RetrievalReport,
    evaluate_retrieval,
    load_cases,
)
from matso_ai.rag.embedder import (
    BGE_M3_PATH_ENV,
    HashEmbedder,
    describe_embedder,
    load_bge_m3,
)

# ---- 惰性載入：取不到一律回 None ----


def test_no_model_path_configured_degrades_silently_to_none(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """模型不在是**部署現實**不是程式錯誤——本專案的 RAG 語料長期不足是設計前提，
    `AI_BARE` 模式本來就要能跑。回 None 讓呼叫端**明確地**決定降級。"""
    monkeypatch.delenv(BGE_M3_PATH_ENV, raising=False)
    assert load_bge_m3() is None


def test_a_bad_model_path_does_not_raise(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """載入失敗也回 None——拋例外會讓 ingest CLI 在沒有模型的機器上根本跑不起來。"""
    monkeypatch.setenv(BGE_M3_PATH_ENV, "/nonexistent/bge-m3")
    assert load_bge_m3() is None


# ---- 降級一定要看得見 ----


def test_degradation_is_reported_not_silent() -> None:
    """**無聲降級會讓人以為檢索品質正常而信任它的引用**。"""
    described = describe_embedder(HashEmbedder(dim=8))
    assert described["degraded"] is True
    assert "降級" in described["note"]


def test_a_real_embedder_is_not_marked_degraded() -> None:
    class _Fake:
        dim = 1024

        def embed(self, texts):  # type: ignore[no-untyped-def]
            return [[0.0] * 1024 for _ in texts]

    described = describe_embedder(_Fake())  # type: ignore[arg-type]
    assert described["degraded"] is False
    assert described["note"] == ""


# ---- manifest 留痕 ----


def test_the_manifest_records_which_embedder_was_used(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """事後看到一批品質差的檢索結果時，沒有這一欄就無從判斷是語料不好還是嵌入器降級了。"""
    from matso_ai.rag.chunker import chunk_markdown
    from matso_ai.rag.ingest import write_manifest

    corpus = tmp_path / "corpus"
    (corpus / "doctrine_general").mkdir(parents=True)
    (corpus / "doctrine_general" / "a.md").write_text("# 標題\n內容\n", encoding="utf-8")
    chunks = chunk_markdown("# 標題\n內容\n", "doctrine_general/a.md")

    path = write_manifest(corpus, chunks, HashEmbedder(dim=8))
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert manifest["embedder"]["degraded"] is True
    assert manifest["chunk_count"] == len(chunks)
    assert manifest["sources"][0]["path"] == "doctrine_general/a.md"


def test_the_cli_falls_back_instead_of_failing(tmp_path, capsys, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`--embedder bge-m3` 取不到模型 → **降級而不是失敗**，而且 stdout 明說。"""
    from matso_ai.rag.ingest import main

    monkeypatch.delenv(BGE_M3_PATH_ENV, raising=False)
    corpus = tmp_path / "corpus"
    (corpus / "doctrine_general").mkdir(parents=True)
    (corpus / "doctrine_general" / "a.md").write_text("# 標題\n內容\n", encoding="utf-8")
    assert main([str(corpus), "--embedder", "bge-m3", "--dim", "8"]) == 0
    assert "降級" in capsys.readouterr().out


# ---- 檢索 hit-rate ----


class _Retriever:
    def __init__(self, results: dict[str, list[str]]) -> None:
        self._results = results

    def search(self, query: str, k: int) -> list[str]:
        return self._results.get(query, [])[:k]


def test_hit_rate_counts_a_case_as_hit_when_any_expected_doc_is_in_top_k() -> None:
    cases = [
        RetrievalCase("問甲", ("a.md",)),
        RetrievalCase("問乙", ("b.md", "c.md")),
        RetrievalCase("問丙", ("z.md",)),
    ]
    retriever = _Retriever({"問甲": ["a.md"], "問乙": ["x.md", "c.md"], "問丙": ["y.md"]})
    report = evaluate_retrieval(retriever, cases, k=5)
    assert report.total == 3 and report.hits == 2


class _IgnoresK:
    """**不理會 k** 的檢索器——`evaluate_retrieval` 自己那道 `[:k]` 就是防這個。

    ⚠ 我第一版用的 fake 自己就先 `[:k]` 了，所以拿掉 `evaluate_retrieval` 的切片
    測試照樣綠（突變測試抓出來的）。要驗到那道防護，fake 必須**真的**回超過 k 筆。
    """

    def __init__(self, results: list[str]) -> None:
        self._results = results

    def search(self, query: str, k: int) -> list[str]:
        return list(self._results)


def test_a_doc_below_k_does_not_count() -> None:
    cases = [RetrievalCase("q", ("target.md",))]
    retriever = _IgnoresK(["a.md", "b.md", "target.md"])
    assert evaluate_retrieval(retriever, cases, k=2).hits == 0
    assert evaluate_retrieval(retriever, cases, k=3).hits == 1


def test_an_empty_corpus_reports_zero_and_does_not_claim_success() -> None:
    """⚠ **`total=0` 時不可以說「通過」**——「沒有東西可測」與「測了而且過了」
    是完全不同的兩件事，混為一談就等於自欺。"""
    report = evaluate_retrieval(_Retriever({}), [], k=5)
    assert report.hit_rate == 0.0
    assert report.passed is False


def test_a_broken_query_counts_as_a_miss_not_a_crash() -> None:
    """一條壞 query 不該讓整批評測沒有結果。"""

    class _Exploding:
        def search(self, query: str, k: int) -> list[str]:
            raise RuntimeError("qdrant down")

    assert evaluate_retrieval(_Exploding(), [RetrievalCase("q", ("a.md",))], k=5).hits == 0


def test_the_threshold_is_deliberately_low_to_start() -> None:
    """語料從零開始長，一開始就設 0.8 只會逼人關掉這個關卡。"""
    assert 0.0 < MIN_HIT_RATE <= 0.6
    assert RetrievalReport(total=10, hits=5, k=5).passed is True
    assert RetrievalReport(total=10, hits=4, k=5).passed is False


def test_malformed_cases_are_skipped_not_fatal() -> None:
    raw = [
        {"query": "good", "expected": "a.md"},
        {"query": "", "expected": ["b.md"]},
        {"expected": ["c.md"]},
        {"query": "no-expected"},
        {"query": "list", "expected": ["d.md", "e.md"]},
    ]
    cases = load_cases(raw)
    assert [c.query for c in cases] == ["good", "list"]
    assert cases[0].expected == ("a.md",)
