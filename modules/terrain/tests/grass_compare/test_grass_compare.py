"""GRASS 對照測試（O2.3 驗收）。GRASS docker / 真檔不在場即 skip（marker: grass）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from compare import AGREEMENT_THRESHOLD, compare, grass_available, sample_observers
from terrain.dted import DtedMap

pytestmark = pytest.mark.grass


def test_sample_observers_deterministic_and_on_land(real_dted_path: Path) -> None:
    """抽樣可獨立驗證（不需 GRASS）：確定性、皆為陸地、在界內。"""
    with DtedMap.open(real_dted_path) as dted:
        a = sample_observers(dted, 50)
        b = sample_observers(dted, 50)
        assert [(o.lat, o.lng) for o in a] == [(o.lat, o.lng) for o in b]  # 確定性
        assert len(a) == 50
        for o in a:
            assert dted.contains(o.lat, o.lng)
            assert not dted.get_elevation(o.lat, o.lng).water


def test_viewshed_agrees_with_grass(real_dted_path: Path) -> None:
    if not grass_available():
        pytest.skip("GRASS docker image (osgeo/grass-gis) 不在場——release 前於備妥環境執行")
    result = compare(real_dted_path)
    assert result.ratio >= AGREEMENT_THRESHOLD, (
        f"與 GRASS r.viewshed 一致率 {result.ratio:.3%} < {AGREEMENT_THRESHOLD:.0%}"
    )
