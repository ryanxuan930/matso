"""重連用的原子狀態快照（WP-E3；SPEC_V2 §6 WP-E3、contracts/ws_protocol.md）。

`GET /api/v1/sessions/{id}/state` —— WS 送出 `RESYNC_REQUIRED` 後，client 以此端點
**一次**取回全部可見狀態並原子重建。過去這個端點只存在於契約（一行佔位），前端收到 RESYNC
就把結果丟掉、改靠每 10 秒重抓六個獨立 GET 兜底——那六個回應彼此不同時，會拼出一個
「單位是新的、敵情是舊的」的畫面。

## 為什麼是「呼叫既有 handler」而不是重寫過濾

紅線 3：fog of war 的 faction 過濾只能在後端。本端點**不自行實作任何過濾**，而是直接呼叫
`/units`、`/intel`、`/map-features`、`/relations` 的 handler 函式。理由是**一致性由構造保證**：

- 三個端點的過濾規則本來就有細微差異（units 看「自己＋盟軍」、map-features 只看「共同＋自己」、
  units 有 `STUB_GATEWAY` 的 E2E affordance…）。重寫一份必然會漂移，而**迷霧過濾的漂移就是
  資安漏洞**——重連後看到的比正常時多，或少。
- 兩份實作也代表兩處要維護：日後任一端點的過濾改了，快照會安靜地留在舊規則。

handler 的 `Depends(...)` 只是預設值；以具名引數全部傳入即為普通函式呼叫。

## last_seq 的取樣順序（會影響正確性）

**先取 `last_seq`，再讀狀態。** 反過來的話，介於「讀狀態」與「取 seq」之間送出的 STATE_DIFF
會既不在快照裡、seq 又 ≤ last_seq —— client 依約丟棄它，那個更新就永久遺失。
先取 seq 則最壞情況是「快照已含某 diff、client 又套用一次」，而 diff 是覆寫式的，重複套用無害。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, get_settings
from app.api.intel import faction_posture, get_intel
from app.api.map_features import MapFeatureView, list_map_features
from app.api.relations import FactionRelationsView, get_faction_relations
from app.api.units import UnitView, list_units
from app.auth.schemas import CurrentUser
from app.cache import make_redis
from app.config import Settings
from app.intel.schemas import ContactView
from app.state.hot_state import session_tick_key
from app.state.redis_stream import seq_key

router = APIRouter(prefix="/api/v1/sessions", tags=["state"])


class StateSnapshotView(BaseModel):
    """單一原子快照（契約 `StateSnapshotView`）。"""

    tick: int
    last_seq: int
    observer_faction: str | None
    # WP-C5：觀測陣營的整體通聯姿態（god view 為 None）。前端據此顯示「敵情圖粗化中」——
    # 粗化本身**已在 contacts 上生效**，本欄純為說明，client 不得據此再動資料。
    comms_posture: str | None
    units: list[UnitView]
    contacts: list[ContactView]
    map_features: list[MapFeatureView]
    relations: FactionRelationsView


def _int_key(client: object, key: str) -> int:
    """讀一個整數型 Redis 鍵；不存在/壞值/Redis 掛掉 → 0（快照仍可用，只是無法去重）。"""
    try:
        raw = client.get(key)  # type: ignore[attr-defined]
    except Exception:
        return 0
    try:
        return int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        return 0


@router.get("/{session_id}/state", response_model=StateSnapshotView)
def get_state(
    session_id: str,
    as_faction: str | None = Query(None, description="White Cell 視角切換"),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> StateSnapshotView:
    client = make_redis(settings.redis_url)
    # ⚠ 順序有意義：seq 必須在狀態**之前**取樣（見模組 docstring）。
    last_seq = _int_key(client, seq_key(session_id))
    tick = _int_key(client, session_tick_key(session_id))

    relations = get_faction_relations(session_id, as_faction, user, db)
    observer = relations.observer
    return StateSnapshotView(
        tick=tick,
        last_seq=last_seq,
        # 觀測視角由 relations handler 推導（全知未指定 as_faction → None＝god view），
        # 不在此另算一次——多一份推導就多一個會漂移的地方。
        observer_faction=observer,
        # 同理，粗化與否由 /intel 那條路自己決定；本欄只是把它算出來的姿態說出來。
        comms_posture=(
            faction_posture(db, settings, session_id, observer).value if observer else None
        ),
        units=list_units(session_id, as_faction, user, db, settings),
        contacts=get_intel(session_id, as_faction, user, db, settings),
        map_features=list_map_features(session_id, as_faction, user, db),
        relations=relations,
    )
