"""單位移動系統（O10.1 + #28）——執行 MOVE 指令：每 tick 讓單位朝目標/沿自訂路徑前進。

紅線遵循：決定性——只用注入的 `SimTime`、固定速度、與注入的 `DeterministicRNG`（stream=
"movement"）推進，**不碰牆鐘、不用裸 random**。Kernel 為熱狀態唯一寫入者：本系統經
`hot_state.update_unit` 累積 per-unit diff，由 Kernel 於 tick 末 `drain_diff` 廣播 STATE_DIFF。
DB 位置一併更新，讓 GET /units 反映最新位置、且斷線重連正確。

#28 強化：
  * 自訂路徑：payload.waypoints（[[lng,lat],…]）逐段（leg）前進；進度存 payload._leg。
  * 強穿耗損：admit（VALIDATED→首見）時，若整條路徑穿越不可通行標註（障礙/建築/不可通行地形），
    以注入的 rng 擲一次額外隨機耗損 → 扣 current_strength（DB + 熱狀態）並記 MOVE_ATTRITION。
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable

import h3
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.adjudication.daylight import move_speed_modifier as night_move_modifier
from app.adjudication.effectiveness import effectiveness_pct
from app.adjudication.formation import formation_of, march_speed_modifier
from app.adjudication.suppression import move_modifier
from app.comms import order_admissible, parse_link_state
from app.engine.clock import SimTime
from app.engine.daylight_wiring import LightClock
from app.engine.formation_wiring import FORMATION_KEY
from app.engine.obstacle_wiring import (
    apply_mine_suppression,
    is_engineer,
    road_is_cut,
    roll_mine_strike,
    transit_speed_multiplier,
    typed,
)
from app.engine.rng import DeterministicRNG
from app.engine.suppression_wiring import SUPPRESSION_KEY, interrupt_posture
from app.models import MapFeature, Order, OrderStatus, TacticalUnit
from app.movement.attrition import (
    Obstacle,
    classify_crossings,
    forced_extra_attrition,
    obstacle_from_feature,
    obstacles_at,
    route_distance_m,
)
from app.movement.fuel import UnitFuel, burn_fuel, load_unit_fuel
from app.movement.mobility import UnitMobility, resolve_unit_mobility
from app.movement.mobility_matrix import MobilityRules, default_rules
from app.movement.params import TEMPO_ATTRITION_FACTOR
from app.movement.router import PathFn, plan_route
from app.sim_params import SimParams
from app.state.hot_state import HotStateStore
from app.state.ledger import LedgerEvent
from app.weather import WeatherState, movement_mobility_modifier

# 單次行軍的耗損上限（佔進入時戰力）——避免長途一次歸零（強穿另有其上限）。
_MARCH_LOSS_CAP_PCT = 0.30
# #81 Phase B 地形調速：取樣單位所在 hex（與戰術格同解析度）→ terrain_class + slope。
_TERRAIN_RES = 8
_UNSET = object()  # _terrain_cost_cache 的哨兵（None＝不可通行，是合法快取值）。

# h3_list → {h3: (terrain_class, slope_deg)}；建不出/失敗回空 → 不調速（Phase A 行為）。
TerrainSampler = Callable[[list[str]], dict[str, tuple[str, float]]]

# 每小時公里 → 每 tick 公里 = speed_kmh × tick_rate_ms / 3_600_000
_MS_PER_H = 3_600_000.0

# 抵達 tick（熱狀態鍵）——`emplace_ticks`「進入陣地後待命多久才能開火」的計時起點。
# ⚠ **缺鍵＝視為早已就位**（不是「剛抵達」）。這是中性預設：從未移動過的單位、
# 以及這張卡之前就存在的所有 session，都不會被這道新閘門擋住。
ARRIVED_TICK_KEY = "arrived_at_tick"


def _haversine_km(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dphi = math.radians(b_lat - a_lat)
    dlmb = math.radians(b_lng - a_lng)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


def _step_towards(
    lat: float, lng: float, dlat: float, dlng: float, step_km: float
) -> tuple[float, float]:
    """由 (lat,lng) 朝 (dlat,dlng) 前進 step_km；剩餘距離 ≤ step 則直接到點（呼叫端判定）。"""
    dist = _haversine_km(lat, lng, dlat, dlng)
    if dist <= 1e-9:
        return dlat, dlng
    frac = step_km / dist
    return lat + (dlat - lat) * frac, lng + (dlng - lng) * frac


def _waypoints_of(payload: dict) -> list[tuple[float, float]]:  # type: ignore[type-arg]
    """payload.waypoints → [(lng,lat), …]（過濾壞點）；無則空清單。"""
    raw = payload.get("waypoints")
    if not isinstance(raw, list):
        return []
    out: list[tuple[float, float]] = []
    for p in raw:
        if isinstance(p, (list, tuple)) and len(p) >= 2:
            try:
                out.append((float(p[0]), float(p[1])))
            except (TypeError, ValueError):
                continue
    return out


class UnitMovementSystem:
    """滿足 Kernel 的 `MovementSystem` 介面。每 tick：撿起 VALIDATED MOVE → EXECUTING，
    朝目標/沿自訂路徑推進；到終點則標 COMPLETED。位置寫 DB + 熱狀態。"""

    def __init__(
        self,
        *,
        session_id: str,
        session_factory: sessionmaker,  # type: ignore[type-arg]
        hot_state: HotStateStore,
        tick_rate_ms: int,
        speed_kmh: float = 40.0,
        rng: DeterministicRNG | None = None,
        terrain_sampler: TerrainSampler | None = None,
        weather_mobility: float = 1.0,
        weather_for: Callable[[], WeatherState | None] | None = None,
        path_fn: PathFn | None = None,
        sim_params: SimParams | None = None,
        mobility_rules: MobilityRules | None = None,
        light: LightClock | None = None,
    ) -> None:
        self._session_id = session_id
        self._session_factory = session_factory
        self._hot_state = hot_state
        self._tick_rate_ms = tick_rate_ms
        # #93 推演參數（於 runner 啟動時載入一次）；None → 預設＝原硬編碼行為。
        self._params = sim_params or SimParams()
        # 後備每 tick 步距（無法由編裝導出機動時）；#80 起改為 per-unit（見 _step_sync admit）。
        self._step_km = speed_kmh * tick_rate_ms / _MS_PER_H
        # 強穿耗損的隨機來源（stream="movement"）；None 則停用（既有測試不注入）。
        self._rng = rng
        # #81 Phase B：地形取樣器（None＝不調速）+ 天氣機動修正 + 每格成本快取（地形靜態）。
        self._terrain_sampler = terrain_sampler
        # 全局後備倍率。**過去這是唯一的天氣入口，而且沒有任何呼叫端傳過它**——
        # 於是天氣對機動的影響（`movement_mobility_modifier`）整條是死碼：
        # 想定裡下暴雨，部隊照著晴天的速度行軍。
        self._weather_mobility = weather_mobility if weather_mobility > 0 else 1.0
        # WP-C4b：逐 tick、**逐格**的天氣。回呼而非值——傳一份快照進來，整局就永遠
        # 停在建構當下那一份（同 `make_engage_env` / `make_detect_env` 的紀律）。
        # None → 整段跳過，只用後備倍率＝既有行為，一個位元都不差。
        self._weather_for = weather_for
        self._terrain_cost_cache: dict[tuple[str, str], float | None] = {}
        # WP-B6 想定機動覆寫：**該局**的規則物件（None → 出貨預設）。刻意以建構參數注入而非
        # 改全域快取——同一 core 行程同時跑 N 局，全域可變狀態會跨局污染（見 mobility_matrix）。
        self._mobility = mobility_rules or default_rules()
        # #82 Phase C：地形 A* 路徑查詢（None＝不規劃，維持 Phase A/B 直線）。
        self._path_fn = path_fn
        # WP-C4a 晝夜。**該局沒宣告日出日落就是 None**，逐 tick 那段整個跳過——
        # 「一次都不算」比「算出來剛好是 1.0」更省，也更不會在改係數時意外動到既有局。
        self._light = light if (light is not None and light.declared) else None

    async def step(self, now: SimTime) -> list[LedgerEvent]:
        # 同步 DB/H3 計算移到執行緒，避免阻塞 event loop（HOW_TO §3.1）。
        return await asyncio.to_thread(self._step_sync, now)

    def _step_sync(self, now: SimTime) -> list[LedgerEvent]:
        events: list[LedgerEvent] = []
        with self._session_factory() as db:
            orders = (
                db.execute(
                    select(Order)
                    .where(
                        Order.session_id == self._session_id,
                        Order.order_type == "MOVE",
                        Order.status.in_([OrderStatus.VALIDATED, OrderStatus.EXECUTING]),
                    )
                    # ⚠ **紅線 1**：這一條 drain 消耗 `movement` RNG stream（觸雷擲骰、
                    # 強穿耗損）。主鍵是 uuid4，沒有排序就等於「同一份想定重開一局，
                    # 抽樣順序換人」。engine 底下另外五條 drain 都有這一行
                    # （其中兩處還註明「確定性順序」），偏偏會抽樣的這條漏了。
                    .order_by(Order.issued_at_tick, Order.id)
                )
                .scalars()
                .all()
            )
            # 只有本 tick 有新 admit（VALIDATED）的指令才載入阻礙標註（省 query）。
            obstacles: list[Obstacle] | None = None
            for o in orders:
                unit = db.get(TacticalUnit, o.unit_id)
                p = o.payload or {}
                if unit is None or unit.current_lat is None or unit.current_lng is None:
                    continue
                targets = self._targets(p, dest_h3=p.get("to_h3"), h3mod=h3)
                if not targets:
                    continue
                # admit（首見，status 仍 VALIDATED）：一次性解析機動速度 + 行軍/強穿耗損。
                if o.status == OrderStatus.VALIDATED:
                    # #33b 通信閘門：OFFLINE 收不到新指令、DEGRADED 延遲 N ticks（§6.2）。僅擋新
                    # 指令；已在執行者續行。靜默保留（不逐 tick 記事件避免洗版）。
                    if not self._comms_admits(o, now):
                        continue
                    # #80：per-unit 機動速度（由編裝導出）→ 存 payload._step_km（跨 tick 沿用）；
                    # #81：另存 _mobility_profile 供地形成本查表。
                    tempo = str(p.get("tempo") or "NORMAL")
                    # #93：徒步速度以**該局的推演參數**導出。過去這裡不傳，
                    # 而預覽端（api/movement）有傳——於是調了設定，預覽 ETA 變了、
                    # 實跑照舊速度走，正是 sim_params 模組說明「紀律 2」禁止的那個病。
                    mob = resolve_unit_mobility(
                        db,
                        o.unit_id,
                        foot_xc_kmh=self._params.foot_xc_kmh,
                        foot_road_kmh=self._params.foot_road_kmh,
                    )
                    p = {
                        **p,
                        "_step_km": mob.step_km(self._tick_rate_ms, tempo=tempo),
                        "_mobility_profile": mob.profile,
                        # #84：油耗率（0＝徒步/無油料模型 → 逐 tick 完全略過油料處理）。
                        "_fuel_burn_km": mob.fuel_burn_per_km,
                        # #83：沿路速度基準（road_kmh × tempo）——進入有路的格時改用。
                        "_road_step_km": mob.step_km(self._tick_rate_ms, on_road=True, tempo=tempo),
                    }
                    # #82 Phase C：規劃地形路徑（繞開不可通行）→ 存 _route_wp；規劃後重算 targets，
                    # 使行軍耗損依**實際繞行距離**計（繞遠路更耗）。使用者自訂 waypoints 不覆寫。
                    route_ev = None
                    if self._path_fn is not None and not _waypoints_of(p):
                        p, route_ev = self._plan_route(o, unit, p, mob.profile, now)
                        o.payload = p
                        targets = self._targets(p, dest_h3=p.get("to_h3"), h3mod=h3)
                        if not targets:
                            continue
                    else:
                        o.payload = p
                    if route_ev is not None:
                        events.append(route_ev)
                    # #84：出發前先驗油——油箱已乾則**根本沒出發**，不應承受行軍/強穿耗損。
                    if self._fuel_rate(p) > 0:
                        pre = load_unit_fuel(db, o.unit_id)
                        if pre.needs_fuel and pre.remaining <= 0:
                            events.append(self._halt_out_of_fuel(o, unit, p, pre, now))
                            continue
                    # #80 行軍耗損：依總路徑距離 × per-km 磨耗 × tempo（確定性；地形難度 Phase B）。
                    ev_m = self._apply_march_attrition(unit, targets, mob, tempo, now)
                    if ev_m is not None:
                        events.append(ev_m)
                    # 強穿障礙額外耗損（既有；RNG stream="movement"）。
                    if self._rng is not None:
                        if obstacles is None:
                            obstacles = self._load_obstacles(db)
                        ev = self._apply_forced_attrition(db, unit, targets, obstacles, now)
                        if ev is not None:
                            events.append(ev)
                # #84 油料（SPEC_FULL §5.3「油料耗盡無法移動」MUST）：每 tick 先查油 → 以剩餘油量
                # 夾住本 tick 可行距離 → 推進 → 依**實際位移**扣油並寫回 DB。油乾即停駛。
                fuel = None
                if self._fuel_rate(p) > 0:
                    fuel = load_unit_fuel(db, o.unit_id)
                    if fuel.needs_fuel and fuel.remaining <= 0:
                        events.append(self._halt_out_of_fuel(o, unit, p, fuel, now))
                        continue
                before = (float(unit.current_lat or 0.0), float(unit.current_lng or 0.0))
                cap = fuel.range_km() if fuel is not None and fuel.needs_fuel else None
                # WP-C2：逐 tick 障礙裁決需要當局的障礙清單。與強穿耗損共用同一份
                # lazy 載入（一 tick 至多一次 query）。
                if obstacles is None:
                    obstacles = self._load_obstacles(db)
                ev = self._advance_unit(
                    o, unit, p, targets, now, fuel_cap_km=cap, obstacles=obstacles
                )
                if ev is not None:
                    events.append(ev)
                if fuel is not None and fuel.needs_fuel:
                    moved = _haversine_km(
                        before[0],
                        before[1],
                        float(unit.current_lat or 0.0),
                        float(unit.current_lng or 0.0),
                    )
                    burn_fuel(fuel, moved)
                    self._hot_state.update_unit(o.unit_id, {"fuel": round(fuel.remaining, 2)})
                    # 抵達者本令已 COMPLETED（不覆寫）；未抵達卻油乾 → 停駛。
                    if fuel.remaining <= 0 and o.status is not OrderStatus.COMPLETED:
                        events.append(self._halt_out_of_fuel(o, unit, p, fuel, now))
            db.commit()
        return events

    def _comms_admits(self, o: Order, now: SimTime) -> bool:
        """通信閘門（§6.2）：OFFLINE 收不到新指令、DEGRADED 延遲送達。缺 comms_state → ONLINE。"""
        state = self._hot_state.get_unit(o.unit_id) or {}
        link = parse_link_state(state.get("comms_state"))
        return order_admissible(link, int(o.issued_at_tick or 0), now.tick)

    def _targets(
        self,
        payload: dict[str, object],
        *,
        dest_h3: object,
        h3mod: object,
    ) -> list[tuple[float, float]]:
        """回傳依序前進的目標點 [(lng,lat), …]（不含起點）。

        優先序：使用者自訂 waypoints（刻意畫的路線，尊重之）→ `_route_wp`（#82 地形規劃路徑）
        → 單一目的地（精確經緯或格心）。
        """
        wps = _waypoints_of(payload)
        if wps:
            return wps
        routed = _waypoints_of({"waypoints": payload.get("_route_wp")})
        if routed:
            return routed
        to_lat, to_lng = payload.get("to_lat"), payload.get("to_lng")
        if isinstance(to_lat, (int, float)) and isinstance(to_lng, (int, float)):
            return [(float(to_lng), float(to_lat))]
        if isinstance(dest_h3, str) and dest_h3:
            lat, lng = h3mod.cell_to_latlng(dest_h3)  # type: ignore[attr-defined]
            return [(float(lng), float(lat))]
        return []

    def _weather_mobility_at(self, lat: float, lng: float) -> float:
        """該座標所在格的機動天氣倍率。無回呼/無快照/查無該格 → 後備倍率（中性）。

        天氣格網的解析度由插件決定，core 不能假設——所以要先把座標換算到
        **快照自己的**解析度再查（同 `make_engage_env` 的做法）。
        """
        if self._weather_for is None:
            return self._weather_mobility
        try:
            state = self._weather_for()
        except Exception:  # 天氣服務抖一下不該讓全場停止行軍
            return self._weather_mobility
        if state is None:
            return self._weather_mobility
        cell = h3.latlng_to_cell(lat, lng, state.resolution())
        return self._weather_mobility * movement_mobility_modifier(state.effects_at(cell))

    def _advance_unit(
        self,
        o: Order,
        unit: TacticalUnit,
        payload: dict,  # type: ignore[type-arg]
        targets: list[tuple[float, float]],
        now: SimTime,
        fuel_cap_km: float | None = None,
        obstacles: list[Obstacle] | None = None,
    ) -> LedgerEvent | None:
        leg = payload.get("_leg", 0)
        leg = int(leg) if isinstance(leg, (int, float)) else 0
        leg = max(0, min(leg, len(targets) - 1))
        # #80 per-unit 步距：admit 時已由編裝導出存入 payload._step_km；缺則用後備常數。
        raw_step = payload.get("_step_km")
        if isinstance(raw_step, (int, float)) and raw_step > 0:
            step_km = float(raw_step)
        else:
            step_km = self._step_km
        # WP-C1：被壓制的部隊走不動——趴著推進本來就慢。同時把姿態打回 MOVING
        # （挖到一半的洞帶不走）。無壓制/已是 MOVING → 乘 1.0、不寫熱狀態，位元不變。
        hot_state = self._hot_state.get_unit(unit.id) or {}
        raw_sup = hot_state.get(SUPPRESSION_KEY)
        if isinstance(raw_sup, (int, float)) and raw_sup > 0:
            step_km *= move_modifier(float(raw_sup), self._params.suppression_move_penalty)
        # WP-C3：隊形的行軍速度倍率。縱隊最快、魚骨（停下來的警戒隊形）幾乎不動。
        # COLUMN（中性預設）＝1.0，既有局位元不變。
        step_km *= march_speed_modifier(formation_of(hot_state.get(FORMATION_KEY)))
        # WP-C4a：夜間行軍。**有夜視器材的單位不受罰**——那正是夜戰的關鍵差別。
        # 無宣告日出日落 → `self._light` 為 None → 整段跳過（一次都不算，位元不變）。
        if self._light is not None:
            step_km *= night_move_modifier(
                self._light.level_at(now), night_capable=self._unit_night_capable(unit)
            )
        interrupt_posture(self._hot_state, unit.id, now.tick)
        tgt_lng, tgt_lat = targets[leg]
        cur_lat, cur_lng = float(unit.current_lat or 0.0), float(unit.current_lng or 0.0)
        # #81 Phase B：以目前所在格地形類別+坡度調速；不可通行→停在此 + MOVE_BLOCKED。
        # WP-C4b：天氣倍率逐格查（泥濘/暴風雪只罰在那片天氣底下的部隊，不是全場齊罰）。
        weather_mob = self._weather_mobility_at(cur_lat, cur_lng)
        step_km *= weather_mob
        # WP-C2：先算出腳下有哪些障礙——**道路分支要用到它**（斷橋讓道路加速失效）。
        here = typed(obstacles or [])
        if here:
            here = obstacles_at((cur_lng, cur_lat), here)
        if self._terrain_sampler is not None:
            prof = payload.get("_mobility_profile")
            prof = prof if isinstance(prof, str) and prof else "FOOT"
            cell = h3.latlng_to_cell(cur_lat, cur_lng, _TERRAIN_RES)
            klass, _slope = self._terrain_of(cell)
            # #83 道路優先：此格有可用道路 → 改用道路速度基準，且**不套地形/坡度成本**
            # （路面已鋪整；林中公路不該按森林算）。
            road_cls = klass.split("|", 1)[1] if "|" in klass else ""
            factor = self._mobility.road_speed_factor(prof, road_cls) if road_cls else None
            # WP-C2 斷橋：橋斷了就**不能再沿路走**——道路加速失效，退回地形成本。
            # 斷橋刻意不是「減速倍率」（炸斷的橋不會讓你走得慢，它讓你得繞路或涉水），
            # 所以它的效果只能接在這裡，不在下面的障礙倍率那一段。
            if factor is not None and road_is_cut(here):
                factor = None
            if factor is not None:
                raw_road = payload.get("_road_step_km")
                if isinstance(raw_road, (int, float)) and raw_road > 0:
                    step_km = float(raw_road) * weather_mob * factor
            else:
                cost = self._terrain_cost(cell, prof)
                if cost is None:
                    return self._block_impassable(o, unit, cell, prof, now)
                step_km /= cost  # 成本↑＝越難走＝步距縮短
        # WP-C2：站在障礙裡 → 速度倍率（鐵絲網/戰車壕 ×0.1）。**在道路加速之後**乘：
        # 障礙就是拿來卡住道路的，讓道路基準把它蓋掉等於障礙對主要接近路線無效。
        if here:
            engineer = is_engineer(unit)
            step_km *= transit_speed_multiplier(here, engineer=engineer)
            ev_mine = self._roll_mine(unit, here, step_km, engineer, now)
            if ev_mine is not None:
                # 觸雷 → 本令**停在原地結束**（COMPLETED），要繼續得重下令。
                # 「炸完照走」會讓雷區只剩扣血，失去它真正的價值：把縱隊釘住。
                o.status = OrderStatus.COMPLETED
                return ev_mine
        # #84：本 tick 行程不得超過剩餘油量所能支撐的距離（開到沒油就停在那裡，不會超跑）。
        if fuel_cap_km is not None:
            step_km = min(step_km, max(0.0, fuel_cap_km))
        remaining = _haversine_km(cur_lat, cur_lng, tgt_lat, tgt_lng)
        if remaining <= step_km:
            # 抵達此段終點。
            unit.current_lat, unit.current_lng = float(tgt_lat), float(tgt_lng)
            self._hot_state.update_unit(o.unit_id, {"lat": tgt_lat, "lng": tgt_lng})
            if leg >= len(targets) - 1:
                o.status = OrderStatus.COMPLETED
                # WP-C10.5 `emplace_ticks` 的計時起點。**在本卡之前程式裡沒有任何
                # 「單位停下來了」的時間戳**：`interrupt_posture` 對已在 MOVING 的單位不寫入、
                # 抵達後沒有東西重設姿態、`UNIT_ARRIVED` 只是帳本事件（不進熱狀態）、
                # 而 MOVE 令的 `resolved_at_tick` 在活執行期根本沒被寫。
                # 沒有這個鍵，emplace_ticks 就無從計時。
                self._hot_state.update_unit(o.unit_id, {ARRIVED_TICK_KEY: now.tick})
                return LedgerEvent(
                    event_type="UNIT_ARRIVED",
                    tick=now.tick,
                    initiator_id=o.unit_id,
                    detail={"order_id": o.id, "lat": tgt_lat, "lng": tgt_lng},
                )
            # 續往下一段（進度存回 payload）。
            o.payload = {**payload, "_leg": leg + 1}
            o.status = OrderStatus.EXECUTING
            return LedgerEvent(
                event_type="UNIT_MOVED",
                tick=now.tick,
                initiator_id=o.unit_id,
                detail={"order_id": o.id, "lat": tgt_lat, "lng": tgt_lng, "leg": leg + 1},
            )
        nlat, nlng = _step_towards(cur_lat, cur_lng, tgt_lat, tgt_lng, step_km)
        unit.current_lat, unit.current_lng = float(nlat), float(nlng)
        if o.status != OrderStatus.EXECUTING:
            o.status = OrderStatus.EXECUTING
        self._hot_state.update_unit(o.unit_id, {"lat": nlat, "lng": nlng})
        return LedgerEvent(
            event_type="UNIT_MOVED",
            tick=now.tick,
            initiator_id=o.unit_id,
            detail={"order_id": o.id, "lat": nlat, "lng": nlng},
        )

    @staticmethod
    def _unit_night_capable(unit: TacticalUnit) -> bool:
        """單位有沒有夜視器材（`attributes.night_capable`）。缺鍵→False。

        與感測器的 `night_capable` 分開：那個是「這具感測器看不看得見」，
        這個是「這支部隊摸不摸得黑走路」。同一個連可以有夜視鏡卻沒有駕駛用夜視。
        """
        attrs = unit.attributes if isinstance(unit.attributes, dict) else {}
        return bool(attrs.get("night_capable"))

    def _roll_mine(
        self,
        unit: TacticalUnit,
        here: list[Obstacle],
        step_km: float,
        engineer: bool,
        now: SimTime,
    ) -> LedgerEvent | None:
        """WP-C2 觸雷：扣戰力 + 壓制 + 停止。沒觸雷回 None。

        用 stream="movement"（與強穿耗損同串流）。`self._rng is None` 的路徑（無 RNG 的
        測試/重播）一律不觸雷——寧可不炸也不要用非決定性的來源補。
        """
        if self._rng is None:
            return None
        hit = roll_mine_strike(
            here,
            step_km,
            self._rng,
            engineer=engineer,
            # #93：雷區殺傷力可調。**過去這三個係數在 SimParams 裡但引擎讀模組常數**
            # ——調了完全沒作用。
            p_per_km=self._params.mine_strike_p_per_km,
            engineer_mult=self._params.engineer_mine_strike_mult,
        )
        if hit is None:
            return None
        before = float(unit.current_strength)
        after = max(0.0, before - self._params.mine_strike_strength_loss)
        unit.current_strength = after
        authorized = float(unit.authorized_strength) or 100.0
        self._hot_state.update_unit(
            unit.id, {"strength": after, "health": effectiveness_pct(after / authorized)}
        )
        apply_mine_suppression(self._hot_state, unit.id)
        return LedgerEvent(
            event_type="MINE_STRIKE",
            tick=now.tick,
            initiator_id=unit.id,
            damage_calc=before - after,
            detail={
                "feature_id": hit.feature_id,
                "label": hit.label,
                "engineer": engineer,
                "strength_before": before,
                "strength_after": after,
                "lat": float(unit.current_lat or 0.0),
                "lng": float(unit.current_lng or 0.0),
            },
        )

    @staticmethod
    def _fuel_rate(payload: dict) -> float:  # type: ignore[type-arg]
        """本令的每公里油耗（admit 時由編裝導出存入）。0＝不受油料限制（徒步等）。"""
        rate = payload.get("_fuel_burn_km")
        return float(rate) if isinstance(rate, (int, float)) and rate > 0 else 0.0

    def _halt_out_of_fuel(
        self,
        o: Order,
        unit: TacticalUnit,
        payload: dict,  # type: ignore[type-arg]
        fuel: UnitFuel,
        now: SimTime,
    ) -> LedgerEvent:
        """油盡 → 停駛（本令結束、單位留原地）。同 #81 MOVE_BLOCKED 機制（COMPLETED＋事件）。

        事件名 `MOVE_HALTED_FUEL`（與 MOVE_BLOCKED/MOVE_ATTRITION 同前綴）：廣播器不送 detail，
        故**必須**用獨立 event_type 才能在 COP 戰況列區分「不可通行」與「沒油」。
        補給後需重下 MOVE 令才能再動（COMPLETED 不會自行續跑）。
        """
        o.status = OrderStatus.COMPLETED
        self._hot_state.update_unit(o.unit_id, {"fuel": 0.0})
        return LedgerEvent(
            event_type="MOVE_HALTED_FUEL",
            tick=now.tick,
            initiator_id=o.unit_id,
            detail={
                "order_id": o.id,
                "reason": "OUT_OF_FUEL",
                "profile": str(payload.get("_mobility_profile") or "?"),
                "fuel_remaining": round(fuel.remaining, 3),
                "fuel_burn_per_km": round(fuel.burn_per_km, 3),
                "lat": float(unit.current_lat or 0.0),
                "lng": float(unit.current_lng or 0.0),
            },
        )

    def _plan_route(
        self,
        o: Order,
        unit: TacticalUnit,
        payload: dict,  # type: ignore[type-arg]
        profile: str,
        now: SimTime,
    ) -> tuple[dict, LedgerEvent | None]:  # type: ignore[type-arg]
        """規劃地形路徑存入 payload._route_wp（#82）。回 (payload, 事件|None)。

        任意點位：由單位**當前精確座標**出發、以**精確目的地**作結（見 movement/router）。
        規劃失敗/不可達 → 退回直線（payload 不變）並記 MOVE_ROUTE_FALLBACK 供觀測。
        """
        dest = self._dest_latlng(payload)
        if dest is None:
            return payload, None
        route = plan_route(
            self._path_fn,  # type: ignore[arg-type]
            start_lat=float(unit.current_lat or 0.0),
            start_lng=float(unit.current_lng or 0.0),
            dest_lat=dest[0],
            dest_lng=dest[1],
            profile=profile,
        )
        if not route.routed:
            # 退回直線：不阻擋移動（避免超出地形快取範圍的長距離誤拒），但留下可觀測記錄。
            if route.reason != "same_cell":
                return payload, LedgerEvent(
                    event_type="MOVE_ROUTE_FALLBACK",
                    tick=now.tick,
                    initiator_id=o.unit_id,
                    detail={"order_id": o.id, "reason": route.reason, "profile": profile},
                )
            return payload, None
        return {**payload, "_route_wp": [[lng, lat] for lng, lat in route.waypoints]}, LedgerEvent(
            event_type="MOVE_ROUTE_PLANNED",
            tick=now.tick,
            initiator_id=o.unit_id,
            detail={"order_id": o.id, "legs": route.hops, "profile": profile},
        )

    @staticmethod
    def _dest_latlng(payload: dict) -> tuple[float, float] | None:  # type: ignore[type-arg]
        """由 payload 取精確目的地 (lat,lng)：優先 to_lat/to_lng，否則 to_h3 格心。"""
        to_lat, to_lng = payload.get("to_lat"), payload.get("to_lng")
        if isinstance(to_lat, (int, float)) and isinstance(to_lng, (int, float)):
            return float(to_lat), float(to_lng)
        dest_h3 = payload.get("to_h3")
        if isinstance(dest_h3, str) and dest_h3:
            lat, lng = h3.cell_to_latlng(dest_h3)
            return float(lat), float(lng)
        return None

    def _terrain_of(self, cell: str) -> tuple[str, float]:
        """取該格的 (terrain_class[|road_class], slope)。取樣失敗/無取樣器 → ("", 0.0)。"""
        if self._terrain_sampler is None:
            return ("", 0.0)
        try:
            info = self._terrain_sampler([cell])
        except Exception:
            return ("", 0.0)
        return info.get(cell, ("", 0.0))

    def _terrain_cost(self, cell: str, profile: str) -> float | None:
        """該格對此 profile 的通行成本倍率（快取；不可通行回 None）。

        地形靜態 → 以 (cell, profile) 快取跨 tick/單位共用。取樣失敗（terrain 服務暫不可用）→ 回 1.0
        不快取（下次重試）——與交戰「地形服務中斷不凍結戰鬥」同紀律，且保持決定性退化。
        """
        key = (cell, profile)
        cached = self._terrain_cost_cache.get(key, _UNSET)
        if cached is not _UNSET:
            return cached  # type: ignore[return-value]
        assert self._terrain_sampler is not None
        try:
            info = self._terrain_sampler([cell])
        except Exception:
            return 1.0
        ct = info.get(cell)
        # #83：取樣值可能是 "FOREST|primary"，地形成本只看 terrain_class 部分。
        cost = (
            self._mobility.step_cost(profile, ct[0].split("|", 1)[0], ct[1])
            if ct is not None
            else 1.0
        )
        self._terrain_cost_cache[key] = cost
        return cost

    def _block_impassable(
        self, o: Order, unit: TacticalUnit, cell: str, profile: str, now: SimTime
    ) -> LedgerEvent:
        """單位進入不可通行地形 → 停在此、移動中止（COMPLETED）並記 MOVE_BLOCKED（#81）。"""
        o.status = OrderStatus.COMPLETED
        return LedgerEvent(
            event_type="MOVE_BLOCKED",
            tick=now.tick,
            initiator_id=o.unit_id,
            detail={
                "order_id": o.id,
                "reason": "IMPASSABLE_TERRAIN",
                "profile": profile,
                "cell": cell,
                "lat": float(unit.current_lat or 0.0),
                "lng": float(unit.current_lng or 0.0),
            },
        )

    def _load_obstacles(self, db: object) -> list[Obstacle]:
        rows = (
            db.execute(  # type: ignore[attr-defined]
                # 排序同上：障礙的並列順序決定觸雷擲骰與強穿耗損的套用次序。
                select(MapFeature)
                .where(MapFeature.session_id == self._session_id)
                .order_by(MapFeature.id)
            )
            .scalars()
            .all()
        )
        out: list[Obstacle] = []
        for f in rows:
            obs = obstacle_from_feature(
                {
                    "id": f.id,
                    "kind": f.kind,
                    "geometry_type": f.geometry_type,
                    "geometry": f.geometry,
                    "label": f.label,
                    "influence_radius_m": f.influence_radius_m,
                    "attributes": f.attributes,
                }
            )
            if obs is not None:
                out.append(obs)
        return out

    def _apply_march_attrition(
        self,
        unit: TacticalUnit,
        targets: list[tuple[float, float]],
        mob: UnitMobility,
        tempo: str,
        now: SimTime,
    ) -> LedgerEvent | None:
        """行軍耗損（#80）：總路徑距離 × per-km 磨耗 × tempo 扣戰力（確定性；地形難度待 Phase B）。

        於 admit 一次性套用（比照強穿）；march 先於強穿，兩者對剩餘戰力依序扣減、各記事件。
        """
        before = float(unit.current_strength)
        if before <= 0.0 or not targets:
            return None
        route = [(float(unit.current_lng or 0.0), float(unit.current_lat or 0.0)), *targets]
        dist_km = route_distance_m(route) / 1000.0
        if dist_km <= 0.0:
            return None
        per_km = self._params.attrition_for(mob.profile)
        tempo_mult = TEMPO_ATTRITION_FACTOR.get(tempo, 1.0)
        loss = min(dist_km * per_km * tempo_mult, before * _MARCH_LOSS_CAP_PCT)
        if loss <= 0.0:
            return None
        after = max(0.0, before - loss)
        unit.current_strength = after
        authorized = float(unit.authorized_strength) or 100.0
        health = effectiveness_pct(after / authorized)
        self._hot_state.update_unit(unit.id, {"strength": after, "health": health})
        return LedgerEvent(
            event_type="MOVE_ATTRITION",
            tick=now.tick,
            initiator_id=unit.id,
            damage_calc=round(loss, 4),
            detail={
                "reason": "MARCH",
                "profile": mob.profile,
                "tempo": tempo,
                "distance_km": round(dist_km, 3),
                "strength_before": before,
                "strength_after": after,
            },
        )

    def _apply_forced_attrition(
        self,
        db: object,
        unit: TacticalUnit,
        targets: list[tuple[float, float]],
        obstacles: list[Obstacle],
        now: SimTime,
    ) -> LedgerEvent | None:
        """整條路徑（起點 + targets）若穿越阻礙 → 擲一次額外隨機耗損，扣戰力並記事件。"""
        if not obstacles or self._rng is None:
            return None
        route = [(float(unit.current_lng or 0.0), float(unit.current_lat or 0.0)), *targets]
        crossings = classify_crossings(route, obstacles)
        if not crossings:
            return None
        before = float(unit.current_strength)
        loss = forced_extra_attrition(crossings, before, self._rng)
        if loss <= 0.0:
            return None
        after = max(0.0, before - loss)
        unit.current_strength = after
        authorized = float(unit.authorized_strength) or 100.0
        health = effectiveness_pct(after / authorized)
        self._hot_state.update_unit(unit.id, {"strength": after, "health": health})
        return LedgerEvent(
            event_type="MOVE_ATTRITION",
            tick=now.tick,
            initiator_id=unit.id,
            damage_calc=loss,
            detail={
                "reason": "FORCED_CROSSING",
                "crossings": [{"feature_id": c.feature_id, "kind": c.kind} for c in crossings],
                "strength_before": before,
                "strength_after": after,
            },
        )
