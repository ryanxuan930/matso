"""#98 陣營關係矩陣持久化：三元組 ↔ FactionRelations、寬容解析、執行期讀取。

關鍵不變式：**未宣告（NULL）＝全 HOSTILE**——既有推演局的欄位全是 NULL，
語義必須與加欄位之前完全一致，否則等於偷偷改了所有舊局的敵我判定。
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.factions.relations import FactionRelations, Relation, relations_from_triples
from app.factions.session_store import load_session_relations
from app.models.base import Base
from app.models.tables import WargameSession


@pytest.fixture
def db() -> Session:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _add_session(db: Session, relations: object) -> str:
    db.add(
        WargameSession(
            id="s1", name="t", master_seed=1, current_weather={}, faction_relations=relations
        )
    )
    db.commit()
    return "s1"


def test_null_relations_means_all_hostile(db: Session) -> None:
    """既有局（欄位 NULL）語義不變：任兩陣營敵對。"""
    _add_session(db, None)

    rel = load_session_relations(db, "s1")

    assert rel.is_hostile("BLUE", "RED")
    assert rel.is_allied("BLUE", "BLUE")  # 己方對己方恆為 ALLIED


def test_declared_relations_round_trip(db: Session) -> None:
    """存進去什麼，讀出來就是什麼（含 ALLIED/NEUTRAL）。"""
    source = FactionRelations(
        [("BLUE", "YELLOW", Relation.ALLIED), ("RED", "YELLOW", Relation.NEUTRAL)]
    )
    _add_session(db, source.to_triples())

    rel = load_session_relations(db, "s1")

    assert rel.is_allied("BLUE", "YELLOW")
    assert rel.is_allied("YELLOW", "BLUE")  # 對稱
    assert rel.is_neutral("RED", "YELLOW")
    assert rel.is_hostile("BLUE", "RED")  # 未宣告 → 預設敵對


def test_missing_session_falls_back_to_hostile(db: Session) -> None:
    assert load_session_relations(db, "nope").is_hostile("BLUE", "RED")


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "not-a-list",
        {},
        [["BLUE"]],  # 長度不足
        [["BLUE", "RED", "BEST_FRIENDS"]],  # 未知關係值
        [[1, 2, "ALLIED"]],  # 型別錯
        [None],
    ],
)
def test_malformed_relations_never_raise(raw: object) -> None:
    """髒資料不該讓整局跑不動——一律退回全 HOSTILE 預設。"""
    rel = relations_from_triples(raw)

    assert rel.is_hostile("BLUE", "RED")


def test_partially_malformed_keeps_good_rows() -> None:
    """一筆髒資料不該毀掉整個矩陣：好的那筆仍生效。"""
    rel = relations_from_triples(
        [["BLUE", "YELLOW", "ALLIED"], ["RED", "GREEN", "NONSENSE"]],
    )

    assert rel.is_allied("BLUE", "YELLOW")
    assert rel.is_hostile("RED", "GREEN")  # 壞的那筆跳過 → 回預設


def test_to_triples_is_deterministic() -> None:
    """輸出排序固定——否則同一份關係每次存出的 JSON 都不同，diff/replay 都會噪。"""
    a = FactionRelations([("RED", "BLUE", Relation.ALLIED), ("A", "B", Relation.NEUTRAL)])
    b = FactionRelations([("A", "B", Relation.NEUTRAL), ("BLUE", "RED", Relation.ALLIED)])

    assert a.to_triples() == b.to_triples()
    assert a.to_triples() == [["A", "B", "NEUTRAL"], ["BLUE", "RED", "ALLIED"]]
