"""面目標射擊在活執行期的接線（WP-C10.2）——把 VALIDATED FIRE_MISSION 接上裁決純函數。

`adjudication/area_fire.py` 是純同步純函數（紅線 2）；本模組只做 I/O 邊界：

    drain VALIDATED FIRE_MISSION → 蒐集落點附近單位（**敵我皆收**）
    → resolve_area_fire → 扣彈藥、落戰損、轉 COMPLETED

**蒐集目標為什麼不做半徑預篩**：落點是 Rayleigh 抽樣，尾巴無上界——任何「半徑 + N 倍 sigma」
的預篩都是猜的，猜小了就是把遠處的傷亡悄悄吃掉，而且不會有任何徵兆。這裡直接把
**全部有座標的單位**交給純函數依實際落點篩，寧可多算幾次 haversine（單位數量級 500，
火力任務本身稀疏）。真的成為熱點時該補的是空間索引，不是一個魔術半徑。

**射手自己也在目標清單裡**：對自己的位置叫火力（danger close / 最終保護射擊）是真實戰術，
不特判。砲彈不挑人這件事沒有例外。
"""

from __future__ import annotations

import enum
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adjudication.area_fire import AreaTarget, resolve_area_fire
from app.adjudication.bda import build_bda_event
from app.adjudication.effectiveness import effectiveness_pct
from app.adjudication.fratricide import is_friendly
from app.adjudication.weapon import INDIRECT_CATEGORIES
from app.comms import order_admissible, parse_link_state
from app.engine.clock import SimTime
from app.engine.engage_wiring import WeaponEntry
from app.engine.formation_wiring import FORMATION_KEY
from app.engine.movement import ARRIVED_TICK_KEY
from app.engine.rng import DeterministicRNG
from app.engine.suppression_wiring import POSTURE_KEY, apply_area_suppression
from app.factions.relations import FactionRelations
from app.fires.survivability import MISSION_COUNT_KEY
from app.models.enums import OrderStatus
from app.models.tables import EquipmentInstance, Order, TacticalUnit
from app.orders.schemas import OrderType
from app.orders.state_machine import next_status
from app.orders.ttl import expired, ttl_of
from app.state.hot_state import HotStateStore
from app.state.ledger import LedgerEvent

_EARTH_R_M = 6_371_000.0

# ---- WP-C10.4 觀測判定 ----
# 觀測/落點離地高（與 orders.precheck._ENGAGE_OBS_M、sensor_wiring 同一個值——
# 三處若各給一個數，同一組座標會得到三種視線結論）。
_OBS_HEIGHT_M = 10.0
# 一次火力任務最多打幾次 LOS RPC。tick 預算 200ms、單次 gRPC 死線也是 200ms——
# 不設上限的話，一個 500 單位的編組會把整個 tick（連同同行程的其他 session）吃光。
# 依距離排序後取最近的幾個：真正看得到落點的本來就是那幾個。
_MAX_LOS_PROBES = 8
# 觀測距離上限。只看 LOS 不看距離的話，40 km 外的單位會被當成前觀——
# 地形視線在那個距離上「通」是幾何事實，但人眼/光學看不到彈著修正。
_MAX_SPOT_RANGE_M = 15_000.0
# 沒有觀測時的散布倍率（驗收條件：前觀死亡 → 散布加倍）。
NO_OBSERVER_DISPERSION_MULT = 2.0


class ObserverVerdict(enum.StrEnum):
    """射擊陣營對落點的觀測狀態（WP-C10.4）。

    **`UNKNOWN` 與 `UNOBSERVED` 刻意分開**：前者是系統答不出來（地形服務掛了），
    後者是戰術事實（沒人看得到）。合成一個 bool 就會把「服務中斷」呈現成
    「全場砲兵突然都瞎了」——把故障演成戰術事實是最難查的一種錯。
    """

    OBSERVED = "OBSERVED"
    UNOBSERVED = "UNOBSERVED"
    UNKNOWN = "UNKNOWN"


def dispersion_multiplier(verdict: ObserverVerdict) -> float:
    """觀測狀態 → 散布倍率。**`UNKNOWN` 走 1.0（fail open）**。

    判不出來時不加倍：地形服務中斷不該讓每一門砲的精度默默下降，那是把系統故障
    偽裝成戰術後果。而且 `STUB_GATEWAY`／開發環境本來就沒有真 gateway，
    fail open 讓既有行為完全不動。
    """
    return NO_OBSERVER_DISPERSION_MULT if verdict is ObserverVerdict.UNOBSERVED else 1.0


def _haversine_m(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    h = (
        math.sin(math.radians(b_lat - a_lat) / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b_lng - a_lng) / 2) ** 2
    )
    return 2 * _EARTH_R_M * math.asin(min(1.0, math.sqrt(h)))


@dataclass(frozen=True, slots=True)
class FireMissionCommand:
    """一道面目標射擊令（打座標）。與 `EngageCommand` 刻意分型——目標是點不是單位。"""

    order_id: str
    shooter_id: str
    target_lat: float
    target_lng: float
    rounds: int = 1
    # 下令時指名的武器（payload.weapon_id＝EquipmentInstance.id）；None＝取射程最遠的曲射武器。
    weapon_template_id: str | None = None
    # WP-C4c："SMOKE" → 發煙任務（生成煙幕、不產生傷亡）。
    ammo_type: str | None = None
    # WP-C10.3 時效：發令 tick 與有效 tick 數。**0＝永不過期**（中性預設）。
    # 判定放在裁決層而不是 drain：那裡有 `_reject()`，被擋下的火力任務本來就走那條路落帳。
    issued_at_tick: int = 0
    ttl_ticks: int = 0


class FireMissionOrderSource:
    """從 DB 拉本 session 的 VALIDATED FIRE_MISSION 並轉 EXECUTING（確定性排序）。

    通信閘門與 `EngageOrderSource` 同紀律（§6.2）：射手 OFFLINE 收不到新令、DEGRADED 延遲
    N ticks，被擋者留在 VALIDATED 待通信恢復。**火力任務尤其不該例外**——叫不到火力
    正是通信中斷最直接的後果。
    """

    def __init__(
        self,
        db: Session,
        session_id: str,
        hot_state: HotStateStore | None = None,
        clock: object | None = None,
    ) -> None:
        self._db = db
        self._session_id = session_id
        self._hot = hot_state
        self._clock = clock

    async def drain(self) -> list[FireMissionCommand]:
        orders = self._db.scalars(
            select(Order)
            .where(
                Order.session_id == self._session_id,
                Order.status == OrderStatus.VALIDATED,
                Order.order_type == OrderType.FIRE_MISSION.value,
            )
            .order_by(Order.issued_at_tick, Order.id)
        ).all()
        now_tick = self._clock.now().tick if self._clock is not None else None  # type: ignore[attr-defined]
        commands: list[FireMissionCommand] = []
        for order in orders:
            if self._hot is not None and now_tick is not None:
                state = self._hot.get_unit(order.unit_id) or {}
                link = parse_link_state(state.get("comms_state"))
                if not order_admissible(link, int(order.issued_at_tick or 0), now_tick):
                    continue  # 通信擋下 → 留 VALIDATED，本 tick 不執行
            payload = order.payload or {}
            order.status = next_status(order.status, OrderStatus.EXECUTING)
            try:
                lat = float(payload["target_lat"])
                lng = float(payload["target_lng"])
            except (KeyError, TypeError, ValueError):
                # payload 壞掉的令不能永遠卡在 VALIDATED 被反覆撈出來——就地判 REJECTED。
                # 先轉 EXECUTING 再轉 REJECTED：狀態機不容許 VALIDATED 直接跳終態。
                order.status = next_status(order.status, OrderStatus.REJECTED)
                continue
            wid = payload.get("weapon_id")
            raw_rounds = payload.get("rounds", 1)
            rounds = int(raw_rounds) if isinstance(raw_rounds, (int, float)) else 1
            commands.append(
                FireMissionCommand(
                    order_id=order.id,
                    shooter_id=order.unit_id,
                    issued_at_tick=int(order.issued_at_tick or 0),
                    ttl_ticks=ttl_of(payload),
                    target_lat=lat,
                    target_lng=lng,
                    rounds=max(1, rounds),
                    weapon_template_id=str(wid) if wid else None,
                    ammo_type=(str(payload.get("ammo_type")) if payload.get("ammo_type") else None),
                )
            )
        self._db.commit()
        return commands


class AreaFireAdjudicator:
    """把 `FireMissionCommand` 交給 `resolve_area_fire`，並把結果落到熱狀態 + DB + order 狀態。

    武器選定**一律看裝備類別**（`INDIRECT_CATEGORIES`），與預檢同一份規則——見該常數的註解。
    指名了直射武器 → 視同未指名，退回該單位射程最遠的曲射武器（令能通過預檢就代表它有一把）。
    """

    def __init__(
        self,
        db: Session,
        hot_state: HotStateStore,
        rng: DeterministicRNG,
        weapons_for: Callable[[str], Sequence[WeaponEntry]],
        faction_for: Callable[[str], str] | None = None,
        gateway: object | None = None,
        bda_rng: DeterministicRNG | None = None,
        relations: FactionRelations | None = None,
        session_id: str = "",
    ) -> None:
        self._db = db
        self._hot = hot_state
        self._rng = rng
        self._weapons_for = weapons_for
        self._faction_for = faction_for
        # WP-C10.4 觀測判定用的地形 LOS gateway。None＝判不出來（UNKNOWN → 不加倍）。
        self._gateway = gateway
        # WP-C10.4b BDA 誤差用的**獨立** RNG stream。與落點共用一條的話，
        # 「這次有沒有前觀」會決定抽樣次數，於是前觀死不死會改變後續每一發的落點。
        # None → 不發 BDA（既有測試/呼叫端零行為變更）。
        self._bda_rng = bda_rng
        # WP-C4c 發煙任務要把煙落庫，而煙是 session 範圍的實體。
        self._session_id = session_id
        # WP-C9：判「誰是友軍」的唯一判準。None → 退回字串相等（既有行為不變），
        # 但那對聯軍是錯的——盟軍傷亡會被算成正常戰果。正式路徑一定要注入。
        self._relations = relations

    def resolve(self, order: FireMissionCommand, now: SimTime) -> list[LedgerEvent]:
        shooter = self._hot.get_unit(order.shooter_id)
        if shooter is None:
            self._complete(order.order_id, now.tick)
            return []

        # WP-C10.3 逾時作廢。**在挑武器之前判**——過期的任務連挑砲都不必。
        # 這條存在的理由：射手斷聯時通信閘門會把令留在 VALIDATED，恢復通聯後才執行；
        # 對時效性火力而言，那等於把彈打到幾十個 tick 前的戰場。
        if expired({"ttl_ticks": order.ttl_ticks}, order.issued_at_tick, now.tick):
            late = now.tick - order.issued_at_tick
            return self._reject(
                order, now, "EXPIRED", f"逾時作廢（時效 {order.ttl_ticks} tick，已過 {late} tick）"
            )

        entry = self._pick_weapon(order)
        if entry is None:
            return self._reject(order, now, "NO_INDIRECT_WEAPON", "此單位無可用的曲射武器")

        aim = (order.target_lat, order.target_lng)
        reason = self._legality(order, entry, shooter, aim, now)
        if reason is not None:
            return self._reject(order, now, reason[0], reason[1])

        # 彈不夠就打不夠——**不是整道令作廢**。有幾發打幾發是砲兵的真實行為，
        # 而「本來要 12 發只打了 3 發」這件事要看得見，故記在事件裡。
        available = self._live_ammo(order.shooter_id, entry)
        fired = min(order.rounds, available)
        if fired <= 0:
            return self._reject(order, now, "NO_AMMO", "彈藥不足，無法執行火力任務")

        shooter_faction = self._faction_for(order.shooter_id) if self._faction_for else None
        # WP-C4c 發煙任務：生成煙幕、**不產生任何傷亡**。分流放在扣彈之後——
        # 發煙一樣要消耗彈藥（那是它的成本），但不走殺傷裁決。
        if (order.ammo_type or "").upper() == "SMOKE":
            return self._emplace_smoke(order, aim, fired, shooter_faction, now)
        # 目標清單同時是**觀測者候選池**——落點附近有座標的單位本來就要蒐集一次，
        # 沒必要為了找前觀再查一次 DB（而且熱狀態的戰力才看得出誰還活著）。
        targets = self._gather_targets()
        verdict = self.observer_verdict(targets, shooter_faction, aim)
        result = resolve_area_fire(
            entry.profile,
            aim,
            targets,
            self._rng,
            now.tick,
            shooter_id=order.shooter_id,
            shooter_faction=shooter_faction,
            # WP-C9：友軍＝關係矩陣說的友軍（含盟軍），不是字串相等。
            is_friendly_faction=self._friendly_test(shooter_faction),
            rounds=fired,
            dispersion_mult=dispersion_multiplier(verdict),
        )
        self._spend_ammo(order.shooter_id, entry, fired)
        self._count_mission(order.shooter_id)
        self._apply_losses(result.losses)
        # WP-C1：壓制半徑（＝殺傷半徑 ×3）內的每個單位都被壓制，不只挨了戰損的那些——
        # 砲彈在旁邊炸開卻沒傷到你，你照樣得趴下。這是砲兵最主要的用途，
        # 少了這一步「砲兵在模型裡就只剩一個效率很差的殺傷工具」（見 WP-C1 卡）。
        # 每個單位各自帶「有幾發落進它的壓制半徑」——齊放外緣的單位不該與正中心同等壓制。
        apply_area_suppression(self._hot, result.suppressed, entry.category)
        event = result.event
        if event is not None:
            if fired < order.rounds:
                event.ai_decision["rounds_requested"] = order.rounds
            # 觀測狀態與實際套用的倍率都寫進 ai_decision（**入 hash chain**）：
            # gateway 的答覆是外部狀態，重播重建不出來——不落帳的話「這次為什麼散布加倍」
            # 事後無從稽核。
            event.ai_decision["observation"] = verdict.value
            event.ai_decision["dispersion_mult"] = dispersion_multiplier(verdict)
        self._complete(order.order_id, now.tick)
        if event is None:
            return []
        bda = self._bda_event(order, verdict, shooter_faction, aim, result.losses, now)
        out = [event, bda] if bda is not None else [event]
        out.extend(self._fratricide_events(order, shooter_faction, result, now))
        # WP-C7.2：落點半徑內的補給點一併摧毀——**打擊敵後勤是可行戰法**。
        # 用既有的面射擊蒐集半徑，不另造一套「攻擊建物」的裁決。
        out.extend(self._destroy_supply_points(order, aim, entry, now))
        return out

    def _destroy_supply_points(
        self, order: Any, aim: tuple[float, float], entry: Any, now: SimTime
    ) -> list[LedgerEvent]:
        """摧毀落點殺傷半徑內的補給點（WP-C7.2）。回帳本事件。

        `lethal_radius_m` 為 0 的武器（直射範本）自然什麼都炸不到——不必特判。
        """
        from app.engine.supply_points import destroy_at

        radius = float(getattr(entry.profile, "lethal_radius_m", 0.0) or 0.0)
        if radius <= 0.0 or not self._session_id:
            return []
        ids = destroy_at(self._db, self._session_id, aim[0], aim[1], radius)
        return [
            LedgerEvent(
                event_type="SUPPLY_POINT_DESTROYED",
                tick=now.tick,
                initiator_id=order.shooter_id,
                ai_decision={"feature_id": fid},
            )
            for fid in ids
        ]

    def _fratricide_events(
        self, order: Any, shooter_faction: str | None, result: Any, now: SimTime
    ) -> list[LedgerEvent]:
        """誤傷了誰 → 每個受害單位**各一筆** `FRATRICIDE`（WP-C9）。

        ## 為什麼一個受害者一筆，不是一筆總結

        `event_audience` 由 `initiator_id`/`target_id` 的陣營推導受眾，**兩者都取不到陣營時
        回 None ＝全域廣播**。做成一筆總結事件（受害者塞進 `ai_decision`、`target_id` 留空）
        的話，聯軍誤傷會直接播給敵軍——他們立刻知道對面在自相殘殺。
        一個受害者一筆並填上 `target_id`，受眾自然收斂成「射手陣營 + 受害者陣營」。

        ## 為什麼不是靠 AREA_FIRE_RESOLVED 的 `friendly_losses` 就好

        那個欄位在 AAR 會被 `_FOGGED_DECISION_KEYS` 整個剝掉（對非全知觀點），
        而誤傷正是**當責者最需要看到**的一件事。獨立事件才有自己的受眾與書籤。
        """
        if not shooter_faction:
            return []
        friendly = result.event.ai_decision.get("friendly_losses") if result.event else None
        if not friendly:
            return []
        losses = result.losses or {}
        return [
            LedgerEvent(
                event_type="FRATRICIDE",
                tick=now.tick,
                initiator_id=order.shooter_id,
                target_id=victim,
                damage_calc=round(float(losses.get(victim, 0.0)), 3),
                # `ai_decision` 入 hash chain；`detail` 不入（可被竄改而不觸發 verify）。
                # 誤傷是要追究責任的事，證據一定放前者。
                ai_decision={
                    "cause": "AREA_FIRE",
                    "shooter_faction": shooter_faction,
                    "order_id": getattr(order, "order_id", None),
                },
            )
            for victim in friendly
        ]

    def _emplace_smoke(
        self, order: Any, aim: tuple[float, float], fired: int, faction: str | None, now: SimTime
    ) -> list[LedgerEvent]:
        """發煙任務（WP-C4c）：落點生成煙幕。

        **不抽落點散布**：煙幕的戰術意義是「這一片看不見」，一團 150 m 的煙本來就涵蓋
        CEP 等級的誤差；為它抽 Rayleigh 只會多動一次 RNG（擾動後續所有隨機序列）
        卻不改變任何可觀測結果。
        """
        from app.engine.smoke_wiring import emplace_smoke

        cloud = emplace_smoke(
            self._db,
            self._session_id,
            lat=aim[0],
            lng=aim[1],
            tick=now.tick,
            rounds=fired,
            owner_faction=faction or "",
        )
        self._complete(order.order_id, now.tick)
        return [
            LedgerEvent(
                event_type="SMOKE_EMPLACED",
                tick=now.tick,
                initiator_id=order.shooter_id,
                ai_decision={
                    "aim_lat": aim[0],
                    "aim_lng": aim[1],
                    "radius_m": cloud.radius_m,
                    "expires_at_tick": cloud.expires_at_tick,
                    "rounds": fired,
                },
            )
        ]

    def _friendly_test(self, shooter_faction: str | None) -> Callable[[str], bool] | None:
        """「這個陣營算不算射手的友軍」——注入給 `resolve_area_fire`（WP-C9）。

        沒有射手陣營或沒有關係矩陣 → None，純函數退回字串相等（既有行為不變）。
        """
        relations = self._relations
        if not shooter_faction or relations is None:
            return None
        return lambda f: is_friendly(relations, shooter_faction, f)

    def _bda_event(
        self,
        order: FireMissionCommand,
        verdict: ObserverVerdict,
        shooter_faction: str | None,
        aim: tuple[float, float],
        losses: dict[str, float],
        now: SimTime,
    ) -> LedgerEvent | None:
        """觀測者的戰果回報（WP-C10.4b）。**沒有觀測就沒有回報。**

        不是回報 0——那會被讀成「打了但沒傷到」，是另一種假情報。什麼都不發，
        射方就只知道「砲打出去了」，那正是沒有前觀時他實際擁有的資訊。

        `shooter_faction` 為空時也不發：`event_audience` 對沒有受眾線索的事件
        **退回全域廣播**，那會把戰果評估送給挨打的一方。
        """
        if self._bda_rng is None or verdict is not ObserverVerdict.OBSERVED:
            return None
        if not shooter_faction:
            return None
        return build_bda_event(
            tick=now.tick,
            shooter_id=order.shooter_id,
            shooter_faction=shooter_faction,
            aim=aim,
            truth=sum(losses.values()),
            rng=self._bda_rng,
            order_id=order.order_id,
        )

    def observer_verdict(
        self,
        targets: list[AreaTarget],
        shooter_faction: str | None,
        aim: tuple[float, float],
    ) -> ObserverVerdict:
        """射擊陣營對落點有沒有觀測（WP-C10.4）。

        候選＝**友軍（含盟軍）、還活著、在觀測距離內**的單位，依距落點遠近排序後最多探 8 次 LOS，
        看到一個就回。上限是必要的：tick 預算 200ms，而每次 LOS 是一趟 gRPC。

        **判不出來時回 `UNKNOWN` 而不是 `UNOBSERVED`**——見 `ObserverVerdict` 的說明。
        且**絕不讓例外逃出去**：`kernel.run_tick` 與 `run_paced` 對裁決都沒有防護，
        一個 `TerrainUnavailableError` 會讓 runner 崩潰、3 秒後被重建，
        在地形服務中斷期間變成重啟迴圈。
        """
        has_los = getattr(self._gateway, "has_los", None)
        if has_los is None or not shooter_faction:
            return ObserverVerdict.UNKNOWN
        # ⚠ **原本是 `t.faction == shooter_faction`**——盟軍的前觀不算，於是聯軍作戰時
        # 明明有友軍看著落點，散布卻照樣加倍。與 C9 的 `friendly_losses` 同一個 bug 家族。
        friendly = self._friendly_test(shooter_faction)
        candidates = [
            t
            for t in targets
            if (friendly(t.faction) if friendly else t.faction == shooter_faction)
            and (t.current_strength is None or t.current_strength > 0)
            and _haversine_m(t.lat, t.lng, *aim) <= _MAX_SPOT_RANGE_M
        ]
        if not candidates:
            return ObserverVerdict.UNOBSERVED
        candidates.sort(key=lambda t: (_haversine_m(t.lat, t.lng, *aim), t.unit_id))
        probed = 0
        for t in candidates[:_MAX_LOS_PROBES]:
            try:
                outcome = has_los((t.lat, t.lng, _OBS_HEIGHT_M), (aim[0], aim[1], _OBS_HEIGHT_M))
            except Exception:
                continue  # 這一個問不到；下一個。全都問不到 → UNKNOWN（見下）
            probed += 1
            if bool(getattr(outcome, "visible", False)):
                return ObserverVerdict.OBSERVED
        # 一個都問不出結果 → 是系統答不出來，不是「沒人看得到」。
        return ObserverVerdict.UNOBSERVED if probed else ObserverVerdict.UNKNOWN

    # ---- 內部 ----

    def _pick_weapon(self, order: FireMissionCommand) -> WeaponEntry | None:
        indirect = [
            e for e in self._weapons_for(order.shooter_id) if e.category in INDIRECT_CATEGORIES
        ]
        if not indirect:
            return None
        if order.weapon_template_id:
            named = [e for e in indirect if e.weapon_id == order.weapon_template_id]
            if named:
                return named[0]
        # 射程最遠者；同射程時取 weapon_id 較小者（穩定序 → 抽樣序列決定性）。
        return min(indirect, key=lambda e: (-e.profile.max_range_m, e.weapon_id))

    def _legality(
        self,
        order: FireMissionCommand,
        entry: WeaponEntry,
        shooter: dict[str, Any],
        aim: tuple[float, float],
        now: SimTime,
    ) -> tuple[str, str] | None:
        """射程 + 就位檢查。**下令當下過了不代表現在還過**——射手可能已經移動，故執行時重查。

        刻意不查 LOS：間瞄火力打的就是看不見的地方（與 `_precheck_fire_mission` 同一決定）。
        """
        try:
            s_lat, s_lng = float(shooter["lat"]), float(shooter["lng"])
        except (KeyError, TypeError, ValueError):
            return ("NO_POSITION", "射擊單位無座標")
        not_emplaced = self._emplacement_block(entry, shooter, now)
        if not_emplaced is not None:
            return not_emplaced
        dist = _haversine_m(s_lat, s_lng, *aim)
        if dist > entry.profile.max_range_m:
            return (
                "OUT_OF_RANGE",
                f"距離 {dist / 1000:.1f} km 超出射程 {entry.profile.max_range_m / 1000:.1f} km",
            )
        return None

    @staticmethod
    def _emplacement_block(
        entry: WeaponEntry, shooter: dict[str, Any], now: SimTime
    ) -> tuple[str, str] | None:
        """進入陣地後的待命時間（`emplace_ticks`）——**打完就跑之後不該能立刻再開火**。

        三道中性預設，任何一道成立就完全不擋（既有想定/既有 session 零影響）：
        1. `emplace_ticks <= 0`（未宣告）→ 不設限。
        2. 熱狀態沒有 `arrived_at_tick` → **視為早已就位**，不是「剛抵達」。
           從未移動過的單位、以及這張卡之前就存在的所有 session 都走這條。
        3. 已經等滿了 → 放行。
        """
        wait = entry.profile.emplace_ticks
        if wait <= 0:
            return None
        raw = shooter.get(ARRIVED_TICK_KEY)
        if not isinstance(raw, (int, float)):
            return None  # 缺鍵＝早已就位（中性預設）
        elapsed = now.tick - int(raw)
        if elapsed >= wait:
            return None
        return (
            "NOT_EMPLACED",
            f"進入陣地後尚未就位（還需 {wait - elapsed} tick，需 {wait} tick 展開）",
        )

    def _gather_targets(self) -> list[AreaTarget]:
        """全部有座標的單位——**敵我皆收**。只收敵軍等於把友軍傷害悄悄關掉。"""
        targets: list[AreaTarget] = []
        for unit_id, state in self._hot.get_all().items():
            lat, lng = state.get("lat"), state.get("lng")
            if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
                continue
            auth = state.get("authorized_strength")
            cur = state.get("strength", state.get("health"))
            targets.append(
                AreaTarget(
                    unit_id=unit_id,
                    faction=self._faction_for(unit_id) if self._faction_for else "",
                    lat=float(lat),
                    lng=float(lng),
                    armor_class=str(state.get("armor_class", "INFANTRY")),
                    current_strength=float(cur) if isinstance(cur, (int, float)) else None,
                    authorized_strength=float(auth) if isinstance(auth, (int, float)) else None,
                    platform_count=int(state.get("platform_count") or 1),
                    # WP-C1：**已就位**的姿態（缺鍵讀作 MOVING）。掘壕對砲擊的防護最有意義。
                    posture=str(state.get(POSTURE_KEY) or "MOVING"),
                    # WP-C3：縱隊擠在一條線上，挨砲最慘。
                    formation=str(state.get(FORMATION_KEY) or "COLUMN"),
                )
            )
        # 穩定序：純函數逐目標算距離不抽樣，順序不影響數值，但影響事件內字典的鍵序（hash chain）。
        targets.sort(key=lambda t: t.unit_id)
        return targets

    def _count_mission(self, shooter_id: str) -> None:
        """這門砲在這個陣地上又打了一次任務（WP-C10.5 陣地變換的計數）。

        **計「任務次數」不是「發數」**：發數由下令者自填、`rounds_per_mission` 又沒有
        任何程式讀，兩者都不是穩定的單位。計數落熱狀態——checkpoint 與 rollback 都會
        連它一起帶走，不像 `MselEngine._fired` 那種只活在行程記憶體裡的 set。

        射擊被物理擋下（`_reject`）不會走到這裡：沒打出去就不算暴露。
        """
        state = self._hot.get_unit(shooter_id) or {}
        raw = state.get(MISSION_COUNT_KEY, 0)
        current = int(raw) if isinstance(raw, (int, float)) else 0
        self._hot.update_unit(shooter_id, {MISSION_COUNT_KEY: current + 1})

    def _live_ammo(self, shooter_id: str, entry: WeaponEntry) -> int:
        state = self._hot.get_unit(shooter_id) or {}
        by_weapon = state.get("ammo_by_weapon")
        if isinstance(by_weapon, dict) and entry.weapon_id in by_weapon:
            raw = by_weapon[entry.weapon_id]
            return int(raw) if isinstance(raw, (int, float)) else 0
        return entry.ammo

    def _spend_ammo(self, shooter_id: str, entry: WeaponEntry, fired: int) -> None:
        state = self._hot.get_unit(shooter_id) or {}
        by_weapon = dict(state.get("ammo_by_weapon") or {})
        remaining = max(0, self._live_ammo(shooter_id, entry) - fired)
        by_weapon[entry.weapon_id] = remaining
        total = int(state.get("ammo", 0) or 0)
        self._hot.update_unit(
            shooter_id, {"ammo_by_weapon": by_weapon, "ammo": max(0, total - fired)}
        )
        # 持久化到 DB（#53 同紀律）：供 GET /weapons 顯示正確、sim 重啟續戰不回滿。
        inst = self._db.get(EquipmentInstance, entry.weapon_id)
        if inst is not None:
            inst.current_state = {**(inst.current_state or {}), "ammo": remaining}

    def _apply_losses(self, losses: dict[str, float]) -> None:
        """戰損落熱狀態 + DB。`health` 一律由戰力比導出（與交戰裁決同一條路徑）。"""
        for unit_id, loss in losses.items():
            if loss <= 0:
                continue
            state = self._hot.get_unit(unit_id) or {}
            auth = float(state.get("authorized_strength") or 100.0)
            cur = float(state.get("strength", state.get("health", auth)) or 0.0)
            after = max(0.0, cur - loss)
            health = effectiveness_pct(after / auth) if auth > 0 else 0.0
            self._hot.update_unit(unit_id, {"strength": after, "health": health})
            unit = self._db.get(TacticalUnit, unit_id)
            if unit is not None:
                unit.current_strength = after
                unit.health_status = health

    def _reject(
        self, order: FireMissionCommand, now: SimTime, reason: str, detail: str
    ) -> list[LedgerEvent]:
        """被物理擋下的火力任務**仍然落帳**——「叫了火力但沒打出去」是要能追究的事。"""
        event = LedgerEvent(
            event_type="AREA_FIRE_RESOLVED",
            tick=now.tick,
            initiator_id=order.shooter_id,
            damage_calc=0.0,
            ai_decision={
                "status": "REJECTED",
                "reason": reason,
                "reason_detail": detail,
                "aim_lat": order.target_lat,
                "aim_lng": order.target_lng,
            },
        )
        self._complete(order.order_id, now.tick)
        return [event]

    def _complete(self, order_id: str, tick: int) -> None:
        order = self._db.get(Order, order_id)
        if order is None:
            self._db.commit()
            return
        order.status = next_status(order.status, OrderStatus.COMPLETED)
        order.resolved_at_tick = tick
        self._db.commit()
