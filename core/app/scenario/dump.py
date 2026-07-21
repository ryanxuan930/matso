"""想定匯出（O7.3）——LoadedScenario → scenario package 目錄（loader 的逆操作）。

編輯器「編輯→匯出→重新載入」roundtrip 的後端核心：dump 後以 load_scenario_package 重載須等價。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from app.scenario.loader import LoadedScenario


def scenario_to_dict(loaded: LoadedScenario) -> dict[str, Any]:
    """LoadedScenario → scenario.yaml 的 dict（含 factions/relations/victory/files）。"""
    factions: list[dict[str, Any]] = []
    for fid in loaded.faction_ids:
        entry: dict[str, Any] = {"id": fid}
        if fid in loaded.faction_colors:
            entry["color"] = loaded.faction_colors[fid]
        factions.append(entry)

    orbat_files = {fid: f"orbat/{fid.lower()}.yaml" for fid in _factions_with_units(loaded)}
    files: dict[str, Any] = {}
    if orbat_files:
        files["orbat"] = orbat_files
    if loaded.msel:
        files["msel"] = "msel.yaml"

    out: dict[str, Any] = {
        "name": loaded.name,
        "version": loaded.version,
        "bbox": list(loaded.bbox),
        "mode": loaded.mode,
        "tick_rate_ms": loaded.tick_rate_ms,
        "hex_resolution": loaded.hex_resolution,
        "aggregate_adjudication_level": loaded.aggregate_adjudication_level,
        "factions": factions,
        "relations": [[a, b, rel.value] for a, b, rel in loaded.relations.declarations()],
        "victory_conditions": loaded.victory_conditions,
    }
    if files:
        out["files"] = files
    return out


def _factions_with_units(loaded: LoadedScenario) -> list[str]:
    seen: list[str] = []
    for u in loaded.units:
        if u.faction not in seen:
            seen.append(u.faction)
    return seen


def _orbat_dict(loaded: LoadedScenario, faction: str) -> dict[str, Any]:
    units: list[dict[str, Any]] = []
    for u in loaded.units:
        if u.faction != faction:
            continue
        unit: dict[str, Any] = {"designation": u.designation, "unit_level": u.unit_level}
        if u.lat is not None:
            unit["lat"] = u.lat
        if u.lng is not None:
            unit["lng"] = u.lng
        if u.parent is not None:
            unit["parent"] = u.parent
        units.append(unit)
    return {"faction": faction, "units": units}


def _msel_dict(loaded: LoadedScenario) -> dict[str, Any]:
    return {
        "events": [
            {"id": e.id, "once": e.once, "trigger": e.trigger, "inject": e.inject}
            for e in loaded.msel
        ]
    }


def dump_scenario_package(loaded: LoadedScenario, package_dir: str | Path) -> None:
    """把 LoadedScenario 寫成 scenario package 目錄（scenario.yaml + orbat/*.yaml + msel.yaml）。"""
    root = Path(package_dir)
    (root / "orbat").mkdir(parents=True, exist_ok=True)

    def _write(rel: str, data: dict[str, Any]) -> None:
        (root / rel).write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )

    _write("scenario.yaml", scenario_to_dict(loaded))
    for faction in _factions_with_units(loaded):
        _write(f"orbat/{faction.lower()}.yaml", _orbat_dict(loaded, faction))
    if loaded.msel:
        _write("msel.yaml", _msel_dict(loaded))
