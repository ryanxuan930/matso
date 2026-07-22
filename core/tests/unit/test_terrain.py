"""地形遮蔽命中修正（真實化交戰 Phase 3 STEP3）——terrain.engagement_cover_modifier。"""

from __future__ import annotations

import math

import pytest

from app.terrain import engagement_cover_modifier


def test_open_terrain_no_cover() -> None:
    assert engagement_cover_modifier(25.0) == pytest.approx(1.0)
    assert engagement_cover_modifier(100.0) == pytest.approx(1.0)


def test_grazing_shot_heavy_cover() -> None:
    assert engagement_cover_modifier(0.0) == pytest.approx(0.55)
    assert engagement_cover_modifier(-5.0) == pytest.approx(0.55)


def test_partial_clearance_interpolates() -> None:
    # 餘隙 12.5m（半程）→ 介於 0.55 與 1.0 之間。
    m = engagement_cover_modifier(12.5)
    assert 0.55 < m < 1.0
    assert m == pytest.approx(0.55 + 0.45 * 0.5)


def test_none_or_inf_is_neutral() -> None:
    assert engagement_cover_modifier(None) == pytest.approx(1.0)
    assert engagement_cover_modifier(math.inf) == pytest.approx(1.0)


def test_monotonic_nondecreasing_in_clearance() -> None:
    prev = -1.0
    for c in range(0, 30):
        m = engagement_cover_modifier(float(c))
        assert m >= prev - 1e-9
        assert 0.55 <= m <= 1.0
        prev = m
