"""補給點與補給線（WP-C7.2）。

規格的一句話定義了這張卡的價值：**「這讓『打擊敵後勤』成為可行戰法」**。
補給點是地圖上一個有庫存、有陣營、打得掉的東西；打掉它，下游單位的水位就不再回升。

## 補給點是 `MapFeature(kind="SUPPLY_POINT")`，理由與煙幕同一條

熱狀態是 **unit 鍵值**的，補給點不是單位（它不移動、不交戰、沒有戰力）。
硬塞成 pseudo-unit 會讓每個 `hot.get_all()` 消費端都得學會忽略它——WP-C4c 已經走過這條。
存成 MapFeature 免費得到持久化、地圖圖層、以及**已經存在的敵我可見性語義**。

## 「打掉補給點」用的是既有的摧毀語義，不是新機制

補給點的存活由 `attributes.destroyed` 表示。誰去設它？——WP-C10.2 的面射擊已經會蒐集
落點半徑內的所有目標，本模組只提供 `destroy_at()` 供火力裁決在命中時呼叫。
**不另造一套「攻擊建物」的裁決**：那會變成第二套傷害模型。

## 撥交是「拉」不是「推」

`draw_from()` 由**需要補給的一方**呼叫（單位低於水位 → 找最近的己方補給點 → 拉）。
做成補給點主動推送的話，補給點就得知道全場有誰、誰缺什麼——那是全知，
而且會讓「補給線被切斷」變得無法表達（推送不需要路徑）。

⚠ 本卡**只做庫存與撥交**，不做運輸。「補給車自動往返」在規格裡是 MISSION `MOVE_MARCH`
的複用，屬 C7.3 的範圍——先把帳做對，再讓車跑起來。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.adjudication.supply import SupplyClass, SupplyLevel

SUPPLY_POINT_KIND = "SUPPLY_POINT"
# 單位要在這個半徑內才拉得到補給。與 `RESUPPLY_RANGE_KM` 分開：那是補給車對單位，
# 這是單位對補給點。
DRAW_RADIUS_M = 3000.0


@dataclass(frozen=True, slots=True)
class SupplyPoint:
    """一個補給點。`stock` 是各類別的庫存量（無容量上限——它是倉庫不是背包）。"""

    feature_id: str
    faction: str
    lat: float
    lng: float
    stock: dict[SupplyClass, float]
    destroyed: bool = False

    @property
    def usable(self) -> bool:
        return not self.destroyed and any(v > 0 for v in self.stock.values())


def read_point(row: Any) -> SupplyPoint | None:
    """`MapFeature` → `SupplyPoint`。幾何/屬性壞掉 → None（略過該筆，不毀掉整局）。"""
    attrs = row.attributes if isinstance(row.attributes, dict) else {}
    coords = row.geometry
    if not isinstance(coords, list) or len(coords) < 2:
        return None
    try:
        lng, lat = float(coords[0]), float(coords[1])
    except (TypeError, ValueError):
        return None
    raw_stock = attrs.get("stock")
    stock: dict[SupplyClass, float] = {}
    if isinstance(raw_stock, dict):
        for key, value in raw_stock.items():
            try:
                stock[SupplyClass(str(key))] = max(0.0, float(value))
            except (ValueError, TypeError):
                continue
    return SupplyPoint(
        feature_id=str(row.id),
        faction=str(row.owner_faction or ""),
        lat=lat,
        lng=lng,
        stock=stock,
        destroyed=bool(attrs.get("destroyed")),
    )


def load_points(db: Any, session_id: str) -> list[SupplyPoint]:
    """本局所有補給點（含已摧毀的——AAR 要看得到它曾經在那裡）。"""
    from sqlalchemy import select

    from app.models.tables import MapFeature

    rows = db.scalars(
        select(MapFeature)
        .where(MapFeature.session_id == session_id, MapFeature.kind == SUPPLY_POINT_KIND)
        .order_by(MapFeature.id)  # 確定性：撥交順序不可隨查詢順序漂
    ).all()
    return [p for p in (read_point(r) for r in rows) if p is not None]


def nearest_usable(
    points: list[SupplyPoint], faction: str, lat: float, lng: float, radius_m: float = DRAW_RADIUS_M
) -> SupplyPoint | None:
    """離 (lat, lng) 最近、**同陣營且還有貨**的補給點。超出半徑 → None。

    ⚠ 只找**自己陣營**的。盟軍補給點要不要共用是後勤協定問題，不是物理問題——
    預設不共用比較保守，要開放應該是想定的明確宣告而不是預設。
    """
    from app.movement.attrition import haversine_m

    best: tuple[float, SupplyPoint] | None = None
    for point in points:
        if point.faction != faction or not point.usable:
            continue
        dist = haversine_m((lng, lat), (point.lng, point.lat))
        if dist <= radius_m and (best is None or dist < best[0]):
            best = (dist, point)
    return best[1] if best is not None else None


def draw_from(
    db: Any, point: SupplyPoint, wanted: dict[SupplyClass, float]
) -> dict[SupplyClass, float]:
    """從補給點拉貨。回**實際撥出的量**（庫存不足就給多少算多少）。

    庫存不足時給一部分而不是整批拒絕——那才是真實的補給點行為，
    而且「拉到一半」正是指揮官需要看見的訊號（這個補給點快空了）。
    """
    from app.models.tables import MapFeature

    row = db.get(MapFeature, point.feature_id)
    if row is None or point.destroyed:
        return {}
    stock = dict(point.stock)
    issued: dict[SupplyClass, float] = {}
    for supply_class, amount in sorted(wanted.items(), key=lambda kv: kv[0].value):
        available = stock.get(supply_class, 0.0)
        take = min(max(0.0, amount), available)
        if take > 0:
            stock[supply_class] = available - take
            issued[supply_class] = take
    if issued:
        # ⚠ JSON 欄位要**整包換掉**才會被 SQLAlchemy 視為 dirty（同 WP-C2 的教訓）。
        attrs = dict(row.attributes or {})
        attrs["stock"] = {c.value: round(v, 4) for c, v in sorted(stock.items())}
        row.attributes = attrs
    return issued


def destroy_at(db: Any, session_id: str, lat: float, lng: float, radius_m: float) -> list[str]:
    """摧毀落點半徑內的補給點。回被摧毀的 feature id。

    供火力裁決在命中時呼叫——**不另造一套「攻擊建物」的裁決**，那會變成第二套傷害模型。
    """
    from app.models.tables import MapFeature
    from app.movement.attrition import haversine_m

    destroyed: list[str] = []
    for point in load_points(db, session_id):
        if point.destroyed:
            continue
        if haversine_m((lng, lat), (point.lng, point.lat)) <= radius_m:
            row = db.get(MapFeature, point.feature_id)
            if row is not None:
                row.attributes = {**(row.attributes or {}), "destroyed": True}
                destroyed.append(point.feature_id)
    return destroyed


def topped_up(
    current: dict[SupplyClass, SupplyLevel], issued: dict[SupplyClass, float]
) -> dict[SupplyClass, SupplyLevel]:
    """把撥交量加回單位水位，**夾在容量上限**（背包裝不下就是裝不下）。"""
    out = dict(current)
    for supply_class, amount in issued.items():
        level = out.get(supply_class)
        if level is None or not level.declared:
            continue
        out[supply_class] = SupplyLevel(min(level.capacity, level.on_hand + amount), level.capacity)
    return out


__all__ = [
    "DRAW_RADIUS_M",
    "SUPPLY_POINT_KIND",
    "SupplyPoint",
    "destroy_at",
    "draw_from",
    "load_points",
    "nearest_usable",
    "read_point",
    "topped_up",
]
