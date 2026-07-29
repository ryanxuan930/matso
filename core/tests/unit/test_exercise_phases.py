"""演習階段機與整備勾稽的純函數（WP-B1）。

`test_exercise_api.py` 走 REST 驗行為；這裡驗**轉移真值表**——每一條非法邊都要有人踩過，
不然「只能沿序前進」這句話就只是註解。
"""

from __future__ import annotations

import itertools

import pytest

from app.exercise.phases import (
    default_checklist,
    is_valid_transition,
    missing_required,
    next_phase,
)
from app.models.enums import ExercisePhase

_SEQ = [
    ExercisePhase.PREP,
    ExercisePhase.REHEARSAL,
    ExercisePhase.EXECUTION,
    ExercisePhase.REVIEW,
    ExercisePhase.ARCHIVED,
]


def test_only_the_next_phase_is_legal() -> None:
    """25 格真值表全部走過——合法的只有對角線上方那一條。"""
    legal = set(itertools.pairwise(_SEQ))
    for a, b in itertools.product(_SEQ, repeat=2):
        assert is_valid_transition(a, b) is ((a, b) in legal), f"{a}→{b}"


def test_archived_is_terminal() -> None:
    assert next_phase(ExercisePhase.ARCHIVED) is None
    for target in _SEQ:
        assert not is_valid_transition(ExercisePhase.ARCHIVED, target)


def test_self_transition_is_not_a_noop_it_is_illegal() -> None:
    """「再推一次」不該靜靜成功——那會在稽核軌跡上留下一筆沒有發生任何事的推進。"""
    for phase in _SEQ:
        assert not is_valid_transition(phase, phase)


def test_default_checklist_items_are_independent_dicts() -> None:
    """共用同一個 dict 的話，勾了一個演習的項目會勾到所有演習的。"""
    a, b = default_checklist(), default_checklist()
    a[0]["done"] = True
    assert b[0]["done"] is False


@pytest.mark.parametrize(
    ("phase", "expected"),
    [
        (
            ExercisePhase.PREP,
            {"prep_meeting_1", "prep_meeting_2", "prep_meeting_3", "scenario_published"},
        ),
        (ExercisePhase.REHEARSAL, {"rehearsal_done", "params_sealed"}),
        (ExercisePhase.EXECUTION, set()),  # 正式實施沒有離場前提
        (ExercisePhase.REVIEW, set()),  # 檢討會是 required=False
    ],
)
def test_missing_required_is_scoped_to_the_phase_being_left(
    phase: ExercisePhase, expected: set[str]
) -> None:
    assert set(missing_required(default_checklist(), phase)) == expected


def test_ticking_clears_the_blocker() -> None:
    items = default_checklist()
    for item in items:
        if item["phase"] == "PREP":
            item["done"] = True
    assert missing_required(items, ExercisePhase.PREP) == []


def test_no_checklist_means_no_prerequisites() -> None:
    """None ＝沒有勾稽項，不是「全部未達成」——擋下一個根本沒有清單的演習幫不到任何人。"""
    assert missing_required(None, ExercisePhase.PREP) == []
    assert missing_required([], ExercisePhase.PREP) == []
