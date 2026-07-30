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


def test_every_declared_metric_has_a_writer() -> None:
    """**宣告了就要有人寫**——沒有寫入端的指標比沒有指標更糟。

    儀表板上一條永遠是 0 的線會被讀成「這件事沒發生」，而真相是「這件事沒被量」。
    `matso_io_latency_ms` 與 `matso_ai_workers` 就這樣躺了一陣子（已記 Backlog 並補上）。

    掃 `core/app/` **與 `ai/`** 兩棵樹——`llm_latency` 的唯一寫入端在
    `matso_ai/inference/role_manager.py`，只掃 `app/` 會把它誤報成沒接線。
    這條在**新增指標卻忘了接線**時轉紅。
    """
    import ast
    import pathlib

    import app.metrics as m

    app_dir = pathlib.Path(m.__file__).parent
    ai_dir = app_dir.parents[1] / "ai"
    assert ai_dir.is_dir(), f"找不到 ai/ 樹（{ai_dir}）——路徑推導壞了，這條會變成假綠"
    helpers = {
        name
        for name in m.__all__
        if callable(getattr(m, name)) and not name[0].isupper() and name != "MetricsRegistry"
    }
    called: set[str] = set()
    for path in [*app_dir.rglob("*.py"), *ai_dir.rglob("*.py")]:
        if path.name == "metrics.py":
            continue  # 定義處不算呼叫端
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                if isinstance(fn, ast.Attribute):
                    called.add(fn.attr)
                elif isinstance(fn, ast.Name):
                    called.add(fn.id)
    unwired = helpers - called
    assert not unwired, f"這些指標宣告了卻沒有寫入端：{sorted(unwired)}"


def test_the_ledger_write_is_timed_separately_from_the_tick() -> None:
    """`io_latency` 要量**帳本寫入**，不能併進 `tick_duration`。

    合在一起的話「tick 超時」永遠分不出是算太久還是 DB 慢，而這兩者的處置完全不同
    （減負載 vs 修連線池）。所以 kernel 裡 `metrics.io_latency` 必須出現在
    `event_sink.append` 之後、且不在 `tick_duration` 的量測區間內。
    """
    import pathlib

    from app.engine import kernel as k

    src = pathlib.Path(k.__file__).read_text(encoding="utf-8")
    tick_at = src.index("metrics.tick_duration(")
    append_at = src.index("self._event_sink.append")
    io_at = src.index("metrics.io_latency(")
    assert tick_at < append_at < io_at, "io_latency 必須在帳本寫入之後量，且不含在 tick 量測內"


def test_gauges_are_registered_at_zero_before_anything_happens() -> None:
    """gauge 要在 SimManager 一啟動就存在，即使值是 0。

    指標是**第一次寫入時才出現**的。Prometheus 裡「沒有這條序列」不等於「值是 0」：
    沒有 AI 指派的局會讓 `matso_ai_workers` 整條消失，儀表板呈現的是斷線而不是
    「沒有 AI 在跑」，`ops/monitoring/alerts.yml` 的規則也跟著失效。
    """
    import asyncio

    from app import metrics as m
    from app.sim_runtime import SimManager

    m.REGISTRY.reset()
    mgr = SimManager.__new__(SimManager)  # 不碰 Redis/DB：只驗 run() 開頭的登記行為
    mgr._stop = asyncio.Event()  # type: ignore[attr-defined]
    mgr._tasks = {}  # type: ignore[attr-defined]
    mgr._ai_workers = {}  # type: ignore[attr-defined]
    mgr._scan_interval = 0.0  # type: ignore[attr-defined]

    def _stop_after_first_scan() -> list[str]:
        # ⚠ 不能事先 `_stop.set()`——`run()` 的**第一行就是 `_stop.clear()`**，
        # 預先設好的旗標會被清掉，迴圈永遠不結束（我第一版就是這樣掛住的）。
        mgr._stop.set()  # type: ignore[attr-defined]
        return []

    mgr._session_ids = _stop_after_first_scan  # type: ignore[attr-defined]
    asyncio.run(mgr.run())

    out = m.REGISTRY.render()
    assert "matso_ai_workers 0" in out, "沒有 AI 指派時 matso_ai_workers 必須是 0 而不是缺席"
    assert "matso_active_sessions 0" in out
