"""執行期（runtime）層——真實牆鐘與行程級裝配。

刻意置於 engine 之外：engine 內任何模組皆不得依賴真實牆鐘（P4 / 紅線），
而 tick 效能量測（TICK_OVERRUN 判定）需要真實單調時間，故其實作放在這裡並注入 Kernel。
"""

from __future__ import annotations

import time


class PerfCounterClock:
    """以 time.perf_counter_ns() 提供單調牆鐘，供 Kernel 量測 tick 運算耗時。"""

    def now_ns(self) -> int:
        return time.perf_counter_ns()
