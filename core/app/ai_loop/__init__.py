"""AI 自主迴路（SPEC_FULL §9.1/§9.2/§10）——事件驅動 → 產令 → 護欄 → 物理預檢 → pending。

紅線：AI 產生的每個 order 仍走物理預檢（護欄 G3），沒有繞過物理引擎的特權。模式感知：
AI_OFF → 迴路不啟動（紅軍由人操作，傳統兵推）；AI_BARE → 引用必空。
"""

from __future__ import annotations

from app.ai_loop.opfor import AiTurnResult, OpforDecider, run_opfor_turn

__all__ = ["AiTurnResult", "OpforDecider", "run_opfor_turn"]
