"""執行期指標（WP-E4）：exposition 格式、直方圖正確性、不洩漏 session。

自己寫 exposition 格式的代價就是要自己把它寫對——**這一組測試是那個代價**。
"""

from __future__ import annotations

import re

import pytest

from app.metrics import (
    TICK_BUCKETS_MS,
    MetricsRegistry,
)


@pytest.fixture
def reg() -> MetricsRegistry:
    return MetricsRegistry()


def _buckets(out: str, name: str) -> list[int]:
    """明確桶的計數（**不含 `+Inf`**）。

    ⚠ 我第一版的 regex 把 `+Inf` 也撈進來了，於是每個斷言都多一個元素。
    那是測試的 bug 不是程式的——但它會讓人第一眼以為直方圖又壞了。
    """
    return [
        int(v)
        for le, v in re.findall(rf"{name}_bucket\{{le=\"([^\"]+)\"\}} (\d+)", out)
        if le != "+Inf"
    ]


# ---- 直方圖：這是最容易寫錯的一塊 ----


def test_bucket_counts_are_cumulative_and_monotonic(reg: MetricsRegistry) -> None:
    """⚠ **我第一版把累積做了兩次**（observe 累積、render 又累積一次），
    於是桶數超過 `_count`，Prometheus 算出來的分位數是錯的
    ——而且錯得看起來很合理：曲線仍然單調遞增。"""
    hist = reg.histogram("h", (10.0, 100.0, 1000.0))
    for value in (5.0, 50.0, 500.0):
        hist.observe(value)
    counts = _buckets(reg.render(), "h")
    assert counts == sorted(counts), "桶必須單調遞增"
    assert counts == [1, 2, 3]


def test_the_inf_bucket_equals_the_count(reg: MetricsRegistry) -> None:
    """`+Inf` 桶不等於 `_count` 的話，分位數計算直接是錯的。"""
    hist = reg.histogram("h", (1.0, 2.0))
    for value in (0.5, 1.5, 99.0):  # 99 落在所有明確桶之外
        hist.observe(value)
    out = reg.render()
    inf = int(re.search(r'h_bucket\{le="\+Inf"\} (\d+)', out).group(1))
    count = int(re.search(r"h_count (\d+)", out).group(1))
    assert inf == count == 3


def test_sum_and_count_track_observations(reg: MetricsRegistry) -> None:
    hist = reg.histogram("h", (1000.0,))
    for value in (1.5, 2.5):
        hist.observe(value)
    out = reg.render()
    assert "h_sum 4" in out and "h_count 2" in out  # 整數不輸出小數點


def test_a_value_on_a_bucket_boundary_counts_in_that_bucket(reg: MetricsRegistry) -> None:
    """`le` 是「小於等於」——邊界值算在裡面。差一個 `=` 就是差一個桶。"""
    hist = reg.histogram("h", (10.0, 20.0))
    hist.observe(10.0)
    assert _buckets(reg.render(), "h") == [1, 1]


def test_an_empty_histogram_still_renders_valid_output(reg: MetricsRegistry) -> None:
    reg.histogram("h", (1.0, 2.0))
    out = reg.render()
    assert "h_count 0" in out
    assert _buckets(out, "h") == [0, 0]


# ---- 格式 ----


def test_each_metric_declares_its_type(reg: MetricsRegistry) -> None:
    reg.counter("c", "說明").inc()
    reg.gauge("g", "說明").set(2)
    reg.histogram("h", (1.0,), "說明").observe(0.5)
    out = reg.render()
    assert "# TYPE c counter" in out
    assert "# TYPE g gauge" in out
    assert "# TYPE h histogram" in out
    assert out.count("# HELP") == 3


def test_output_is_sorted_so_diffs_are_readable(reg: MetricsRegistry) -> None:
    reg.counter("zebra").inc()
    reg.counter("alpha").inc()
    out = reg.render()
    assert out.index("alpha") < out.index("zebra")


def test_output_ends_with_a_newline(reg: MetricsRegistry) -> None:
    """Prometheus 的 exposition 格式要求最後一行有換行。"""
    reg.counter("c").inc()
    assert reg.render().endswith("\n")


def test_integers_render_without_a_decimal_point(reg: MetricsRegistry) -> None:
    reg.counter("c").inc(3)
    assert "\nc 3\n" in reg.render()


# ---- 不洩漏 ----


def test_no_metric_name_carries_a_session_or_unit_label() -> None:
    """**兩個獨立理由**：基數爆炸，以及 `/metrics` 通常不驗證身分——
    把 session id 放進去等於把「有哪些推演正在跑」公開出去。"""
    from app import metrics as m

    m.tick_duration(1.0)
    m.ws_fanout(1)
    m.llm_latency(1.0)
    m.guardrail_blocked()
    m.active_sessions(1)
    out = m.REGISTRY.render()
    # 唯一合法的標籤是直方圖的 `le`。
    labels = set(re.findall(r"\{(\w+)=", out))
    assert labels <= {"le"}, f"出現了不該有的標籤：{labels - {'le'}}"


def test_tick_buckets_straddle_the_default_budget() -> None:
    """分桶要跨過 tick 預算（200ms），否則「接近超時」與「已經超時」分不開。"""
    assert 200 in TICK_BUCKETS_MS
    assert min(TICK_BUCKETS_MS) < 200 < max(TICK_BUCKETS_MS)


# ---- 端點 ----


def test_the_metrics_endpoint_serves_prometheus_text(session_factory) -> None:  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        r = client.get("/metrics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")


def test_the_metrics_endpoint_is_not_in_the_openapi_schema() -> None:
    """這不是給前端用的 API，不該出現在契約裡。"""
    from app.main import app

    assert "/metrics" not in app.openapi()["paths"]
