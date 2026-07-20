"""Fog of war 隔離 contract test（O3.3）——**此測試從此進 CI 常駐**（SPEC §7.2/§13.3）。

紅線保證：RED 的情報查詢**永遠拿不到 BLUE ground truth**——
- 拿不到 BLUE 自己的 contacts（跨陣營隔離）；
- 未偵測到的 BLUE 單位不出現；
- 未達等級的目標，其真實番號/型號/陣營被去識別化（target_unit_id 永不下發）。
"""

from __future__ import annotations

import pytest
from _order_fakes import seed_world
from sqlalchemy.orm import Session, sessionmaker

from app.intel import store
from app.intel.schemas import ContactView
from app.intel.service import IntelService
from app.intel.sweep import Contact
from app.models.enums import Faction, IntelFidelity


def _red_sees_blue(
    db: Session, session_id: str, blue_unit_id: str, fidelity: IntelFidelity
) -> None:
    store.record(
        db,
        session_id,
        Contact(
            observer_faction=Faction.RED,
            target_unit_id=blue_unit_id,
            fidelity=fidelity,
            tick=3,
            lat=23.75,
            lng=121.25,
            error_radius_m=100.0,
        ),
    )
    db.commit()


def test_red_query_never_returns_blue_ground_truth(
    session_factory: sessionmaker[Session],
) -> None:
    world = seed_world(session_factory)
    with session_factory() as db:
        _red_sees_blue(db, world.session_id, world.blue_unit_id, IntelFidelity.DETECTED)
        views = IntelService(db).visible_contacts(world.session_id, Faction.RED)

        assert len(views) == 1
        view = views[0]
        # ground truth 連結永不下發：ContactView 無 target_unit_id 欄位
        assert not hasattr(view, "target_unit_id")
        assert "target_unit_id" not in view.model_dump()
        # DETECTED → 真實番號/型號/陣營全被去識別化
        assert view.designation is None
        assert view.unit_type is None
        assert view.faction is None
        assert view.fidelity is IntelFidelity.DETECTED


def test_blue_cannot_see_reds_contacts(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    with session_factory() as db:
        _red_sees_blue(db, world.session_id, world.blue_unit_id, IntelFidelity.IDENTIFIED)
        # BLUE 未偵測任何目標 → BLUE 視圖為空（拿不到 RED 建立的 contact）
        assert IntelService(db).visible_contacts(world.session_id, Faction.BLUE) == []


def test_identified_reveals_designation(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    with session_factory() as db:
        _red_sees_blue(db, world.session_id, world.blue_unit_id, IntelFidelity.IDENTIFIED)
        view = IntelService(db).visible_contacts(world.session_id, Faction.RED)[0]
        # IDENTIFIED → 揭露番號/型號/陣營（但仍非 target_unit_id）
        assert view.designation == "B1"
        assert view.unit_type is not None
        assert view.faction == "BLUE"


def test_classified_reveals_type_not_designation(
    session_factory: sessionmaker[Session],
) -> None:
    world = seed_world(session_factory)
    with session_factory() as db:
        _red_sees_blue(db, world.session_id, world.blue_unit_id, IntelFidelity.CLASSIFIED)
        view = IntelService(db).visible_contacts(world.session_id, Faction.RED)[0]
        assert view.unit_type is not None  # 型號揭露
        assert view.designation is None  # 番號仍隱藏
        assert view.faction is None


def test_god_view_only_for_white_cell(session_factory: sessionmaker[Session]) -> None:
    world = seed_world(session_factory)
    with session_factory() as db:
        _red_sees_blue(db, world.session_id, world.blue_unit_id, IntelFidelity.DETECTED)
        service = IntelService(db)
        with pytest.raises(PermissionError):
            service.god_view(world.session_id, Faction.RED)
        # WHITE_CELL 全知：看得到所有 contacts（含 RED 建立的）
        god: list[ContactView] = service.god_view(world.session_id, Faction.WHITE_CELL)
        assert len(god) == 1
