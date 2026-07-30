"""煙幕接線（WP-C4c）：漂移、過期清理、與既有局的零影響。

`drift()` 的數學由 test_obscurants.py 覆蓋；本檔只測「接線層」的決策
——那正是它過去缺的東西：函式寫好了、測試也綠，生產路徑一個呼叫端都沒有。
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.models.tables import WargameSession

_SID = "sess-smoke"


def _seed_session(db: Session) -> None:
    db.add(WargameSession(id=_SID, name="煙幕測試", master_seed=1, current_weather={}))
    db.flush()


# ---- WP-C4c：煙隨風漂（`drift()` 有實作與 8 條測試、生產零呼叫端） ----


def test_smoke_drifts_downwind_over_time(session_factory) -> None:  # type: ignore[no-untyped-def]
    """風場一路讀進 `CellEffects` 卻沒有消費者；`drift()` 也沒有任何生產呼叫端。

    結果：颳八級風的戰場上，煙幕像釘在地上一樣不動。
    """
    from app.engine.smoke_wiring import SmokeCache, emplace_smoke

    with session_factory() as db:
        _seed_session(db)
        emplace_smoke(db, _SID, lat=24.0, lng=121.0, tick=0, rounds=4)
        db.commit()

    still = SmokeCache(session_factory, _SID).at(10)
    # 北風（來向 0）→ 煙往南走。
    blown = SmokeCache(session_factory, _SID, wind_for=lambda: (10.0, 0.0)).at(10)

    assert still[0].lat == pytest.approx(24.0)
    assert blown[0].lat < 24.0, "北風應把煙往南吹"


def test_no_wind_means_no_drift(session_factory) -> None:  # type: ignore[no-untyped-def]
    """守門不可過寬：無風/未注入 → 位元不變（既有局零影響）。"""
    from app.engine.smoke_wiring import SmokeCache, emplace_smoke

    with session_factory() as db:
        _seed_session(db)
        emplace_smoke(db, _SID, lat=24.0, lng=121.0, tick=0, rounds=4)
        db.commit()

    calm = SmokeCache(session_factory, _SID, wind_for=lambda: (0.0, 90.0)).at(10)

    assert calm[0].lat == pytest.approx(24.0) and calm[0].lng == pytest.approx(121.0)


def test_smoke_without_an_emplace_tick_never_drifts(session_factory) -> None:  # type: ignore[no-untyped-def]
    """舊資料沒有 `emplaced_at_tick` → 不漂，而不是從 tick 0 起算出一個荒謬的位移。"""
    from app.engine.smoke_wiring import SMOKE_KIND, SmokeCache
    from app.models.tables import MapFeature

    with session_factory() as db:
        _seed_session(db)
        db.add(
            MapFeature(
                session_id=_SID,
                kind=SMOKE_KIND,
                geometry_type="POINT",
                geometry=[121.0, 24.0],
                owner_faction="BLUE",
                influence_radius_m=50.0,
                attributes={"expires_at_tick": 100},  # 沒有 emplaced_at_tick
            )
        )
        db.commit()

    out = SmokeCache(session_factory, _SID, wind_for=lambda: (20.0, 0.0)).at(50)

    assert out[0].lat == pytest.approx(24.0)
