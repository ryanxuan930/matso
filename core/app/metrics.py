"""執行期指標（WP-E4）——Prometheus 文字格式，**零外部相依**。

## 為什麼自己寫而不是 `prometheus_client`

使用者選了「不加容器」的方案，理由之一是 air-gapped 部署每多一個相依就多一件要打包的事。
exposition 格式本身很簡單（counter/gauge/histogram 各三行結構），而**自己寫換到的是
完全的可控性**：不會有某個版本忽然改變 `_created` 系列或預設 collector 的行為。
代價是要自己把格式寫對——所以有一組測試逐字釘住輸出。

## 為什麼是行程內註冊表

`SimManager` 與 FastAPI **跑在同一個行程**（`main.py` 的 lifespan 啟動它），
所以 tick 量測與 `/metrics` 端點共用記憶體，不需要 Redis 之類的載體。

⚠ 這是一個**前提**不是巧合。哪天 runner 被拆成獨立行程，這個模組就會安靜地只回報
API 行程看得到的那部分（tick 指標全部歸零）。真的要拆的話，載體要一起改
——這行註解就是留給那時候的人看的。

## 不放 per-session 標籤

兩個獨立理由，任一個都足夠：
1. **基數爆炸**：每開一局就多一組時間序列，Prometheus 會被長期堆積的 session id 拖垮。
2. **`/metrics` 通常不驗證身分**（Prometheus 直接 scrape）。把 session id 放進去等於
   把「有哪些推演正在跑」公開出去。指標要能回答「系統健康嗎」，不必回答「誰在打誰」。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

# tick 預算預設 200ms（SPEC_FULL §18）——分桶跨過它，讓「接近超時」與「已經超時」分得開。
TICK_BUCKETS_MS: tuple[float, ...] = (5, 10, 25, 50, 100, 150, 200, 300, 500, 1000)
# LLM 心跳是秒級的，與 tick 完全不同量級，故各用一組桶。
LLM_BUCKETS_MS: tuple[float, ...] = (250, 500, 1000, 2500, 5000, 10000, 30000, 60000)
# DB/Redis 一次操作的量級。
IO_BUCKETS_MS: tuple[float, ...] = (1, 5, 10, 25, 50, 100, 250, 1000)


@dataclass
class _Counter:
    help: str
    value: float = 0.0

    def inc(self, amount: float = 1.0) -> None:
        self.value += amount


@dataclass
class _Gauge:
    help: str
    value: float = 0.0

    def set(self, value: float) -> None:
        self.value = value


@dataclass
class _Histogram:
    help: str
    buckets: tuple[float, ...]
    counts: list[int] = field(default_factory=list)
    total: float = 0.0
    count: int = 0

    def __post_init__(self) -> None:
        if not self.counts:
            self.counts = [0] * len(self.buckets)

    def observe(self, value: float) -> None:
        """記一次觀測。

        ⚠ `counts[i]` 存的是**累積值**（≤ buckets[i] 的觀測數），因為 Prometheus 的
        `le` 桶本來就是累積的。所以這裡對每個 `value <= upper` 的桶都 +1，
        **render 不可以再累加一次**——我第一版就是兩邊都累加，於是桶數超過 `_count`，
        算出來的分位數會是錯的（而且錯得看起來很合理：曲線仍然單調遞增）。
        """
        self.total += value
        self.count += 1
        for i, upper in enumerate(self.buckets):
            if value <= upper:
                self.counts[i] += 1


class MetricsRegistry:
    """極小的指標註冊表。**執行緒安全**——tick 迴圈與 scrape 不在同一條路徑上。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, _Counter] = {}
        self._gauges: dict[str, _Gauge] = {}
        self._histograms: dict[str, _Histogram] = {}

    # ---- 註冊/取用 ----

    def counter(self, name: str, help_text: str = "") -> _Counter:
        with self._lock:
            return self._counters.setdefault(name, _Counter(help=help_text))

    def gauge(self, name: str, help_text: str = "") -> _Gauge:
        with self._lock:
            return self._gauges.setdefault(name, _Gauge(help=help_text))

    def histogram(self, name: str, buckets: tuple[float, ...], help_text: str = "") -> _Histogram:
        with self._lock:
            return self._histograms.setdefault(name, _Histogram(help=help_text, buckets=buckets))

    # ---- 輸出 ----

    def render(self) -> str:
        """Prometheus 文字格式。**依名稱排序**——輸出穩定，diff 才看得出變化。"""
        with self._lock:
            lines: list[str] = []
            for name, counter in sorted(self._counters.items()):
                lines += _typed(name, "counter", counter.help)
                lines.append(f"{name} {_num(counter.value)}")
            for name, gauge in sorted(self._gauges.items()):
                lines += _typed(name, "gauge", gauge.help)
                lines.append(f"{name} {_num(gauge.value)}")
            for name, hist in sorted(self._histograms.items()):
                lines += _typed(name, "histogram", hist.help)
                # `counts` 已是累積值（見 `_Histogram.observe`）——**不要再累加**。
                for upper, count in zip(hist.buckets, hist.counts, strict=True):
                    lines.append(f'{name}_bucket{{le="{_num(upper)}"}} {count}')
                # +Inf 桶**必須等於 _count**，否則 Prometheus 算出來的分位數是錯的。
                lines.append(f'{name}_bucket{{le="+Inf"}} {hist.count}')
                lines.append(f"{name}_sum {_num(hist.total)}")
                lines.append(f"{name}_count {hist.count}")
            return "\n".join(lines) + "\n"

    def reset(self) -> None:
        """僅供測試——正式執行期的指標是單調的。"""
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()


def _typed(name: str, kind: str, help_text: str) -> list[str]:
    out = []
    if help_text:
        out.append(f"# HELP {name} {help_text}")
    out.append(f"# TYPE {name} {kind}")
    return out


def _num(value: float) -> str:
    """整數不輸出小數點（Prometheus 兩者都收，但整數比較好讀）。"""
    return str(int(value)) if float(value).is_integer() else repr(float(value))


# 全域註冊表。單一行程（見模組說明），故一份即可。
REGISTRY = MetricsRegistry()


# ---- 具名指標（集中在此，避免名稱散落各處拼錯） ----


def tick_duration(ms: float) -> None:
    REGISTRY.histogram(
        "matso_tick_duration_ms", TICK_BUCKETS_MS, "Kernel 單 tick 牆鐘時長"
    ).observe(ms)


def tick_overrun() -> None:
    REGISTRY.counter("matso_tick_overrun_total", "超出 tick 預算的次數").inc()


def tick_completed() -> None:
    REGISTRY.counter("matso_tick_total", "已完成的 tick 數").inc()


def ws_fanout(recipients: int) -> None:
    REGISTRY.counter("matso_ws_fanout_total", "WS 事件扇出總則數").inc(recipients)


def llm_latency(ms: float) -> None:
    REGISTRY.histogram("matso_llm_latency_ms", LLM_BUCKETS_MS, "LLM 單次呼叫延遲").observe(ms)


def guardrail_blocked(count: int = 1) -> None:
    REGISTRY.counter("matso_guardrail_blocked_total", "護欄攔截次數").inc(count)


def io_latency(ms: float) -> None:
    REGISTRY.histogram("matso_io_latency_ms", IO_BUCKETS_MS, "DB/Redis 單次操作延遲").observe(ms)


def active_sessions(count: int) -> None:
    REGISTRY.gauge("matso_active_sessions", "執行中的推演局數").set(count)


def ai_workers(count: int) -> None:
    REGISTRY.gauge("matso_ai_workers", "執行中的 AI 決策 worker 數").set(count)


__all__ = [
    "IO_BUCKETS_MS",
    "LLM_BUCKETS_MS",
    "REGISTRY",
    "TICK_BUCKETS_MS",
    "MetricsRegistry",
    "active_sessions",
    "ai_workers",
    "guardrail_blocked",
    "io_latency",
    "llm_latency",
    "tick_completed",
    "tick_duration",
    "tick_overrun",
    "ws_fanout",
]
