"""想定匯出（O7.3）——LoadedScenario → scenario package 目錄（loader 的逆操作）。

編輯器「編輯→匯出→重新載入」roundtrip 的後端核心：dump 後以 load_scenario_package 重載須等價。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from app.scenario.loader import MOBILITY_OVERRIDE_FILE, LoadedScenario


def scenario_to_dict(loaded: LoadedScenario) -> dict[str, Any]:
    """LoadedScenario → scenario.yaml 的 dict（含 factions/relations/victory/files）。"""
    factions: list[dict[str, Any]] = []
    for fid in loaded.faction_ids:
        entry: dict[str, Any] = {"id": fid}
        # 鍵序對齊 scenario.schema.json 的宣告序（id → display_name → color），
        # 讓 dump 的輸出可預測、diff 乾淨。
        if fid in loaded.faction_display_names:
            entry["display_name"] = loaded.faction_display_names[fid]
        if fid in loaded.faction_colors:
            entry["color"] = loaded.faction_colors[fid]
        factions.append(entry)

    orbat_files = {fid: f"orbat/{fid.lower()}.yaml" for fid in _factions_with_units(loaded)}
    files: dict[str, Any] = {}
    if orbat_files:
        files["orbat"] = orbat_files
    if loaded.msel:
        files["msel"] = "msel.yaml"
    if loaded.roe:
        files["roe"] = "roe.yaml"
    if loaded.mobility_overrides:
        files["overrides_dir"] = "overrides"

    out: dict[str, Any] = {"name": loaded.name, "version": loaded.version}
    if loaded.description is not None:
        out["description"] = loaded.description
    out |= {
        "bbox": list(loaded.bbox),
        "mode": loaded.mode,
        "tick_rate_ms": loaded.tick_rate_ms,
        "hex_resolution": loaded.hex_resolution,
        "aggregate_adjudication_level": loaded.aggregate_adjudication_level,
        "factions": factions,
        "relations": [[a, b, rel.value] for a, b, rel in loaded.relations.declarations()],
        "victory_conditions": loaded.victory_conditions,
    }
    # WP-A3：禁射區必須進 roundtrip——漏了會讓「匯出再匯入」悄悄拆掉保護區
    # （同 `fixed` 旗標曾遺失的前例）。空清單不輸出，維持既有想定的 diff 乾淨。
    if loaded.no_strike_zones:
        out["no_strike_zones"] = [dict(z) for z in loaded.no_strike_zones]
    # ⚠ 本函式是**手寫白名單**——沒列進來的鍵，匯出再匯入就會靜靜消失。
    # `no_strike_zones` 之外，以下三個都曾是（或差點是）受害者，故一律要在此列出。
    # 新增想定層設定時**務必同時改這裡**，否則「匯出再匯入」會拆掉那個設定。
    if loaded.request_quotas:
        out["request_quotas"] = dict(loaded.request_quotas)
    if loaded.indirect_fire_requires_approval:
        out["indirect_fire_requires_approval"] = True
    if loaded.survivability_move:
        out["survivability_move"] = dict(loaded.survivability_move)
    if loaded.allow_fratricide:
        out["allow_fratricide"] = True
    if loaded.day_night:
        out["day_night"] = dict(loaded.day_night)

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
        # WP-B6 修：`fixed` 過去在此遺失——loader 讀得進來、DB 也存得下去，就是匯出時掉了。
        # 症狀是「把想定匯出再匯入，指揮部就開始會移動」。只在 True 時輸出（預設 false，
        # 省略即等價）——與前端編輯器 `...(u.fixed ? { fixed: true } : {})` 同一慣例。
        if u.fixed:
            unit["fixed"] = True
        if u.equipment:
            unit["equipment"] = [
                {"template": name, "quantity": qty, **({"ammo": ammo} if ammo is not None else {})}
                for name, qty, ammo in u.equipment
            ]
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
    if loaded.roe:
        _write("roe.yaml", dict(loaded.roe))
    if loaded.mobility_overrides:
        # 覆寫檔維持 JSON（與 contracts/mobility_matrix.json 同格式，可直接對照 diff）。
        (root / "overrides").mkdir(parents=True, exist_ok=True)
        (root / "overrides" / MOBILITY_OVERRIDE_FILE).write_text(
            json.dumps(loaded.mobility_overrides, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
    for faction in _factions_with_units(loaded):
        _write(f"orbat/{faction.lower()}.yaml", _orbat_dict(loaded, faction))
    if loaded.msel:
        _write("msel.yaml", _msel_dict(loaded))
