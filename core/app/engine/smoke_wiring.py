"""煙幕在活執行期的接線（WP-C4c）。

`adjudication/obscurants.py` 是純幾何；本模組只做 I/O 邊界：煙存在哪、怎麼生、怎麼查。

## 煙存成 `MapFeature`，不是熱狀態

熱狀態是 **unit 鍵值**的，而煙不是單位。硬塞成 pseudo-unit 的代價是每一個
`hot.get_all()` 的消費端（sensor sweep、AI context、broadcaster、`compute_state_hash`）
都得學會忽略它——那是四處要改、漏一處就出事的形狀。

存成 `MapFeature(kind="SMOKE")` 則免費得到：**持久化**（重啟/checkpoint 自動涵蓋）、
既有的地圖標註載入機制、前端本來就會畫 MapFeature。代價是一次 DB 寫入（發煙很罕見）
與逐 tick 一次查詢（與 `_load_obstacles` 同樣做成每 tick 一次的快取）。

## 消散不需要每 tick 寫回

到期資訊寫在 `attributes.expires_at_tick`，判定是純比較。**沒有任何逐 tick 的狀態更新**
——那也意味著煙不會在 STATE_DIFF 上製造每 tick 的雜訊。清理（刪掉過期的 row）是可選的
維護工作，不是正確性的一部分。
"""

from __future__ import annotations

from typing import Any

from app.adjudication.obscurants import (
    DEFAULT_SMOKE_RADIUS_M,
    SmokeCloud,
    duration_ticks,
)

SMOKE_KIND = "SMOKE"


def load_active_smoke(db: Any, session_id: str, tick: int) -> list[SmokeCloud]:
    """本局此刻仍有效的煙。**沒有煙時回空 list**——呼叫端因此一次幾何判定都不做。

    既有局一片煙都沒有，所以這條路徑對它們是零成本、零行為變更。
    """
    from sqlalchemy import select

    from app.models.tables import MapFeature

    rows = db.scalars(
        select(MapFeature).where(MapFeature.session_id == session_id, MapFeature.kind == SMOKE_KIND)
    ).all()
    out: list[SmokeCloud] = []
    for row in rows:
        cloud = _to_cloud(row)
        if cloud is not None and cloud.active_at(tick):
            out.append(cloud)
    return out


def _to_cloud(row: Any) -> SmokeCloud | None:
    """`MapFeature` → `SmokeCloud`。幾何/屬性壞掉 → None（略過該筆，不讓一筆髒資料毀掉整局）。"""
    attrs = row.attributes if isinstance(row.attributes, dict) else {}
    expires = attrs.get("expires_at_tick")
    if not isinstance(expires, (int, float)):
        return None
    coords = row.geometry
    if not isinstance(coords, list) or len(coords) < 2:
        return None
    try:
        lng, lat = float(coords[0]), float(coords[1])
    except (TypeError, ValueError):
        return None
    radius = row.influence_radius_m
    return SmokeCloud(
        lat=lat,
        lng=lng,
        radius_m=float(radius) if isinstance(radius, (int, float)) and radius > 0 else 0.0,
        expires_at_tick=int(expires),
        feature_id=str(row.id),
    )


def emplace_smoke(
    db: Any,
    session_id: str,
    *,
    lat: float,
    lng: float,
    tick: int,
    rounds: int = 1,
    radius_m: float = DEFAULT_SMOKE_RADIUS_M,
    owner_faction: str = "",
) -> SmokeCloud:
    """生成一團煙並落庫。回生成的煙。

    `rounds` 決定持續時間——**發數是發煙者唯一能調的旋鈕**（見 `obscurants` 模組說明）。
    """
    from app.models.tables import MapFeature

    expires = tick + duration_ticks(rounds)
    row = MapFeature(
        session_id=session_id,
        kind=SMOKE_KIND,
        geometry_type="POINT",
        geometry=[float(lng), float(lat)],
        owner_faction=owner_faction,
        label="煙幕",
        influence_radius_m=float(radius_m),
        attributes={"expires_at_tick": int(expires), "rounds": int(rounds)},
    )
    db.add(row)
    db.flush()
    return SmokeCloud(
        lat=float(lat),
        lng=float(lng),
        radius_m=float(radius_m),
        expires_at_tick=int(expires),
        feature_id=str(row.id),
    )


def purge_expired_smoke(db: Any, session_id: str, tick: int) -> int:
    """刪掉過期的煙 row。回刪除數。

    **這是維護不是正確性**：過期的煙靠 `active_at()` 就已經失效，留著只是佔位。
    分開的理由是刪除要 commit，而查詢路徑不該有寫入副作用。
    """
    from sqlalchemy import select

    from app.models.tables import MapFeature

    rows = db.scalars(
        select(MapFeature).where(MapFeature.session_id == session_id, MapFeature.kind == SMOKE_KIND)
    ).all()
    removed = 0
    for row in rows:
        cloud = _to_cloud(row)
        if cloud is None or not cloud.active_at(tick):
            db.delete(row)
            removed += 1
    return removed


class SmokeCache:
    """逐 tick 的煙快取。**每 tick 至多一次 query**，同 tick 內的每一次 LOS 判定共用。

    `env_for` 閉包在 Kernel 建構時就固定了，所以查詢必須走一個「現在幾 tick」的回呼
    ——傳一份清單進去會讓整局停在建立時的那一刻（同 WP-C4a `light_for` 的理由）。
    """

    def __init__(self, session_factory: Any, session_id: str) -> None:
        self._factory = session_factory
        self._session_id = session_id
        self._tick: int | None = None
        self._clouds: list[SmokeCloud] = []

    def at(self, tick: int) -> list[SmokeCloud]:
        if self._tick != tick:
            with self._factory() as db:
                self._clouds = load_active_smoke(db, self._session_id, tick)
            self._tick = tick
        return self._clouds


__all__ = [
    "SMOKE_KIND",
    "SmokeCache",
    "emplace_smoke",
    "load_active_smoke",
    "purge_expired_smoke",
]
