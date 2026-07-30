"""面目標射擊裁決（WP-C10.2）——打座標而非打單位。

**為什麼需要這條路徑**：火力計畫的目標是預劃座標，「攻擊準備射擊」本來就是打一片位置，
不管當下有沒有人在那裡。既有的 `ENGAGE` 一律要 `target_unit_id`，表達不了這件事。

紅線 2：本模組是**純同步純函數**，不碰 DB、不碰熱狀態、不看牆鐘；
所有隨機一律走 `DeterministicRNG`（紅線 1），同一顆種子必得同一個落點。

**面射擊不分敵我**：落彈半徑內的單位一律受影響，包含友軍。
這不是疏漏，是這種火力的本質——[JCATS-F] 的火力協調之所以要有核准鏈與禁射區，
正是因為砲彈不會挑人。誤傷語意的細緻化屬 WP-C9，本模組先把物理做對。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from app.adjudication.formation import area_exposure_modifier, formation_of
from app.adjudication.suppression import Posture, posture_modifier
from app.adjudication.weapon import WeaponProfile
from app.engine.rng import DeterministicRNG
from app.state.ledger import LedgerEvent

_EARTH_R_M = 6_371_000.0
# 壓制半徑 ÷ 殺傷半徑（WP-C1）。砲彈在你旁邊 100 m 炸開沒傷到你，你照樣得趴下——
# 壓制的作用範圍本來就遠大於殺傷範圍，這正是「砲兵主要用來壓制而非殲滅」的物理來源。
SUPPRESSION_RADIUS_MULT = 3.0
# CEP（含 50% 落點的圓半徑）→ 二維常態的 sigma。Rayleigh 分布中位數 = sigma * sqrt(2 ln 2)。
_CEP_TO_SIGMA = 1.0 / 1.1774


@dataclass(frozen=True, slots=True)
class AreaTarget:
    """落點附近的一個單位（敵我皆列——砲彈不挑人）。"""

    unit_id: str
    faction: str
    lat: float
    lng: float
    armor_class: str = "SOFT"
    current_strength: float | None = None
    authorized_strength: float | None = None
    platform_count: int = 1
    # WP-C1 姿態。**掘壕對砲擊的防護最有意義**——不接這條的話，構工只擋得住直射火力，
    # 那把「為什麼要挖散兵坑」整個弄反了。中性預設 MOVING ⇒ 修正 1.0，既有局位元不變。
    posture: str = "MOVING"
    # WP-C3 隊形。**縱隊擠在一條線上，挨砲最慘**（[JCATS-A p.7,26]）。
    # COLUMN（中性預設）⇒ 倍率 1.0。
    formation: str = "COLUMN"


@dataclass(frozen=True, slots=True)
class AreaFireResult:
    impact_lat: float
    impact_lng: float
    # unit_id → 戰力損失。**只含真的挨了損失的單位**（殺傷半徑內）。
    losses: dict[str, float] = field(default_factory=dict)
    # unit_id → 落在其壓制半徑內的發數（WP-C1）。是 `losses` 的超集：
    # 沒被傷到但被打得抬不起頭的單位只出現在這裡。**不入帳本事件**（不改 hash chain）。
    suppressed: dict[str, int] = field(default_factory=dict)
    event: LedgerEvent | None = None


def _offset_m(lat: float, lng: float, north_m: float, east_m: float) -> tuple[float, float]:
    dlat = north_m / _EARTH_R_M * (180.0 / math.pi)
    dlng = east_m / (_EARTH_R_M * math.cos(math.radians(lat))) * (180.0 / math.pi)
    return lat + dlat, lng + dlng


def _distance_m(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = p2 - p1
    dl = math.radians(b_lng - a_lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * _EARTH_R_M * math.asin(math.sqrt(h))


def sample_impact(
    aim_lat: float, aim_lng: float, cep_m: float, rng: DeterministicRNG
) -> tuple[float, float]:
    """由瞄準點抽落點——CEP 決定散布，**同一顆種子必得同一個落點**（決定性重播）。

    cep_m<=0（未提供散布資料）→ 直接回瞄準點：退化成點命中，不崩潰也不亂猜。

    **抽樣次數固定為 2**（r 與 theta），與 cep 值無關——除了 cep<=0 的早退路徑。
    這件事是刻意的：抽樣次數若隨參數變動，改一個係數就會擾動整條 stream 的後續序列。
    """
    if cep_m <= 0:
        return aim_lat, aim_lng
    sigma = cep_m * _CEP_TO_SIGMA
    # Rayleigh 抽樣：r = sigma * sqrt(-2 ln(1-u))。u∈[0,1) 取自決定性 RNG。
    u = rng.random()
    r = sigma * math.sqrt(-2.0 * math.log(max(1e-12, 1.0 - u)))
    theta = rng.random() * 2.0 * math.pi
    return _offset_m(aim_lat, aim_lng, r * math.cos(theta), r * math.sin(theta))


def _loss_for(weapon: WeaponProfile, target: AreaTarget, distance_m: float) -> float:
    """距落點 distance_m 的單位承受的戰力損失。

    半徑外為 0；半徑內由中心的滿額線性遞減到邊緣的 0——
    不是真實的爆震衰減曲線，但**單調且可解釋**，比拍腦袋的階梯好。
    細緻化（彈種/掩蔽/俯角）屬後續保真卡。
    """
    if weapon.lethal_radius_m <= 0 or distance_m > weapon.lethal_radius_m:
        return 0.0
    falloff = 1.0 - (distance_m / weapon.lethal_radius_m)
    pk = weapon.pk_by_armor_class.get(target.armor_class)
    if pk is None:
        pk = weapon.damage_by_armor_class.get(target.armor_class, 0.0) / 100.0
    auth = target.authorized_strength
    cp_per_platform = (auth / max(1, target.platform_count)) if auth else 1.0
    return max(0.0, pk * falloff * cp_per_platform * _cover(target) * _exposure(target))


def _exposure(target: AreaTarget) -> float:
    """隊形 → 面殺傷的暴露倍率（WP-C3）。**大於 1 代表更慘**（縱隊）。"""
    return area_exposure_modifier(formation_of(target.formation))


def _cover(target: AreaTarget) -> float:
    """姿態 → 承受戰損的修正（WP-C1）。未知字串一律中性——裁決層不因資料髒而爆掉。"""
    try:
        return posture_modifier(Posture(target.posture))
    except ValueError:
        return 1.0


def resolve_area_fire(
    weapon: WeaponProfile,
    aim: tuple[float, float],
    targets: list[AreaTarget],
    rng: DeterministicRNG,
    tick: int,
    *,
    shooter_id: str,
    shooter_faction: str | None = None,
    rounds: int = 1,
    dispersion_mult: float = 1.0,
) -> AreaFireResult:
    """一次面目標射擊。回落點、各單位戰力損失、與帳本事件。

    `rounds` 為齊射發數：每發各自抽落點，損失累加——這才是「多發覆蓋一片」的語意，
    用一發乘以 N 會讓散布消失（等於打得比實際準）。

    `shooter_faction` 有給時，事件會另外標出**同陣營的傷亡**（誤傷）。這件事必須落在帳本上：
    面射擊本來就會傷到自己人，而「有沒有傷到自己人」正是事後檢討火力協調的第一個問題。

    `dispersion_mult` 是**觀測修正**（WP-C10.4）：射擊陣營對落點沒有觀測時由呼叫端傳 2.0
    ——沒有前觀就沒有彈著修正，散布加倍。判定「有沒有人在看」是 I/O（要查地形 LOS），
    故留在接線層；純函數這裡只收一個係數（紅線 2）。
    **`1.0` 必須位元不變**：`x * 1.0` 在 IEEE-754 恆等於 `x`，且 `0.0 * k == 0.0`，
    所以 cep<=0 的早退路徑（不抽樣）也維持原樣——既有局的隨機序列完全不動。
    """
    aim_lat, aim_lng = aim
    cep_m = weapon.dispersion_cep_m * dispersion_mult
    losses: dict[str, float] = {}
    suppressed: dict[str, int] = {}
    sup_radius = weapon.lethal_radius_m * SUPPRESSION_RADIUS_MULT
    impacts: list[tuple[float, float]] = []
    for _ in range(max(1, rounds)):
        ilat, ilng = sample_impact(aim_lat, aim_lng, cep_m, rng)
        impacts.append((ilat, ilng))
        for t in targets:
            d = _distance_m(ilat, ilng, t.lat, t.lng)
            loss = _loss_for(weapon, t, d)
            if loss > 0:
                losses[t.unit_id] = losses.get(t.unit_id, 0.0) + loss
            if d <= sup_radius:
                suppressed[t.unit_id] = suppressed.get(t.unit_id, 0) + 1

    # 損失封頂在目標當前戰力：齊射累加很容易超過殘存戰力，不封頂的話帳本上的
    # damage_calc 會比實際被扣掉的還多——AAR 的傷亡統計就成了假的。
    by_id = {t.unit_id: t for t in targets}
    for uid, value in list(losses.items()):
        cur = by_id[uid].current_strength
        if cur is not None:
            losses[uid] = min(value, max(0.0, cur))

    first_lat, first_lng = impacts[0]
    affected: dict[str, Any] = {uid: round(v, 3) for uid, v in losses.items()}
    # 逐發落點放 ai_decision（不放 detail）：落點是決定性的**證據**，
    # detail 刻意不入 hash chain（見 LedgerEvent 註解），證據性欄位不得放那裡。
    decision: dict[str, Any] = {
        "aim_lat": aim_lat,
        "aim_lng": aim_lng,
        "impact_lat": first_lat,
        "impact_lng": first_lng,
        "rounds": max(1, rounds),
        "losses_by_unit": affected,
        "impacts": [[la, ln] for la, ln in impacts],
    }
    if shooter_faction is not None:
        friendly = sorted(
            uid
            for uid, v in losses.items()
            if v > 0 and by_id[uid].faction == shooter_faction and uid != shooter_id
        )
        decision["friendly_losses"] = friendly
    event = LedgerEvent(
        event_type="AREA_FIRE_RESOLVED",
        tick=tick,
        initiator_id=shooter_id,
        damage_calc=round(sum(losses.values()), 3),
        ai_decision=decision,
    )
    return AreaFireResult(
        impact_lat=first_lat,
        impact_lng=first_lng,
        losses=losses,
        suppressed=suppressed,
        event=event,
    )
