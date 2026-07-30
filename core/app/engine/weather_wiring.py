"""天氣的逐 tick 刷新（WP-C4b）。

## 動手前查證：規格的三個前提有兩個已經成立

SPEC_V2 寫「天氣是 session 啟動單一快照」，並把本卡標成 **golden：重錄**。查證後：

1. **插件的 RPC 早就是 tick-aware 的**——`GetWeatherRequest.sim_tick` 存在，
   `WeatherClient.fetch_state(sim_tick)` 也收 tick。**是 core 只呼叫了一次，永遠傳 0。**
   缺的不是能力，是「每隔 N tick 再問一次」這件事。
2. **風場也早就在契約裡**（`WeatherCell.wind_ms` / `wind_dir_deg`，proto 欄位 3/4）
   ——是 core 的 `_cell_from_proto` 只讀 `effects` 把它丟掉了。已一併修好。
3. **golden 不必重錄**：刷新間隔預設 0 ＝**永不刷新**＝既有的單一快照行為，
   一個位元都不差。與 C1/C3/C4a 同一招。想定/參數要主動開才會變。

## 為什麼是 `weather_for` 回呼而不是換一份快照

`make_engage_env` / `make_detect_env` 的閉包在 Kernel 建構時就固定了。傳一份 `WeatherState`
進去，整局就永遠停在那一份——**那正是本卡要修的病**。所以與 WP-C4a 的 `light_for`、
WP-C4c 的 `smoke_for` 一樣走回呼。

## 決定性

同一個 `sim_tick` 問插件必須得到同一份答案（SYNTHETIC 由 seed 派生），否則重播會漂。
本模組**只快取不加工**：把「這一 tick 的天氣」問一次、同 tick 內共用，不做任何內插或平滑
——那些會讓 core 變成第二個氣象模型，而 core 的原則是「不解讀氣象學」。
"""

from __future__ import annotations

import logging
from typing import Any

from app.weather import WeatherState

_LOG = logging.getLogger(__name__)

# 0 ＝永不刷新（＝既有的「整局一份啟動快照」）。**這是中性預設。**
DEFAULT_REFRESH_TICKS = 0


class WeatherCache:
    """每 `refresh_ticks` 個 tick 重問一次天氣；期間共用同一份快照。

    `fetch` 是 `(sim_tick) -> WeatherState | None` 的取得器（正式路徑是 gRPC client）。
    取不到 → **沿用上一份**而不是退回晴天：天氣服務抖一下就讓全場忽然放晴，
    比慢一拍嚴重得多（同 `has_los` 服務中斷不致盲的退化紀律）。
    """

    def __init__(
        self,
        fetch: Any,
        *,
        refresh_ticks: int = DEFAULT_REFRESH_TICKS,
        initial: WeatherState | None = None,
    ) -> None:
        self._fetch = fetch
        self._refresh_ticks = max(0, int(refresh_ticks))
        self._state = initial
        self._fetched_at: int | None = 0 if initial is not None else None
        # 上次回報過的過期狀態。**以 False 起始**——開局就拿到過期資料時要報一次。
        self._was_stale = False

    @property
    def stale(self) -> bool:
        """目前這份快照是不是過期資料（插件說的）。無快照 → False。"""
        return bool(getattr(self._state, "stale", False))

    def take_stale_change(self) -> bool | None:
        """過期狀態**有變**才回新值，否則 None。

        `weather_payload.schema.json` 要求「stale > 30 分鐘 Core 需告警」，插件也照實回報
        （`WeatherClient` 有把 `resp.stale` 帶進 `WeatherState`）——但 `WeatherState.stale`
        除了定義處**全 repo 零讀取端**：來源斷線超過半小時，白軍畫面上什麼提示都沒有，
        命中率/機動修正繼續套一份不知道多舊的天氣。

        只回「變化」而不是「現在是不是」：每 tick 報一次同一件事只會把戰況 feed 灌爆。
        """
        now = self.stale
        if now == self._was_stale:
            return None
        self._was_stale = now
        return now

    @property
    def refreshes(self) -> bool:
        """會不會刷新。False ＝既有行為（整局一份快照），呼叫端可以整段跳過。"""
        return self._refresh_ticks > 0

    def at(self, tick: int) -> WeatherState | None:
        if not self.refreshes:
            return self._state
        due = self._fetched_at is None or tick - self._fetched_at >= self._refresh_ticks
        if due:
            fresh = self._safe_fetch(tick)
            if fresh is not None:
                self._state = fresh
            # **取不到也要更新時間戳**，否則每個 tick 都會重試一次失敗的 RPC，
            # 把 tick 預算耗在一個已知不可用的服務上。
            self._fetched_at = tick
        return self._state

    def _safe_fetch(self, tick: int) -> WeatherState | None:
        try:
            return self._fetch(tick)  # type: ignore[no-any-return]
        except Exception:
            _LOG.warning("天氣刷新失敗（tick=%s），沿用上一份快照", tick)
            return None


__all__ = ["DEFAULT_REFRESH_TICKS", "WeatherCache"]
