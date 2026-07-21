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
from app.scenario.triggers import MselEntry

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

    colors = {f["id"]: f["color"] for f in sc["factions"] if "color" in f}
    return LoadedScenario(
        name=sc["name"],
        version=sc["version"],
        mode=sc["mode"],
        bbox=list(sc["bbox"]),
        tick_rate_ms=sc["tick_rate_ms"],
        hex_resolution=sc.get("hex_resolution", 8),
        aggregate_adjudication_level=sc.get("aggregate_adjudication_level", "BATTALION"),
        faction_ids=faction_ids,
        faction_colors=colors,
        relations=relations,
        units=units,
        msel=msel,
        victory_conditions=list(sc["victory_conditions"]),
        raw=sc,
    )


def _load_msel(root: Path, rel_path: str | None) -> list[MselEntry]:
    if not rel_path or not (root / rel_path).exists():
        return []
    data = _load_yaml(root / rel_path, rel_path)
    _validate_schema(data, "msel.schema.json", rel_path)
    return [
        MselEntry(
            id=e["id"],
            trigger=e["trigger"],
            inject=e["inject"],
            once=e.get("once", True),
        )
        for e in data["events"]
    ]


def create_session_from_scenario(db: Session, loaded: LoadedScenario, *, master_seed: int) -> str:
    """依載入的想定開局：建 WargameSession + TacticalUnits（含 parent 連結）。回 session id。

    relations 熱狀態載入與 kernel 綁定屬部署層/O7.4；本函式只落地 session 與單位。
    """
    from app.models import SessionMode, TacticalUnit, WargameSession

    session = WargameSession(
        name=loaded.name,
        scenario_id=None,
        master_seed=master_seed,
        mode=SessionMode(loaded.mode),
        current_weather={},
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
        )
        db.add(unit)
        by_designation[(u.faction, u.designation)] = unit
    db.flush()
    for u in loaded.units:
        if u.parent is not None:
            by_designation[(u.faction, u.designation)].parent_id = by_designation[
                (u.faction, u.parent)
            ].id
    db.commit()
    return session.id


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
                )
            )
    return units
