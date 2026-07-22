"""交戰裁決在活執行期（sim_runtime）的接線輔助（新 #1）。

O3.6 已有純函數裁決引擎（adjudication/）與接線層（EngageOrderSource/EngagementAdjudicator），
但活執行期（sim_runtime）先前僅接 movement，order_source/adjudicator 為 NoOp——故 ENGAGE 令
永遠停在 VALIDATED、不造成戰損。本模組補上活執行期所需的三件事：

1. `WeaponResolver`：unit_id → 可用 KINETIC 武器（供 weapon_for；honor 選武器或退回主武器）。
2. `seed_combat_state`：把單位血量/裝甲/彈藥/座標播入熱狀態（裁決讀熱狀態，缺則無從計算）。
3. `make_engage_env`：由熱狀態座標算射程 → EnvSnapshot（最小版：los_clear=True、係數預設 1）。

**紅線**：物理裁決仍是 adjudication/ 的純函數；本模組只做 I/O 邊界（讀 DB/熱狀態、組 EnvSnapshot）。
"""

from __future__ import annotations

import math

import h3
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adjudication.adjudicator import EngageCommand
from app.adjudication.effectiveness import effectiveness_pct
from app.adjudication.engagement import EnvSnapshot
from app.adjudication.weapon import WeaponProfile
from app.models import EquipmentInstance, EquipmentTemplate, TacticalUnit
from app.state.hot_state import HotStateStore
from app.terrain import engagement_cover_modifier
from app.weather import WeatherState, engagement_weather_modifier

_EARTH_R_M = 6_371_000.0

# 可產生 WeaponProfile 的裝備類別（baseStats allOf kinetic → 有 max_range/ph/傷害/pk）。
# KINETIC 直射動能、ARTILLERY 火砲間瞄、MISSILE 飛彈導引皆走同一資料驅動裁決管線。
_WEAPON_CATEGORIES = frozenset({"KINETIC", "ARTILLERY", "MISSILE"})

# 無武器單位的退化 profile：射程近乎 0 → 任何真實距離皆 OUT_OF_RANGE → 交戰被物理拒絕（非崩潰）。
_NO_WEAPON = WeaponProfile.from_base_stats(
    {
        "max_range_m": 0.001,
        "ph_by_range_band": [[0.001, 0.0]],
        "damage_by_armor_class": {},
        "ammo_types": [],
    }
)


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (
        math.sin(math.radians(lat2 - lat1) / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2
    )
    return 2 * _EARTH_R_M * math.asin(min(1.0, math.sqrt(a)))


class WeaponResolver:
    """把交戰命令映到 WeaponProfile：優先 honor 下令選定的武器範本，否則取單位主武器（#11）。

    活執行期建構一次，快取「範本 → profile」與「單位 → 主武器 profile + 主武器彈藥」。
    """

    def __init__(self, db: Session, session_id: str) -> None:
        # 以「武器範本 id」與「裝備實例 id」雙鍵登錄——COP 下令的 weapon_id 實為 EquipmentInstance.id
        # （見 GET /units/{id}/weapons 回傳 id=inst.id），故兩者皆需可對應到 profile。
        self._by_weapon: dict[str, WeaponProfile] = {}
        self._primary: dict[str, WeaponProfile] = {}
        self._primary_ammo: dict[str, int] = {}
        # #30 squad 火力容量：weapon 範本/實例 id → 建制數量；unit → 主武器建制數量。
        self._qty_by_weapon: dict[str, int] = {}
        self._primary_qty: dict[str, int] = {}
        self._build(db, session_id)

    def _build(self, db: Session, session_id: str) -> None:
        units = db.scalars(select(TacticalUnit).where(TacticalUnit.session_id == session_id)).all()
        for unit in units:
            instances = db.scalars(
                select(EquipmentInstance).where(EquipmentInstance.owner_id == unit.id)
            ).all()
            best: WeaponProfile | None = None
            best_ammo = 0
            best_qty = 1
            for inst in instances:
                tmpl = db.get(EquipmentTemplate, inst.template_id)
                if tmpl is None or tmpl.category not in _WEAPON_CATEGORIES:
                    continue
                try:
                    profile = WeaponProfile.from_base_stats(tmpl.base_stats)
                except (ValueError, KeyError, TypeError):
                    continue  # baseStats 壞 → 略過
                self._by_weapon[inst.template_id] = profile
                self._by_weapon[inst.id] = profile  # COP weapon_id = 實例 id
                qty = (
                    int(inst.quantity)
                    if isinstance(inst.quantity, int) and inst.quantity >= 1
                    else 1
                )
                self._qty_by_weapon[inst.template_id] = qty
                self._qty_by_weapon[inst.id] = qty
                ammo = _ammo_of(inst)
                # 主武器＝射程最遠者（活執行期最小版；未指定選武器時的預設）。
                if best is None or profile.max_range_m > best.max_range_m:
                    best, best_ammo, best_qty = profile, ammo, qty
            if best is not None:
                self._primary[unit.id] = best
                self._primary_ammo[unit.id] = best_ammo
                self._primary_qty[unit.id] = best_qty

    def weapon_for(self, cmd: EngageCommand) -> WeaponProfile:
        if cmd.weapon_template_id and cmd.weapon_template_id in self._by_weapon:
            return self._by_weapon[cmd.weapon_template_id]
        return self._primary.get(cmd.shooter_id, _NO_WEAPON)

    def primary_ammo(self, unit_id: str) -> int:
        """單位主武器初始彈藥（供熱狀態 seed；活執行期以單一 ammo 純量近似）。"""
        return self._primary_ammo.get(unit_id, 0)

    def quantity_for(self, cmd: EngageCommand) -> int:
        """射手選定武器的建制數量（#30）：honor 選武器，否則主武器；缺則 1（單體）。"""
        if cmd.weapon_template_id and cmd.weapon_template_id in self._qty_by_weapon:
            return self._qty_by_weapon[cmd.weapon_template_id]
        return self._primary_qty.get(cmd.shooter_id, 1)


def _ammo_of(inst: EquipmentInstance) -> int:
    raw = inst.current_state.get("ammo") if isinstance(inst.current_state, dict) else None
    return int(raw) if isinstance(raw, (int, float)) else 0


def _platform_count_of(unit: TacticalUnit) -> int:
    """單位的平台/建制數：attributes.platform_count 優先，否則 personnel_current，皆無則 1（單體）。

    決定「每平台戰力」＝authorized/platform_count——大部隊每次命中僅損一個平台份量（漸進消耗），
    單體則整個承受（易毀）。legacy 未設者退回 1（如同單體），待 orbat 補 platform_count。
    """
    pc = unit.attributes.get("platform_count") if isinstance(unit.attributes, dict) else None
    if isinstance(pc, (int, float)) and pc >= 1:
        return int(pc)
    if isinstance(unit.personnel_current, int) and unit.personnel_current >= 1:
        return unit.personnel_current
    return 1


def seed_combat_state(
    db: Session, hot: HotStateStore, session_id: str, resolver: WeaponResolver
) -> int:
    """把單位戰鬥狀態播入熱狀態，供裁決引擎讀取。回傳處理的單位數。

    座標永遠以 DB 同步（權威）；血量/裝甲/彈藥僅在熱狀態尚無時播入——避免執行期重啟時把
    交戰進度（Redis 內已扣的血量/彈藥）重置回 DB 初值。
    """
    units = db.scalars(select(TacticalUnit).where(TacticalUnit.session_id == session_id)).all()
    for unit in units:
        existing = hot.get_unit(unit.id) or {}
        patch: dict[str, object] = {}
        if unit.current_lat is not None and unit.current_lng is not None:
            patch["lat"] = unit.current_lat
            patch["lng"] = unit.current_lng
        authorized = float(unit.authorized_strength) or 100.0
        if "strength" not in existing:
            patch["strength"] = float(unit.current_strength)
        if "authorized_strength" not in existing:
            patch["authorized_strength"] = authorized
        if "platform_count" not in existing:
            patch["platform_count"] = _platform_count_of(unit)
        if "health" not in existing:
            # health＝由當前戰力比導出的效能%（與 strength 一致，不再是獨立 HP）。
            patch["health"] = effectiveness_pct(float(unit.current_strength) / authorized)
        if "armor_class" not in existing:
            ac = unit.attributes.get("armor_class") if isinstance(unit.attributes, dict) else None
            patch["armor_class"] = str(ac) if ac else "INFANTRY"
        if "ammo" not in existing:
            patch["ammo"] = resolver.primary_ammo(unit.id)
        if patch:
            hot.update_unit(unit.id, patch)
    return len(units)


# 交戰觀測/目標離地高度（與 precheck._ENGAGE_OBS_M 一致），供地形 LOS 查詢。
_ENGAGE_OBS_M = 10.0


def _weather_res(weather: WeatherState) -> int:
    """由天氣快照的 cell 鍵推得其 h3 解析度（供把射手座標換算到同解析度查修正）。預設 8。"""
    cells = getattr(weather, "_cells", None)
    if isinstance(cells, dict) and cells:
        try:
            return int(h3.get_resolution(next(iter(cells))))
        except (ValueError, TypeError):
            return 8
    return 8


def make_engage_env(  # type: ignore[no-untyped-def]
    hot: HotStateStore, gateway: object | None = None, weather: WeatherState | None = None
):
    """回傳 env_for(shooter, target, indirect_fire) → EnvSnapshot（射程+地形LOS+天氣，Phase3）。

    - range_m：由射手/目標座標 haversine。缺失 → 極大 → OUT_OF_RANGE 拒絕（安全退化）。
    - los_clear：有 `gateway`（terrain gRPC）時查真實視線（離地各 10m）；目標退入稜線→直瞄遮蔽。
      無 gateway 或 RPC 失敗 → True（地形服務中斷不凍結戰鬥）。
    - weather_modifier：有 `weather` 快照時取射手 cell 的交戰天氣修正（直瞄看能見度、間瞄看散佈）。
      無天氣或查無 cell → CLEAR（1.0）。給定座標/快照具決定性 → replay 安全。
    - terrain_cover_modifier（STEP3）：由視線最小餘隙導出——掠地射擊＝目標半遮蔽 → 命中降低（直瞄）；
      間瞄不受地形遮蔽。無 gateway/餘隙 → 1.0。
    """
    w_res = _weather_res(weather) if weather is not None else 8

    def env_for(shooter_id: str, target_id: str, indirect_fire: bool = False) -> EnvSnapshot:
        s = hot.get_unit(shooter_id) or {}
        t = hot.get_unit(target_id) or {}
        try:
            s_lat, s_lng = float(s["lat"]), float(s["lng"])
            t_lat, t_lng = float(t["lat"]), float(t["lng"])
            range_m = _haversine_m(s_lat, s_lng, t_lat, t_lng)
        except (KeyError, TypeError, ValueError):
            return EnvSnapshot(range_m=float("inf"), los_clear=True)
        los_clear = True
        cover_mod = 1.0
        if gateway is not None:
            try:
                outcome = gateway.has_los(  # type: ignore[attr-defined]
                    (s_lat, s_lng, _ENGAGE_OBS_M), (t_lat, t_lng, _ENGAGE_OBS_M)
                )
                los_clear = bool(outcome.visible)
                # 地形遮蔽命中修正（STEP3）：由最小餘隙導出——掠地射擊代表目標半遮蔽 → 較難命中。
                # 間瞄彈道越過地形，不受遮蔽。
                if los_clear and not indirect_fire:
                    cover_mod = engagement_cover_modifier(getattr(outcome, "clearance_m", None))
            except Exception:
                los_clear = True
        weather_mod = 1.0
        if weather is not None:
            effects = weather.effects_at(h3.latlng_to_cell(s_lat, s_lng, w_res))
            weather_mod = engagement_weather_modifier(effects, indirect_fire)
        return EnvSnapshot(
            range_m=range_m,
            los_clear=los_clear,
            weather_modifier=weather_mod,
            terrain_cover_modifier=cover_mod,
        )

    return env_for
