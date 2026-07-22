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

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adjudication.adjudicator import EngageCommand
from app.adjudication.effectiveness import effectiveness_pct
from app.adjudication.engagement import EnvSnapshot
from app.adjudication.weapon import WeaponProfile
from app.models import EquipmentInstance, EquipmentTemplate, TacticalUnit
from app.state.hot_state import HotStateStore

_EARTH_R_M = 6_371_000.0

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
        self._build(db, session_id)

    def _build(self, db: Session, session_id: str) -> None:
        units = db.scalars(select(TacticalUnit).where(TacticalUnit.session_id == session_id)).all()
        for unit in units:
            instances = db.scalars(
                select(EquipmentInstance).where(EquipmentInstance.owner_id == unit.id)
            ).all()
            best: WeaponProfile | None = None
            best_ammo = 0
            for inst in instances:
                tmpl = db.get(EquipmentTemplate, inst.template_id)
                if tmpl is None or tmpl.category != "KINETIC":
                    continue
                try:
                    profile = WeaponProfile.from_base_stats(tmpl.base_stats)
                except (ValueError, KeyError, TypeError):
                    continue  # baseStats 壞 → 略過
                self._by_weapon[inst.template_id] = profile
                self._by_weapon[inst.id] = profile  # COP weapon_id = 實例 id
                ammo = _ammo_of(inst)
                # 主武器＝射程最遠者（活執行期最小版；未指定選武器時的預設）。
                if best is None or profile.max_range_m > best.max_range_m:
                    best, best_ammo = profile, ammo
            if best is not None:
                self._primary[unit.id] = best
                self._primary_ammo[unit.id] = best_ammo

    def weapon_for(self, cmd: EngageCommand) -> WeaponProfile:
        if cmd.weapon_template_id and cmd.weapon_template_id in self._by_weapon:
            return self._by_weapon[cmd.weapon_template_id]
        return self._primary.get(cmd.shooter_id, _NO_WEAPON)

    def primary_ammo(self, unit_id: str) -> int:
        """單位主武器初始彈藥（供熱狀態 seed；活執行期以單一 ammo 純量近似）。"""
        return self._primary_ammo.get(unit_id, 0)


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


def make_engage_env(hot: HotStateStore, gateway: object | None = None):  # type: ignore[no-untyped-def]
    """回傳 env_for(shooter_id, target_id) → EnvSnapshot：由座標算射程 + 真實地形 LOS（Phase 3）。

    - range_m：由射手/目標熱狀態座標 haversine。座標缺失 → 極大 → OUT_OF_RANGE 拒絕（安全退化）。
    - los_clear：有 `gateway`（terrain gRPC）時查真實視線（射手↔目標，離地各 10m）——目標退入稜線後
      即遮蔽（直瞄拒絕、間瞄仍可）。無 gateway 或 RPC 失敗 → 退回 True（地形服務中斷不凍結戰鬥）。
    - 地形 LOS 給定座標具決定性 → replay 安全。天氣/cover 係數（STEP2/3）仍預設 1.0。
    """

    def env_for(shooter_id: str, target_id: str) -> EnvSnapshot:
        s = hot.get_unit(shooter_id) or {}
        t = hot.get_unit(target_id) or {}
        try:
            s_lat, s_lng = float(s["lat"]), float(s["lng"])
            t_lat, t_lng = float(t["lat"]), float(t["lng"])
            range_m = _haversine_m(s_lat, s_lng, t_lat, t_lng)
        except (KeyError, TypeError, ValueError):
            return EnvSnapshot(range_m=float("inf"), los_clear=True)
        los_clear = True
        if gateway is not None:
            try:
                outcome = gateway.has_los(  # type: ignore[attr-defined]
                    (s_lat, s_lng, _ENGAGE_OBS_M), (t_lat, t_lng, _ENGAGE_OBS_M)
                )
                los_clear = bool(outcome.visible)
            except Exception:
                los_clear = True
        return EnvSnapshot(range_m=range_m, los_clear=los_clear)

    return env_for
