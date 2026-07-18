"""SimClock — 模擬時間的唯一來源（SPEC_FULL §3.1、HOW_TO §4.1）。

紅線：模擬邏輯 MUST NOT 依賴真實牆鐘（wall-clock）時間。一切時間取自本模組，
因此模擬可被完整重播（golden replay，SPEC_FULL §19.1）。

SimClock 只負責「目前 tick」與 tick→毫秒的換算。時間壓縮比例（0x/1x/…/60x，
SPEC_FULL §3.1）屬 Kernel 排程層——由 Kernel 決定牆鐘每過多久呼叫一次 advance()，
與模擬時間本身無關，故不放這裡。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True, order=True)
class SimTime:
    """某一 tick 的模擬時間快照（不可變值物件）。

    tick 為單調遞增整數；sim_time_ms 為自 Session 起始（tick 0）以來的模擬毫秒數。
    order=True 讓 SimTime 可直接比較先後（以 tick、sim_time_ms 依序比較）。
    """

    tick: int
    sim_time_ms: int


class SimClock:
    """模擬時鐘。以整數 tick 推進，避免浮點累積誤差破壞可重現性。

    典型用法：Kernel 每個 tick loop 迭代呼叫一次 advance()；其他所有元件只呼叫 now()
    讀取當前時間，絕不呼叫 advance()。

    checkpoint 復原（O1.5）時以 start_tick 重建到指定 tick。
    """

    def __init__(self, tick_rate_ms: int = 1000, start_tick: int = 0) -> None:
        if tick_rate_ms < 1:
            raise ValueError(f"tick_rate_ms 必須 >= 1，收到 {tick_rate_ms}")
        if start_tick < 0:
            raise ValueError(f"start_tick 必須 >= 0，收到 {start_tick}")
        self._tick_rate_ms = tick_rate_ms
        self._tick = start_tick

    @property
    def tick_rate_ms(self) -> int:
        return self._tick_rate_ms

    @property
    def tick(self) -> int:
        return self._tick

    def now(self) -> SimTime:
        """回傳當前模擬時間（唯讀）。所有子系統取時間的唯一入口。"""
        return SimTime(tick=self._tick, sim_time_ms=self._tick * self._tick_rate_ms)

    def advance(self, ticks: int = 1) -> SimTime:
        """推進模擬時間並回傳新的 SimTime。

        ⚠ 語意上只能由 Kernel 呼叫——單一時間推進者原則。Python 無法硬性強制，
        誤用會破壞 P4 可重現性，審查時須確認呼叫點僅在 Kernel tick loop。
        """
        if ticks < 1:
            raise ValueError(f"advance 的 ticks 必須 >= 1，收到 {ticks}")
        self._tick += ticks
        return self.now()
