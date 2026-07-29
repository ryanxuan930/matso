"""活模擬控制旗標（新 #6）——White Cell 暫停/續行的共用 Redis 鍵。

control 端點（PAUSE 設鍵 / RESUME 清鍵）與 sim_runtime 迴圈（輪詢此鍵）以此協調，
使白軍控制台的時間控制真正作用於執行中的 Kernel（先前僅發事件、不影響 tick）。
"""

from __future__ import annotations


def session_pause_key(session_id: str) -> str:
    """該 session 的暫停旗標鍵；存在＝暫停中。"""
    return f"matso:sim:{session_id}:paused"


def session_concluded_key(session_id: str) -> str:
    """該 session 的收場旗標鍵（O11.5）；存在＝勝負已定，runner 停止且不再重啟。"""
    return f"matso:sim:{session_id}:concluded"


def session_rollback_key(session_id: str) -> str:
    """該 session 的待辦回滾請求鍵（值＝目標 tick，WP-E1）。

    白軍按下 ROLLBACK 時**不**由 API 行程直接改熱狀態：`RedisHotState` 有 in-process
    mirror cache，跑中的 runner 看不到外部寫入、且下一個 tick 就會用舊 mirror 蓋回去。
    改為記下請求 + 設 restart 旗標，由重建後的 runner 在啟動階段執行——與
    `live_position`／`live_ammo` 的命令通道同一套紀律（單一寫入者原則）。
    """
    return f"matso:sim:{session_id}:rollback_to"


def session_restart_key(session_id: str) -> str:
    """該 session 的 runner 重啟旗標；存在＝請求 runner 結束當前迴圈，由掃描層重建以重讀
    自主 AI 指派（AI 指派只於 runner 起跑時讀取）。runner 起跑時清此鍵。"""
    return f"matso:sim:{session_id}:restart"
