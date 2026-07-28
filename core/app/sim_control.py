"""活模擬控制旗標（新 #6）——White Cell 暫停/續行的共用 Redis 鍵。

control 端點（PAUSE 設鍵 / RESUME 清鍵）與 sim_runtime 迴圈（輪詢此鍵）以此協調，
使白軍控制台的時間控制真正作用於執行中的 Kernel（先前僅發事件、不影響 tick）。
"""

from __future__ import annotations


def session_pause_key(session_id: str) -> str:
    """該 session 的暫停旗標鍵；存在＝暫停中。"""
    return f"matso:sim:{session_id}:paused"
