"""聯合兵種交戰裁決（SPEC_EXTEND §2）——單位以其**武器組合**同時對目標發揮火力。

純同步純函數（HOW_TO §3、§4.2）：對單位每件**合格**武器各算一份 volley 期望毀傷再加總，
以「射程帶 + 裝甲類 pk」自動完成 weapon-target 匹配（反裝甲打步兵≈0、步槍打主戰車≈0、
遠距只有長程武器打得到）。每件**合格**武器抽**恰一次** dispersion（順序＝武器清單穩定序）
→ 決定性可重播。

與 `resolve_engagement` 的差異：
- 現況「任一武器不合法 → 拒絕整場」→ 改「**逐武器篩選**」：不合法者貢獻 0，唯全數不合法才 REJECTED。
- 輸出帶 `per_weapon[]` 明細（供 AAR）+ `ammo_spent_by_weapon`（供 adjudicator 逐武器扣彈）。

紅線：不碰 DB/Redis/時鐘/RPC；隨機性經注入 `DeterministicRNG`（stream="adjudication"）；
物理數值全由武器 profile 資料驅動，火力政策（P3）只做篩選、不改數值。
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from app.adjudication.effectiveness import effectiveness_pct
from app.adjudication.engagement import (
    EngagementResult,
    EnvSnapshot,
    Resolution,
    Shooter,
    Target,
    _legality_reason,
)
from app.adjudication.weapon import WeaponProfile
from app.engine.rng import DeterministicRNG
from app.state.ledger import LedgerEvent

# 與 engagement._resolve_volley 一致的火力離散因子區間（單一 rng 抽樣，期望≈1）。
_DISPERSION = (0.8, 1.2)

# 合格集為空時回報原因的優先序（越前越「可行動」）：在射程但被遮蔽/彈道阻，比全出界更有資訊。
_REJECT_PRIORITY = ("NO_LOS", "TRAJECTORY_BLOCKED", "OUT_OF_RANGE", "NO_AMMO")

# 供 wiring 注入的「每武器環境」查表：依武器飛行剖面（直/間瞄、彈道）給不同 EnvSnapshot。
WeaponEnvLookup = Callable[[WeaponProfile], EnvSnapshot]


def _clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else v


@dataclass(frozen=True, slots=True)
class CombinedWeapon:
    """聯合兵種加總的純輸入（一件武器）。由 wiring 從 WeaponEntry + 熱狀態活彈藥組成。

    weapon_id＝EquipmentInstance.id（＝熱狀態 ammo_by_weapon 的鍵、per_weapon 明細鍵）。
    ammo＝該武器**當前活彈藥**（熱狀態，非 DB 初值）。
    """

    weapon_id: str
    profile: WeaponProfile
    quantity: int
    ammo: int


def resolve_combined_engagement(
    weapons: Sequence[CombinedWeapon],
    shooter_id: str,
    shooter_effectiveness: float,
    target: Target,
    env_for: WeaponEnvLookup,
    rng: DeterministicRNG,
    tick: int,
) -> EngagementResult:
    """裁決單位以武器組合對目標的單次交戰。確定性：相同 (輸入, rng 狀態) → 相同結果。

    對每件武器：逐武器合法性篩選 → 合格者算 volley 期望毀傷（含一次 dispersion 抽樣）→ Σ；
    毀傷累計夾在目標當前戰力內（能量守恆）。無任何合格武器 → REJECTED。
    """
    authorized = (
        target.authorized_strength
        if target.authorized_strength and target.authorized_strength > 0.0
        else 100.0
    )
    platform_count = max(1, target.platform_count)
    current = target.current_strength if target.current_strength is not None else authorized
    cp_per_platform = authorized / platform_count
    eff = _clamp01(shooter_effectiveness)

    remaining = current
    total_loss = 0.0
    total_ammo = 0
    ammo_spent_by_weapon: dict[str, int] = {}
    per_weapon: list[dict[str, Any]] = []
    reject_reasons: list[str] = []
    max_p_hit = 0.0
    eligible = False
    fired = False

    for w in weapons:
        env = env_for(w.profile)
        shooter_i = Shooter(
            unit_id=shooter_id, ammo_count=w.ammo, quantity=w.quantity, effectiveness=eff
        )
        reason = _legality_reason(w.profile, shooter_i, env)
        if reason is not None:
            reject_reasons.append(reason)
            per_weapon.append({"weapon_id": w.weapon_id, "status": "REJECTED", "reason": reason})
            continue
        eligible = True
        # 命中率（乘法係數，夾 [0,1]）——與 resolve_engagement 一致。
        base = w.profile.base_ph(env.range_m)
        p_hit = _clamp01(
            base
            * env.terrain_cover_modifier
            * env.weather_modifier
            * env.shooter_suppression_modifier
            * env.target_posture_modifier
        )
        max_p_hit = max(max_p_hit, p_hit)
        # 齊射期望毀傷（每合格武器恰一次 dispersion 抽樣）——與 _resolve_volley 同式。
        eff_shooters = max(0.0, w.quantity * eff)
        rate = w.profile.rate_per_tick if w.profile.rate_per_tick > 0 else 1.0
        shots = min(eff_shooters * rate, float(w.ammo))
        ammo_spent = math.ceil(shots) if shots > 0 else 0
        dispersion = rng.uniform(*_DISPERSION)
        expected_hits = shots * p_hit * dispersion
        pk = w.profile.expected_casualties(target.armor_class)  # P(kill|hit) 0..1
        raw_loss = expected_hits * pk * cp_per_platform
        loss = min(max(0.0, raw_loss), remaining)  # 夾在剩餘戰力內（能量守恆）
        remaining -= loss
        total_loss += loss
        if ammo_spent > 0:
            ammo_spent_by_weapon[w.weapon_id] = ammo_spent
            total_ammo += ammo_spent
        if loss > 0.0:
            fired = True
        per_weapon.append(
            {
                "weapon_id": w.weapon_id,
                "status": "HIT" if loss > 0.0 else "MISS",
                "p_hit": round(p_hit, 4),
                "shots_fired": round(shots, 2),
                "expected_hits": round(expected_hits, 3),
                "pk": round(pk, 3),
                "dispersion": round(dispersion, 3),
                "strength_loss": round(loss, 3),
                "ammo_spent": ammo_spent,
            }
        )

    if not eligible:
        # 無任何武器可打 → REJECTED（取最可行動的原因）。不消耗彈藥。
        reason = _pick_reason(reject_reasons)
        event = LedgerEvent(
            event_type="ENGAGEMENT_RESOLVED",
            tick=tick,
            initiator_id=shooter_id,
            target_id=target.unit_id,
            damage_calc=0.0,
            ai_decision={
                "status": Resolution.REJECTED.value,
                "reason": reason,
                "mode": "COMBINED",
                "per_weapon": per_weapon,
            },
        )
        return EngagementResult(
            status=Resolution.REJECTED,
            p_hit=0.0,
            roll=None,
            damage=0.0,
            target_health_after=target.health,
            coefficients={},
            reason=reason,
            ammo_spent=0,
            ammo_spent_by_weapon=None,
            events=[event],
        )

    strength_after = max(0.0, remaining)
    health_after = effectiveness_pct(strength_after / authorized)
    status = Resolution.HIT if fired else Resolution.MISS
    n_fired = sum(1 for pw in per_weapon if pw["status"] != "REJECTED")
    coefficients = {
        "weapons_fired": float(n_fired),
        "strength_loss": round(total_loss, 3),
        "strength_after": round(strength_after, 3),
        "cp_per_platform": round(cp_per_platform, 3),
    }
    ai_decision: dict[str, Any] = {
        "status": status.value,
        "mode": "COMBINED",
        "p_hit": round(max_p_hit, 4),  # 代表值＝最可能命中的武器（明細見 per_weapon）
        "per_weapon": per_weapon,
        "coefficients": coefficients,
        "target_health_after": health_after,
        "target_strength_after": strength_after,
        "ammo_spent": total_ammo,
    }
    event = LedgerEvent(
        event_type="ENGAGEMENT_RESOLVED",
        tick=tick,
        initiator_id=shooter_id,
        target_id=target.unit_id,
        damage_calc=round(total_loss, 3),
        ai_decision=ai_decision,
    )
    return EngagementResult(
        status=status,
        p_hit=round(max_p_hit, 4),
        roll=None,  # 加總走期望值 + 逐武器離散因子，非單次 roll
        damage=round(total_loss, 3),
        target_health_after=health_after,
        coefficients=coefficients,
        reason=None,
        target_strength_after=strength_after,
        ammo_spent=total_ammo,
        ammo_spent_by_weapon=ammo_spent_by_weapon,
        events=[event],
    )


def _pick_reason(reasons: Sequence[str]) -> str:
    for r in _REJECT_PRIORITY:
        if r in reasons:
            return r
    return reasons[0] if reasons else "OUT_OF_RANGE"
