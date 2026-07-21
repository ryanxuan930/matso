"""Order 測試共用：最小世界 seed + 假 PhysicsGateway（不需真 terrain gRPC）。"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from app.errors import TerrainUnavailableError
from app.models.enums import Faction, UnitLevel, UserRole
from app.models.tables import SessionParticipant, TacticalUnit, User, WargameSession


@dataclass(frozen=True)
class OrderWorld:
    """session + 藍/紅各一單位 + 藍方 COMMANDER + 白軍參與者。座標在台灣本島陸地。"""

    session_id: str
    blue_unit_id: str
    red_unit_id: str
    blue_issuer_id: str
    white_issuer_id: str  # WHITE_CELL_STAFF：可跨陣營下令
    cmdr_user_id: str  # 藍方 COMMANDER 的 User id（供 O4.5 bearer token）
    white_user_id: str


def seed_world(factory: sessionmaker[Session]) -> OrderWorld:
    """把最小可下令世界寫入（SQLite）並回 id 集合。"""
    with factory() as db:
        session = WargameSession(name="t", master_seed=1, current_weather={})
        db.add(session)
        db.flush()
        blue = TacticalUnit(
            session_id=session.id,
            designation="B1",
            unit_level=UnitLevel.PLATOON,
            faction=Faction.BLUE,
            current_lat=23.75,
            current_lng=121.25,
        )
        red = TacticalUnit(
            session_id=session.id,
            designation="R1",
            unit_level=UnitLevel.PLATOON,
            faction=Faction.RED,
            current_lat=23.76,
            current_lng=121.26,
        )
        cmdr = User(username="cmdr", password_hash="x", role=UserRole.COMMANDER)
        white = User(username="white", password_hash="x", role=UserRole.WHITE_CELL_STAFF)
        db.add_all([blue, red, cmdr, white])
        db.flush()
        blue_issuer = SessionParticipant(
            user_id=cmdr.id,
            session_id=session.id,
            faction=Faction.BLUE,
            role=UserRole.COMMANDER,
            unit_scope={},
        )
        white_issuer = SessionParticipant(
            user_id=white.id,
            session_id=session.id,
            faction=Faction.WHITE_CELL,
            role=UserRole.WHITE_CELL_STAFF,
            unit_scope={},
        )
        db.add_all([blue_issuer, white_issuer])
        db.commit()
        return OrderWorld(
            session_id=session.id,
            blue_unit_id=blue.id,
            red_unit_id=red.id,
            blue_issuer_id=blue_issuer.id,
            white_issuer_id=white_issuer.id,
            cmdr_user_id=cmdr.id,
            white_user_id=white.id,
        )


def order_token(user_id: str, role: UserRole = UserRole.COMMANDER) -> str:
    """為 O4.5 orders API 測試簽 bearer token（用 _auth_fakes 的測試 secret）。"""
    from _auth_fakes import TEST_SETTINGS

    from app.auth.tokens import JwtCodec, TokenType

    return JwtCodec(secret=TEST_SETTINGS.jwt_secret).issue(
        user_id, role.value, TokenType.ACCESS, 900
    )


class FakeGateway:
    """可設定回傳的假物理 gateway，並記錄呼叫供斷言。"""

    def __init__(self, reachable: bool = True, visible: bool = True) -> None:
        self.reachable = reachable
        self.visible = visible
        self.path_calls: list[tuple[str, str, str]] = []
        self.los_calls: list[tuple[tuple[float, float, float], tuple[float, float, float]]] = []

    def path_reachable(self, from_h3: str, to_h3: str, mobility_profile: str) -> tuple[bool, str]:
        self.path_calls.append((from_h3, to_h3, mobility_profile))
        return self.reachable, ("cost=5.0, eta=5" if self.reachable else "不可達")

    def has_los(
        self, observer: tuple[float, float, float], target: tuple[float, float, float]
    ) -> tuple[bool, float]:
        self.los_calls.append((observer, target))
        return self.visible, (12.3 if self.visible else -5.0)


class DownGateway:
    """模擬 terrain 不可達（斷路器/gRPC 失敗）。"""

    def path_reachable(self, from_h3: str, to_h3: str, mobility_profile: str) -> tuple[bool, str]:
        raise TerrainUnavailableError("terrain down")

    def has_los(
        self, observer: tuple[float, float, float], target: tuple[float, float, float]
    ) -> tuple[bool, float]:
        raise TerrainUnavailableError("terrain down")
