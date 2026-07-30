"""指令時效（WP-C10.3）——**過期的準備射擊不該遲到幾十個 tick 才落地**。

## 這在補什麼洞

執行期的通信閘門（`order_admissible`）遇到射手 OFFLINE 時的做法是「留在 VALIDATED，
本 tick 不執行」，等通聯恢復再打。對大多數令這是對的（部隊恢復通聯後仍該執行上級意圖）。

但對**時效性火力**不是：`at_tick` 排定的準備射擊，如果射手斷聯 40 個 tick，
恢復後才把彈打出去，打的是 40 個 tick 前的戰場——目標早就不在那裡了。
真實作業裡這種任務會**作廢**，由火協重新指派，而不是遲到執行。

## 中性預設：沒宣告就永不過期

`ttl_ticks` 缺席或 ≤0 → `expired()` 恆回 False，行為與過去逐字相同。
既有想定、既有 session、以及所有沒有時效需求的令都不受影響。

## 為什麼用「發令 tick」而不是「排定 tick」

`issued_at_tick` 是這道令**被下達**的時間，是每個 Order 都有的欄位。
若拿 `at_tick`（預劃射擊的排定時間）當基準，只有火力計畫這條路徑有得比，
其他令型就用不了同一個機制。時效的語義是「這道命令發出多久之後就不再有意義」，
本來就該以發令時間起算。
"""

from __future__ import annotations

from typing import Any

TTL_KEY = "ttl_ticks"


def ttl_of(payload: Any) -> int:
    """讀出時效（tick）。**未宣告/非法/≤0 → 0 ＝永不過期**（中性預設）。"""
    if not isinstance(payload, dict):
        return 0
    raw = payload.get(TTL_KEY)
    if not isinstance(raw, (int, float)) or raw <= 0:
        return 0
    return int(raw)


def expired(payload: Any, issued_tick: int, now_tick: int) -> bool:
    """這道令是否已逾時。

    以 `>` 而非 `>=` 比較：`ttl_ticks=1` 表示「發令後 1 個 tick 內仍有效」，
    在第 1 個 tick 執行是準時，第 2 個 tick 才算遲到。
    """
    ttl = ttl_of(payload)
    if ttl <= 0:
        return False
    return (now_tick - issued_tick) > ttl


__all__ = ["TTL_KEY", "expired", "ttl_of"]
