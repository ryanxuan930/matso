"""行動後檢討（AAR）系統（SPEC_FULL §14）——全部由不可變 Event Ledger 推導。

- events：Ledger 讀取 + AarEvent 視圖。
- replay（O8.1）：時間軸 frames + 書籤 + 任一 tick 狀態重建。
- stats（O8.2）：§14.2 指標（戰損交換比、偵測率、護欄攔截…）。
- narrative（O8.3）：AAR_ANALYST 敘事 + event id 引用查核。
- export（O8.4）：JSON/CSV + 匿名化。
"""

from __future__ import annotations

from app.aar.events import AarEvent, read_events

__all__ = ["AarEvent", "read_events"]
