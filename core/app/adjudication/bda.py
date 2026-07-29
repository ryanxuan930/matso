"""戰果評估（BDA，WP-C10.4b）——**這是情報，不是事實**。

整個模組存在的理由只有一句話：**觀測者回報的戰果不等於真實戰損**。
如果 BDA 回傳真值，那它就不是 BDA，只是把帳本換個名字再印一次。

紅線 2：純同步純函數。紅線 1：誤差一律走 `DeterministicRNG`，
且**用獨立的 `"bda"` stream**——與落點共用一條的話，「這次有沒有前觀」會決定抽樣次數，
於是前觀死不死會改變後續每一發砲彈的落點。狀態相依的抽樣次數正是 `rng.py` 警告的耦合。

**只有觀測到才有 BDA**。沒有前觀就不回報——不是回報 0，那會被讀成「打了但沒傷到」，
是另一種假情報。判定「有沒有人在看」屬 I/O，在 `engine/fire_wiring`。
"""

from __future__ import annotations

from typing import Any

from app.engine.rng import DeterministicRNG
from app.state.ledger import LedgerEvent

# 觀測誤差帶（±30%）。真實的 BDA 誤差取決於觀測距離、光學、煙塵、目標種類——
# 那些細緻化屬後續保真卡。這裡先用一個**單一、寫在事件裡、看得見**的帶寬：
# 讀者知道這個數字有多不可靠，比給一個假裝精確的數字誠實。
BDA_ERROR_BAND = 0.30


def estimate_losses(truth: float, rng: DeterministicRNG, *, band: float = BDA_ERROR_BAND) -> float:
    """真實戰損 → 觀測者「看到的」戰損。

    **一次射擊只抽一次**（stream 保持淺，也符合「觀測者對整片彈著區給一個判斷」的語意）。
    四捨五入到小數一位：與帳本 `damage_calc` 的三位精度刻意不同——
    一眼就看得出這是估計值而不是量出來的數。
    """
    if truth <= 0:
        return 0.0
    return max(0.0, round(truth * (1.0 + rng.uniform(-band, band)), 1))


def build_bda_event(
    *,
    tick: int,
    shooter_id: str,
    shooter_faction: str,
    aim: tuple[float, float],
    truth: float,
    rng: DeterministicRNG,
    order_id: str | None = None,
    band: float = BDA_ERROR_BAND,
) -> LedgerEvent:
    """觀測者的戰果回報事件。

    三個欄位刻意留空/留白，每一個都堵一個洩漏：

    - **`damage_calc=None`**：`aar/stats.py` 對**每一種**事件都做 `total_damage += damage_calc`。
      估計值填進去會被加在真值上，AAR 的總戰損直接變成兩倍多一點的胡說。
    - **`target_id=None`**：那會把真實單位身分帶進 AI briefing，也會覆蓋 `observer_faction`
      的受眾意圖（`event_audience` 對沒有 observer_faction 的事件按所涉單位推導受眾）。
    - **只給總量、不給逐單位**：逐單位 BDA 等於把敵軍編成表交給射方——
      `SENSOR_CONTACT` 之所以被排除在 AI briefing 之外正是同一個理由。
      真實的 BDA 本來也是「那一片大概被打掉多少」。
    """
    dec: dict[str, Any] = {
        # 唯一受眾。**沒有這個鍵，`event_audience` 會退回全域廣播**（本事件的
        # initiator 是射手，但受眾規則若走 unit 推導，敵方也會收到自己被評估的結論）。
        "observer_faction": shooter_faction,
        "is_estimate": True,  # 讀者/前端據此永遠不把它當真值呈現
        "aim_lat": aim[0],
        "aim_lng": aim[1],
        "estimated_losses": estimate_losses(truth, rng, band=band),
        "error_band": band,
    }
    if order_id:
        dec["order_id"] = order_id
    return LedgerEvent(
        event_type="BDA_REPORT",
        tick=tick,
        initiator_id=shooter_id,
        target_id=None,
        damage_calc=None,
        ai_decision=dec,
    )
