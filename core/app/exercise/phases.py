"""演習階段機與整備勾稽（WP-B1）——純函數，不碰 DB。

階段機**只能沿序前進、一次一階**。倒退看似方便（「這場預推重來一次」），但：

- WP-B4 在進入 EXECUTION 時把參數簽證鎖定；退回 REHEARSAL 再進來一次，
  簽證要重簽還是沿用？兩種答案都會讓「演習中的參數是凍結的」這句話出現例外。
- 稽核軌跡的意義來自單調——`PREP→REHEARSAL→PREP→REHEARSAL` 讀不出「重來過」，
  只讀得出「有人在按按鈕」。

要重來就開一個新演習。這比讓階段機可逆便宜得多。

## 勾稽項

checklist 是**離開某階段的前提**，不是待辦清單：`required=True` 且未勾的項目會擋下推進。
只當提示的話，與沒有這個機制無異——[JCATS-A p.9–16] 的 17 步 SOP 之所以是 SOP，
就是因為每一步都得真的做完。
"""

from __future__ import annotations

from typing import Any

from app.models.enums import ExercisePhase

# 階段序。`_ORDER.index()` 即為「第幾階」——轉移合法性只看相鄰。
_ORDER: tuple[ExercisePhase, ...] = (
    ExercisePhase.PREP,
    ExercisePhase.REHEARSAL,
    ExercisePhase.EXECUTION,
    ExercisePhase.REVIEW,
    ExercisePhase.ARCHIVED,
)


def next_phase(current: ExercisePhase) -> ExercisePhase | None:
    """序列上的下一階；已在最後一階回 None。"""
    i = _ORDER.index(current)
    return _ORDER[i + 1] if i + 1 < len(_ORDER) else None


def is_valid_transition(current: ExercisePhase, target: ExercisePhase) -> bool:
    """只有「序列上的下一階」合法。跳階與倒退皆否。"""
    return next_phase(current) is target


# 預設整備勾稽項。`phase` ＝這一項是「離開哪個階段」的前提。
#
# `params_sealed` 由 WP-B4 於簽證完成時**自動勾**（`tick_key` 的程式端呼叫）——
# 留在這裡是為了讓 B4 有一個明確的掛點，而不是 B4 再去改 checklist 的結構。
_DEFAULT_CHECKLIST: tuple[dict[str, Any], ...] = (
    {"key": "prep_meeting_1", "label": "整備會議 #1", "phase": "PREP", "required": True},
    {"key": "prep_meeting_2", "label": "整備會議 #2", "phase": "PREP", "required": True},
    {"key": "prep_meeting_3", "label": "整備會議 #3", "phase": "PREP", "required": True},
    {"key": "scenario_published", "label": "想定發佈（D-45）", "phase": "PREP", "required": True},
    {"key": "saturation_test", "label": "系統飽和測試", "phase": "PREP", "required": False},
    {"key": "rehearsal_done", "label": "預推完成", "phase": "REHEARSAL", "required": True},
    {
        "key": "params_sealed",
        "label": "參數簽證完成（WP-B4）",
        "phase": "REHEARSAL",
        "required": True,
    },
    {"key": "aar_reviewed", "label": "檢討會完成", "phase": "REVIEW", "required": False},
)


def default_checklist() -> list[dict[str, Any]]:
    """新演習的預設勾稽項（每項獨立 dict，呼叫端可安全改寫）。"""
    return [
        {**item, "done": False, "done_at": None, "done_by": None} for item in _DEFAULT_CHECKLIST
    ]


def missing_required(checklist: list[dict[str, Any]] | None, phase: ExercisePhase) -> list[str]:
    """離開 `phase` 前還沒勾的必要項目鍵。空 list ＝可以推進。

    `checklist` 為 None（舊資料/手動清空）→ 視為**沒有任何前提**而非「全部未達成」：
    擋下一個根本沒有勾稽項的演習不會幫到任何人。
    """
    return [
        str(item.get("key"))
        for item in (checklist or [])
        if item.get("phase") == phase.value and item.get("required") and not item.get("done")
    ]


__all__ = [
    "default_checklist",
    "is_valid_transition",
    "missing_required",
    "next_phase",
]
