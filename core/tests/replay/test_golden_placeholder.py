"""Golden replay 佔位（SPEC_FULL §19.1）。M1-6 建立 replay harness 後以真測試取代。"""

import pytest


@pytest.mark.golden
@pytest.mark.skip(reason="Golden replay harness lands in M1-6")
def test_golden_replay_placeholder() -> None:  # pragma: no cover
    raise AssertionError("unreachable")
