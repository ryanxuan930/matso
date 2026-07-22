"""執行期（runtime）層——真實牆鐘、tick 節奏控制與自動降頻。

刻意置於 engine 之外：engine 內任何模組皆不得依賴真實牆鐘（P4 / 紅線）。
- PerfCounterClock：供 Kernel 量測 tick 耗時（TICK_OVERRUN 判定）。
- TickPacer + run_paced：牆鐘節奏 + **自動降頻**（SPEC_FULL §3.3：超出預算 MUST 降頻，
  不得靜默丟事件——Kernel 已保證不丟事件，本模組補上降頻）。

降頻規則（O1.7/R6）：
1. 天然節流：每 tick 的等待 = max(額定間隔 − 實際耗時, 0)——tick 比額定慢時不再累積欠帳。
2. 持續過載退避：連續 overrun 達門檻 → 額定間隔乘上 backoff 係數（上限 max_slowdown）；
   恢復正常後逐步衰減回 1.0。避免系統在長期過載下空轉發 tick。
時間壓縮（0x/1x/…/60x，White Cell 控制）由 compression 參數表達；0 = 暫停（由 caller 停止迴圈）。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

from app.engine.kernel import Kernel, TickReport

_NS_PER_S = 1_000_000_000


class PerfCounterClock:
    """以 time.perf_counter_ns() 提供單調牆鐘，供 Kernel 量測 tick 運算耗時。"""

    def now_ns(self) -> int:
        return time.perf_counter_ns()


class TickPacer:
    """依 TickReport 計算下一個 tick 前的等待秒數，內建自動降頻。"""

    def __init__(
        self,
        tick_rate_ms: int,
        *,
        compression: float = 1.0,
        backoff_after: int = 3,
        backoff_factor: float = 2.0,
        max_slowdown: float = 8.0,
    ) -> None:
        if tick_rate_ms < 1:
            raise ValueError(f"tick_rate_ms 必須 >= 1，收到 {tick_rate_ms}")
        if compression <= 0:
            raise ValueError(
                f"compression 必須 > 0（0x 暫停由 caller 停止迴圈），收到 {compression}"
            )
        self._nominal_s = tick_rate_ms / 1000.0 / compression
        self._backoff_after = backoff_after
        self._backoff_factor = backoff_factor
        self._max_slowdown = max_slowdown
        self._consecutive_overruns = 0
        self._slowdown = 1.0

    @property
    def slowdown(self) -> float:
        """目前降頻倍率（1.0 = 額定節奏）。觀測用（metrics）。"""
        return self._slowdown

    def next_delay_s(self, report: TickReport) -> float:
        if report.overran:
            self._consecutive_overruns += 1
            if self._consecutive_overruns >= self._backoff_after:
                self._slowdown = min(self._slowdown * self._backoff_factor, self._max_slowdown)
        else:
            self._consecutive_overruns = 0
            self._slowdown = max(1.0, self._slowdown / self._backoff_factor)
        interval_s = self._nominal_s * self._slowdown
        elapsed_s = report.duration_ns / _NS_PER_S
        return max(interval_s - elapsed_s, 0.0)


async def run_paced(
    kernel: Kernel,
    pacer: TickPacer,
    *,
    ticks: int | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    should_stop: Callable[[], bool] | None = None,
    should_pause: Callable[[], bool] | None = None,
    pause_poll_s: float = 0.25,
) -> int:
    """以牆鐘節奏連續執行 tick。ticks=None 表示跑到 should_stop 為 True。回傳執行的 tick 數。

    `should_pause`（新 #6）：White Cell 暫停旗標。為 True 時凍結——不推進 tick、不消耗 ticks 配額，
    每 `pause_poll_s` 輪詢一次直到解除或 should_stop。使白軍控制台的暫停/續行真正作用於活模擬。
    """
    if ticks is None and should_stop is None:
        raise ValueError("ticks=None 時必須提供 should_stop，否則迴圈無法終止")
    count = 0
    while ticks is None or count < ticks:
        if should_stop is not None and should_stop():
            break
        if should_pause is not None and should_pause():
            await sleep(pause_poll_s)  # 暫停中：凍結、輪詢，不推進 tick
            continue
        report = await kernel.run_tick()
        count += 1
        await sleep(pacer.next_delay_s(report))
    return count
