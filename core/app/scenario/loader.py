"""Scenario package 載入器（SPEC_FULL §11.1 / §12.1）。

流程：讀 scenario.yaml → JSON Schema 驗證 → 語意驗證（factions/relations/victory/orbat）→
建 FactionRelations + 收集單位。**精確錯誤路徑**：錯誤帶 `<檔>: <路徑>: <訊息>`。

開局（寫 DB 建 session + units）由 create_session_from_scenario；kernel 綁定與 relations 熱狀態
於後續卡（O7.4/部署層）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from sqlalchemy.orm import Session

from app.factions import WHITE_CELL, FactionRelations, Relation, validate_faction_id
from app.models.enums import UnitLevel
from app.orders.no_strike import zones_to_cells
from app.scenario.triggers import MselEntry, TriggerError, validate_condition

_CONTRACTS = Path(__file__).resolve().parents[3] / "contracts"


class ScenarioError(ValueError):
    """想定載入/驗證失敗——訊息含精確路徑（<檔>: <路徑>: <原因>）。"""


@dataclass(frozen=True, slots=True)
class ScenarioUnit:
    faction: str
    designation: str
    unit_level: str
    lat: float | None
    lng: float | None
    parent: str | None
    fixed: bool = False  # 固定單位（指揮部等）：不接受 MOVE 令、不被派去移動（§11.1）。
    # WP-B6 編裝：((template_name, quantity, ammo|None), …)。空＝沿用開局的預設配發。
    # 用 tuple 而非 list：ScenarioUnit 是 frozen 值物件，list 會讓它不可雜湊也可被就地改。
    equipment: tuple[tuple[str, int, int | None], ...] = ()


@dataclass(slots=True)
class LoadedScenario:
    name: str
    version: str
    mode: str
    bbox: list[float]
    tick_rate_ms: int
    hex_resolution: int
    aggregate_adjudication_level: str
    faction_ids: list[str]
    faction_colors: dict[str, str]
    relations: FactionRelations
    units: list[ScenarioUnit] = field(default_factory=list)
    msel: list[MselEntry] = field(default_factory=list)
    victory_conditions: list[dict[str, Any]] = field(default_factory=list)
    # WP-B6：schema 有、過去卻沒被模型承接的兩欄。沒承接 → dump 寫不出來 → 匯出即遺失。
    description: str | None = None
    faction_display_names: dict[str, str] = field(default_factory=dict)
    # WP-A3 禁射區宣告（原樣帶入；幾何→h3 格集由 orders/no_strike.py 於讀取時導出）。
    no_strike_zones: list[dict[str, Any]] = field(default_factory=list)
    # WP-B6 交戰規則宣告（roe.yaml 原樣帶入；解析成規則由 orders/roe.py 於讀取時做）。
    # 空 dict ＝ 未宣告 ＝ 無限制。
    roe: dict[str, Any] = field(default_factory=dict)
    # 申請單配額（WP-B5.2）：{RequestKind: 上限}。缺／未列＝不限。
    request_quotas: dict[str, int] = field(default_factory=dict)
    # 曲射火協（WP-B5.3）：True＝ARTILLERY/MISSILE 的 ENGAGE 須掛已核准申請單。
    indirect_fire_requires_approval: bool = False
    # WP-C9 友軍誤傷裁決。省略＝False＝既有的「非敵對一律拒」。
    allow_fratricide: bool = False
    # WP-B6 想定機動覆寫（overrides/mobility_matrix.json 原樣帶入；**局部**覆寫，深合併於預設）。
    mobility_overrides: dict[str, Any] = field(default_factory=dict)
    # 陣地變換（WP-C10.5）：{enabled, missions_before_move, min_km, max_km}。空＝停用。
    survivability_move: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


def _load_yaml(path: Path, label: str) -> dict[str, Any]:
    if not path.exists():
        raise ScenarioError(f"{label}: 檔案不存在（{path}）")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ScenarioError(f"{label}: YAML 解析失敗：{exc}") from exc
    if not isinstance(data, dict):
        raise ScenarioError(f"{label}: 頂層必須是 mapping")
    return data


def _validate_schema(data: dict[str, Any], schema_name: str, label: str) -> None:
    schema = json.loads((_CONTRACTS / schema_name).read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(data), key=lambda e: list(e.path))
    if errors:
        e = errors[0]
        path = ".".join(str(p) for p in e.path) or "(root)"
        raise ScenarioError(f"{label}: {path}: {e.message}")


def _build(
    sc: dict[str, Any],
    faction_ids: list[str],
    relations: FactionRelations,
    units: list[ScenarioUnit],
    msel: list[MselEntry],
    roe: dict[str, Any],
    mobility_overrides: dict[str, Any],
) -> LoadedScenario:
    """由已驗證的 scenario dict 組 LoadedScenario。

    **兩條載入入口（package / bundle）共用這一個組裝點**——過去兩邊各抄一份建構式，
    新增欄位很容易只改一邊；`fixed` 當年就是這樣在 dump 那一側漏掉的（WP-B6）。
    """
    return LoadedScenario(
        name=sc["name"],
        version=sc["version"],
        mode=sc["mode"],
        bbox=list(sc["bbox"]),
        tick_rate_ms=sc["tick_rate_ms"],
        hex_resolution=sc.get("hex_resolution", 8),
        aggregate_adjudication_level=sc.get("aggregate_adjudication_level", "BATTALION"),
        faction_ids=faction_ids,
        faction_colors={f["id"]: f["color"] for f in sc["factions"] if "color" in f},
        relations=relations,
        units=units,
        msel=msel,
        victory_conditions=list(sc["victory_conditions"]),
        no_strike_zones=_validate_no_strike(sc.get("no_strike_zones", [])),
        roe=roe,
        request_quotas={str(k): int(v) for k, v in (sc.get("request_quotas") or {}).items()},
        indirect_fire_requires_approval=bool(sc.get("indirect_fire_requires_approval", False)),
        allow_fratricide=bool(sc.get("allow_fratricide", False)),
        mobility_overrides=mobility_overrides,
        survivability_move=dict(sc.get("survivability_move") or {}),
        description=sc.get("description"),
        faction_display_names={
            f["id"]: f["display_name"] for f in sc["factions"] if "display_name" in f
        },
        raw=sc,
    )


def load_scenario_package(package_dir: str | Path) -> LoadedScenario:
    """載入並全量驗證一個 scenario package 目錄，回 LoadedScenario。任何錯誤 → ScenarioError。"""
    root = Path(package_dir)
    sc = _load_yaml(root / "scenario.yaml", "scenario.yaml")
    _validate_schema(sc, "scenario.schema.json", "scenario.yaml")

    faction_ids = _validate_factions(sc["factions"])
    relations = _build_relations(sc.get("relations", []), faction_ids)
    _validate_victory(sc["victory_conditions"], faction_ids)

    units = _load_orbats(root, sc.get("files", {}).get("orbat", {}), faction_ids)
    msel = _load_msel(root, sc.get("files", {}).get("msel"))
    roe = _load_roe(root, sc.get("files", {}).get("roe"), faction_ids)
    mobility = _load_overrides(root, sc.get("files", {}).get("overrides_dir"))

    return _build(sc, faction_ids, relations, units, msel, roe, mobility)


def load_scenario_bundle(bundle: dict[str, Any]) -> LoadedScenario:
    """由編輯器匯出的 **記憶體 bundle**（非檔案）載入並全量驗證 → LoadedScenario（O7 持久化 / #7）。

    bundle = {scenario:{…}, orbat:{faction:{faction,units:[…]}}, msel?:{events:[…]}}。
    與 load_scenario_package 共用驗證；供 POST /scenarios 存檔前驗證與 create-from-scenario 使用。
    """
    sc = bundle.get("scenario")
    if not isinstance(sc, dict):
        raise ScenarioError("bundle: scenario 缺少或格式錯誤")
    _validate_schema(sc, "scenario.schema.json", "scenario")
    faction_ids = _validate_factions(sc["factions"])
    relations = _build_relations(sc.get("relations", []), faction_ids)
    _validate_victory(sc["victory_conditions"], faction_ids)
    units = _units_from_orbat_dict(bundle.get("orbat") or {}, faction_ids)
    msel = _msel_from_dict(bundle.get("msel"))
    roe = _roe_from_dict(bundle.get("roe"), faction_ids)
    mobility = _mobility_from_dict((bundle.get("overrides") or {}).get("mobility_matrix"))
    return _build(sc, faction_ids, relations, units, msel, roe, mobility)


def _units_from_orbat_dict(orbat: dict[str, Any], faction_ids: list[str]) -> list[ScenarioUnit]:
    units: list[ScenarioUnit] = []
    for faction, ob in orbat.items():
        if faction not in faction_ids:
            raise ScenarioError(f"orbat: 未宣告的陣營：{faction}")
        _validate_schema(ob, "orbat.schema.json", f"orbat[{faction}]")
        if ob["faction"] != faction:
            raise ScenarioError(f"orbat[{faction}]: faction 不符（{ob['faction']} != {faction}）")
        designations = {u["designation"] for u in ob["units"]}
        for j, u in enumerate(ob["units"]):
            parent = u.get("parent")
            if parent is not None and parent not in designations:
                raise ScenarioError(f"orbat[{faction}].units[{j}].parent: 未知上級單位：{parent}")
            units.append(
                ScenarioUnit(
                    faction=faction,
                    designation=u["designation"],
                    unit_level=u["unit_level"],
                    lat=u.get("lat"),
                    lng=u.get("lng"),
                    parent=parent,
                    fixed=bool(u.get("fixed", False)),
                    equipment=_equipment_of(u),
                )
            )
    return units


def _equipment_of(unit: dict[str, Any]) -> tuple[tuple[str, int, int | None], ...]:
    """orbat 單位的 equipment 區段 → 不可變 tuple（結構已由 JSON Schema 驗過）。"""
    return tuple(
        (
            str(e["template"]),
            int(e.get("quantity", 1)),
            int(e["ammo"]) if e.get("ammo") is not None else None,
        )
        for e in (unit.get("equipment") or [])
    )


def _msel_from_dict(data: dict[str, Any] | None) -> list[MselEntry]:
    if not data or not data.get("events"):
        return []
    _validate_schema(data, "msel.schema.json", "msel")
    return _msel_entries(data["events"], "msel")


def _msel_entries(events: list[dict[str, Any]], label: str) -> list[MselEntry]:
    """MSEL 事件清單 → MselEntry；**trigger 於此驗 condition DSL**（兩條入口共用）。

    未知的 trigger type 若不在載入時擋，該則注入會在執行期靜默失效（MselEngine 每 tick
    評估、丟 TriggerError 被吞）——白軍以為安排了增援，結果整局都沒發生。
    """
    out: list[MselEntry] = []
    for i, e in enumerate(events):
        try:
            validate_condition(e["trigger"], f"{label}: events[{i}].trigger")
        except TriggerError as exc:
            raise ScenarioError(str(exc)) from exc
        out.append(
            MselEntry(
                id=e["id"], trigger=e["trigger"], inject=e["inject"], once=e.get("once", True)
            )
        )
    return out


def _roe_from_dict(data: dict[str, Any] | None, faction_ids: list[str]) -> dict[str, Any]:
    """ROE dict → 驗證後原樣帶回（空/None → {}）。**兩條載入入口共用**。"""
    if not data:
        return {}
    _validate_schema(data, "roe.schema.json", "roe")
    _validate_roe_factions(data, faction_ids, "roe")
    return dict(data)


def _validate_roe_factions(data: dict[str, Any], faction_ids: list[str], label: str) -> None:
    """ROE 裡出現的陣營必須是本想定宣告過的。

    打錯字的陣營名不會被 JSON Schema 擋（它只驗字串格式），而規則會安靜地套用到
    一個不存在的陣營——**寫了限制卻沒有任何單位受限**，正是 WP-A3 說的沉默失效。
    """
    known = set(faction_ids)
    for faction in data.get("default_fire_policy") or {}:
        if faction not in known:
            raise ScenarioError(f"{label}: default_fire_policy.{faction}: 未宣告的陣營")
    for i, rule in enumerate(data.get("weapon_restrictions") or []):
        faction = rule.get("faction") if isinstance(rule, dict) else None
        if faction is not None and faction not in known:
            raise ScenarioError(
                f"{label}: weapon_restrictions[{i}].faction: 未宣告的陣營：{faction}"
            )


def _load_roe(root: Path, rel_path: str | None, faction_ids: list[str]) -> dict[str, Any]:
    """讀 `files.roe` 指的 roe.yaml。**宣告了但檔案不存在 → 報錯**。

    與 msel「宣告了但檔不在就靜默略過」的既有寬容處理刻意相反：ROE 是安全/合規機制，
    「以為有限制、其實檔案沒被讀到」的失效模式沒有任何外顯症狀。缺檔要當場炸。
    """
    if not rel_path:
        return {}
    data = _load_yaml(root / rel_path, rel_path)
    _validate_schema(data, "roe.schema.json", rel_path)
    _validate_roe_factions(data, faction_ids, rel_path)
    return data


MOBILITY_OVERRIDE_FILE = "mobility_matrix.json"


def _mobility_from_dict(
    data: Any, label: str = "overrides/" + MOBILITY_OVERRIDE_FILE
) -> dict[str, Any]:
    """機動覆寫 dict → 驗證後原樣帶回（空/None → {}）。**兩條載入入口共用**。"""
    if not data:
        return {}
    if not isinstance(data, dict):
        raise ScenarioError(f"{label}: 頂層必須是 mapping")
    _validate_schema(data, "mobility_matrix.schema.json", label)
    _validate_mobility_passability(data, label)
    return dict(data)


def _validate_mobility_passability(patch: dict[str, Any], label: str) -> None:
    """覆寫**不得改變可通行性**（-1 進出）——否則規劃端與執行端會意見不一致。

    路徑規劃 A* 跑在 terrain 容器、讀它自己那份出貨矩陣，`GetPathRequest` 只帶
    `{from_h3, to_h3, mobility_profile}`，**看不到想定覆寫**。把可通行改掉會出現兩種
    都很糟的分歧：規劃出的路線穿過（對本局而言）不可通行的地形 → 單位半路 MOVE_BLOCKED 停死；
    或本局明明開放通行、A* 仍判不可達 → 退回直線穿越（反而不繞路）。

    只改速度倍率則路線仍然可走，差別僅在是否最省時——這是可接受的近似，且不會卡住單位。
    要真正支援可通行覆寫，得先讓 terrain 服務吃得到 per-session 矩陣（改 proto，屬另一張卡）。
    """
    from app.movement.mobility_matrix import default_rules

    base = default_rules().passability()
    for profile, row in (patch.get("profiles") or {}).items():
        if not isinstance(row, dict):
            continue
        for klass, value in row.items():
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            was = base.get((str(profile), str(klass)))
            if was is None:
                continue  # 預設沒有的組合：新增即可，A* 對它本來就回 1.0
            if was is not (float(value) >= 0):
                raise ScenarioError(
                    f"{label}: profiles.{profile}.{klass}: 覆寫不得改變可通行性"
                    f"（預設{'可' if was else '不可'}通行）——路徑規劃 A* 在 terrain 服務、"
                    "看不到想定覆寫，改了會讓規劃與執行對「走不走得通」意見不一致"
                )


def _load_overrides(root: Path, rel_dir: str | None) -> dict[str, Any]:
    """讀 `files.overrides_dir` 下的 `mobility_matrix.json`（可選；目錄或檔案不存在 → {}）。"""
    if not rel_dir:
        return {}
    path = root / rel_dir / MOBILITY_OVERRIDE_FILE
    if not path.exists():
        return {}
    label = f"{rel_dir}/{MOBILITY_OVERRIDE_FILE}"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ScenarioError(f"{label}: JSON 解析失敗：{exc}") from exc
    return _mobility_from_dict(data, label)


def _load_msel(root: Path, rel_path: str | None) -> list[MselEntry]:
    if not rel_path or not (root / rel_path).exists():
        return []
    data = _load_yaml(root / rel_path, rel_path)
    _validate_schema(data, "msel.schema.json", rel_path)
    return _msel_entries(data["events"], rel_path)


def create_session_from_scenario(
    db: Session,
    loaded: LoadedScenario,
    *,
    master_seed: int,
    scenario_id: str | None = None,
    seed_default_equipment: bool = False,
) -> str:
    """依載入的想定開局：建 WargameSession + TacticalUnits（含 parent 連結）。回 session id。

    relations 熱狀態載入與 kernel 綁定屬部署層/O7.4；本函式只落地 session 與單位。
    scenario_id：連結到已存的 Scenario 列（#7 create-from-scenario）；None 則不連結。
    seed_default_equipment=True：為每個單位配發預設武器（供資料驅動的 ENGAGE 武器/彈種選擇）。
    """
    from app.models import SessionMode, TacticalUnit, WargameSession

    session = WargameSession(
        name=loaded.name,
        scenario_id=scenario_id,
        master_seed=master_seed,
        mode=SessionMode(loaded.mode),
        current_weather={},
        # #98 關係矩陣落地：想定宣告的敵我關係從此隨局持久化（過去 loader 建完就丟，
        # 導致執行期只能退回全 HOSTILE）。空宣告存 None ＝ 未宣告 ＝ 全 HOSTILE 預設。
        faction_relations=loaded.relations.to_triples() or None,
        # WP-A3 禁射區落地：想定宣告的保護區隨局持久化（護欄 G4 與人類 precheck 共用）。
        # 空宣告存 None ＝ 無禁射區（既有局語義）。
        no_strike_zones=loaded.no_strike_zones or None,
        # WP-B6 ROE 落地：想定宣告的交戰規則隨局持久化（裁決層與 precheck 共用）。
        # 空宣告存 None ＝ 無限制（既有局語義）。
        roe=loaded.roe or None,
        # WP-B6 機動覆寫落地：想定的地形通行調整隨局持久化（runner 與預覽端共用）。
        mobility_overrides=loaded.mobility_overrides or None,
        # WP-B5.2 申請配額落地：想定宣告的配額**開局快照一份**（不即時讀想定，
        # 否則想定被編修會追溯改掉進行中的局）。空宣告存 None ＝ 不限（既有局語義）。
        request_quotas=loaded.request_quotas or None,
        # WP-B5.3 曲射火協落地：未宣告存 None ＝ 不設限（既有局零變更）。
        indirect_fire_requires_approval=loaded.indirect_fire_requires_approval or None,
        # `or None`＝未宣告寫 NULL。NOT NULL + default 會回頭改掉既有局的語義。
        allow_fratricide=loaded.allow_fratricide or None,
        # WP-C10.5 陣地變換落地：未宣告存 None ＝ 停用（既有局零變更）。
        survivability_move=loaded.survivability_move or None,
        # WP-B2 MSEL 落地：**過去整個漏掉**——想定的 msel 載得進來卻進不了執行期。
        msel=[
            {"id": e.id, "once": e.once, "trigger": e.trigger, "inject": e.inject}
            for e in loaded.msel
        ]
        or None,
    )
    db.add(session)
    db.flush()

    by_designation: dict[tuple[str, str], TacticalUnit] = {}
    # 先建全部單位（無 parent），再連 parent——避免順序相依。
    for u in loaded.units:
        unit = TacticalUnit(
            session_id=session.id,
            designation=u.designation,
            unit_level=UnitLevel(u.unit_level),
            faction=u.faction,
            current_lat=u.lat,
            current_lng=u.lng,
            is_fixed=u.fixed,
        )
        db.add(unit)
        by_designation[(u.faction, u.designation)] = unit
    db.flush()
    for u in loaded.units:
        if u.parent is not None:
            by_designation[(u.faction, u.designation)].parent_id = by_designation[
                (u.faction, u.parent)
            ].id
    _create_declared_equipment(db, loaded, by_designation)
    if seed_default_equipment:
        from app.adjudication import seed_session_equipment

        # 已由想定明確編裝的單位不會被配發預設步槍——`seed_session_equipment` 本身就跳過
        # 「已有任何裝備」的單位，而上一步已 flush，故它看得到。想定作者說了算。
        seed_session_equipment(db, session.id)
    db.commit()
    return session.id


def _create_declared_equipment(
    db: Session,
    loaded: LoadedScenario,
    by_designation: dict[tuple[str, str], Any],
) -> None:
    """依 orbat 的 `equipment` 宣告建 EquipmentInstance。

    範本以**名稱**參照（SPEC_FULL §11.1 的錯誤訊息範例即為
    `orbat/blue.yaml: units[3].equipment[0]: unknown template 'T-999'`）。名稱查不到就報錯——
    靜默略過會讓想定作者以為部署了戰車、實際上單位空手上場，而且直到交戰才會發現。

    ⚠ 這裡才驗名稱而非載入時驗：範本住 DB（全域武器庫），loader 是零 DB 的純解析層。
    """
    from app.models import EquipmentInstance

    declared = [u for u in loaded.units if u.equipment]
    if not declared:
        return

    from app.adjudication.seed_equipment import ensure_mobility_templates, ensure_weapon_templates

    # 官方想定引用的是出貨種子範本；確保它們存在（既有 upsert，冪等）。
    templates = {**ensure_weapon_templates(db), **ensure_mobility_templates(db)}
    for u in declared:
        unit = by_designation[(u.faction, u.designation)]
        for i, (name, quantity, ammo) in enumerate(u.equipment):
            template_id = templates.get(name)
            if template_id is None:
                raise ScenarioError(
                    f"orbat[{u.faction}].units[{u.designation}].equipment[{i}]: "
                    f"未知裝備範本 '{name}'（可用：{', '.join(sorted(templates))}）"
                )
            db.add(
                EquipmentInstance(
                    template_id=template_id,
                    owner_id=unit.id,
                    quantity=quantity,
                    current_state={"ammo": ammo} if ammo is not None else {},
                )
            )
    db.flush()


def _validate_factions(factions: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for i, f in enumerate(factions):
        fid = f["id"]
        from app.errors import FactionInvalidError

        try:
            validate_faction_id(fid, allow_white_cell=False)  # WHITE_CELL 保留字不得為交戰陣營
        except FactionInvalidError as exc:
            raise ScenarioError(f"scenario.yaml: factions[{i}].id: {exc}") from exc
        if fid in ids:
            raise ScenarioError(f"scenario.yaml: factions[{i}].id: 重複的陣營 id：{fid}")
        ids.append(fid)
    return ids


def _validate_no_strike(zones: Any) -> list[dict[str, Any]]:
    """禁射區的語意驗證（WP-A3）。JSON Schema 已驗結構，這裡只擋「結構合法但無效」的宣告。

    幾何算不出任何格 → 直接拒絕載入：那種區在執行期完全不會攔到東西，
    悄悄放行等於讓想定作者以為自己保護了醫院，實際上沒有——**安全機制的沉默失效最危險**。
    """
    if not isinstance(zones, list):
        return []
    out: list[dict[str, Any]] = []
    for i, z in enumerate(zones):
        if not isinstance(z, dict):
            raise ScenarioError(f"scenario.yaml: no_strike_zones[{i}]: 需為物件")
        cells = zones_to_cells([z])
        if not cells.any_cells:
            name = z.get("name", "?")
            raise ScenarioError(
                f"scenario.yaml: no_strike_zones[{i}]（{name}）: 幾何算不出任何 h3 格，"
                "此區在執行期不會攔到任何目標——請檢查 ring/center 的座標順序為 [lng, lat]"
            )
        out.append(dict(z))
    return out


def _build_relations(rels: list[list[Any]], faction_ids: list[str]) -> FactionRelations:
    known = set(faction_ids)
    declarations: list[tuple[str, str, Relation]] = []
    for i, triple in enumerate(rels):
        a, b, rel = triple[0], triple[1], triple[2]
        for side in (a, b):
            if side == WHITE_CELL or side not in known:
                raise ScenarioError(f"scenario.yaml: relations[{i}]: 未宣告的陣營：{side}")
        if a == b:
            raise ScenarioError(f"scenario.yaml: relations[{i}]: 不可設定陣營對自己的關係")
        declarations.append((a, b, Relation(rel)))
    return FactionRelations(declarations)


def _validate_victory(conditions: list[dict[str, Any]], faction_ids: list[str]) -> None:
    known = set(faction_ids)
    for i, vc in enumerate(conditions):
        if vc["faction"] not in known:
            raise ScenarioError(
                f"scenario.yaml: victory_conditions[{i}].faction: 未宣告的陣營：{vc['faction']}"
            )
        # WP-B6：condition DSL 過去只有 JSON Schema 的 `type: object`（等於沒驗）。
        # 未知 type 要到執行期評估時才丟 TriggerError——白軍以為勝負條件生效、其實整局都不會判。
        try:
            validate_condition(vc["condition"], f"scenario.yaml: victory_conditions[{i}].condition")
        except TriggerError as exc:
            raise ScenarioError(str(exc)) from exc


def _load_orbats(
    root: Path, orbat_files: dict[str, str], faction_ids: list[str]
) -> list[ScenarioUnit]:
    units: list[ScenarioUnit] = []
    for faction, rel_path in orbat_files.items():
        if faction not in faction_ids:
            raise ScenarioError(f"scenario.yaml: files.orbat: 未宣告的陣營：{faction}")
        label = rel_path
        data = _load_yaml(root / rel_path, label)
        _validate_schema(data, "orbat.schema.json", label)
        if data["faction"] != faction:
            raise ScenarioError(
                f"{label}: faction: 與 files.orbat 宣告不符（{data['faction']} != {faction}）"
            )
        designations = {u["designation"] for u in data["units"]}
        for j, u in enumerate(data["units"]):
            parent = u.get("parent")
            if parent is not None and parent not in designations:
                raise ScenarioError(f"{label}: units[{j}].parent: 未知上級單位：{parent}")
            units.append(
                ScenarioUnit(
                    faction=faction,
                    designation=u["designation"],
                    unit_level=u["unit_level"],
                    lat=u.get("lat"),
                    lng=u.get("lng"),
                    parent=parent,
                    fixed=bool(u.get("fixed", False)),
                    equipment=_equipment_of(u),
                )
            )
    return units
