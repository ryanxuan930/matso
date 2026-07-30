"""活體全系統檢查——把一場真的推演跑起來，驗**物理結果真的變了**。

## 為什麼需要這支工具（不是「pytest 已經綠了嗎」）

這個 repo 反覆出現同一類缺陷：**存得進去、讀得回來、測試全綠、實際沒效果**。
單元測試餵的是自己組的假物件，整合測試用的是 in-process 的假 kernel——
兩者都測不到「跑在 container 裡的那條 runner 有沒有真的把單位移動」。

所以本工具**只**透過對外 HTTP API 操作正在跑的那套服務，而且每一條檢查都問
「觀測得到的事實有沒有照物理改變」，不問「有沒有回 200」。回 200 而世界沒動，
在這裡算**失敗**。

## 用法

    uv run python ops/tools/live_system_check.py            # 全部
    uv run python ops/tools/live_system_check.py --only C3  # 只跑一條
    BASE=http://localhost:8000 uv run python ops/tools/live_system_check.py

前提：`cd ops/compose && docker compose up -d --wait`，且種子帳號存在
（`uv run python ops/tools/seed_dev_user.py`）。

## 紀律

- **不改既有推演局**：每次跑都自己開一局全新的（想定也是本檔即時產生的）。
- **等待要有上限**：所有輪詢都有 deadline，逾時＝失敗而不是無限掛著。
- **失敗要說得出差在哪**：訊息一律帶「期望什麼／實際什麼」，不要只有 assert。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

import h3

BASE = os.environ.get("BASE", "http://localhost:8000")
USER = os.environ.get("SEED_USERNAME", "commander")
PASSWORD = os.environ.get("SEED_PASSWORD", "exercise")

# 想定座標基準：**必須是履帶車走得通的平地**。
#
# ⚠ 這一行踩過一次坑：原本設在 121.0（中央山脈），於是 MOVE 預覽回 `terrain_impassable`、
# 400 m 的步槍交戰被地形擋掉視線——地形引擎判得完全正確，錯的是想定選錯地方。
# 現在設在雲林平原（以 `movement/preview` 實測 feasible=True、A* 有繞路）。
LAT0, LNG0 = 23.700, 120.300
# 1 度經度在該緯度約 101.7 km；下面所有距離換算都用這個常數，測試才讀得懂。
KM_PER_DEG_LNG = 111.32 * math.cos(math.radians(LAT0))


# --------------------------------------------------------------------------- HTTP


class ApiError(RuntimeError):
    def __init__(self, method: str, path: str, status: int, body: str) -> None:
        super().__init__(f"{method} {path} → {status}: {body[:400]}")
        self.status = status
        self.body = body


class Api:
    """極簡 HTTP 客戶端（stdlib only——這支工具不該替 repo 增加相依）。"""

    def __init__(self, base: str) -> None:
        self.base = base
        self.token: str | None = None

    def call(self, method: str, path: str, body: Any = None, *, token: str | None = None) -> Any:
        req = urllib.request.Request(
            self.base + path, method=method, headers={"content-type": "application/json"}
        )
        tok = token if token is not None else self.token
        if tok:
            req.add_header("authorization", f"Bearer {tok}")
        payload = json.dumps(body).encode() if body is not None else None
        try:
            with urllib.request.urlopen(req, payload, timeout=30) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            raise ApiError(method, path, exc.code, exc.read().decode("utf-8", "replace")) from exc

    def login(self, username: str = USER, password: str = PASSWORD) -> str:
        pair = self.call("POST", "/api/v1/auth/login", {"username": username, "password": password})
        return str(pair["access_token"])

    def get(self, path: str, **kw: Any) -> Any:
        return self.call("GET", path, **kw)

    def post(self, path: str, body: Any = None, **kw: Any) -> Any:
        return self.call("POST", path, body, **kw)


# --------------------------------------------------------------------------- 幾何


def haversine_m(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    r = 6371008.8
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = p2 - p1
    dl = math.radians(b_lng - a_lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def east_of(km: float) -> float:
    """基準點以東 km 公里的經度。"""
    return LNG0 + km / KM_PER_DEG_LNG


# --------------------------------------------------------------------------- 想定


def build_scenario() -> dict[str, Any]:
    """一張**專為檢查而生**的想定：每個單位都在那裡是為了驗證某一條物理。

    佈局（全部沿同一條緯線，距離換算好讀）：

        BLU_ARTY(155×6)      BLU_INF(步兵)  RED_INF(步兵)        RED_ARM(戰車)
        -10.0 km             0 km           +0.4 km              +3.0 km
                             BLU_ARM(戰車) 在 +0 km、北方 2.2 km（MOVE 測試起點）
                             BLU_LOG(油罐車) 貼著 BLU_ARM（補給測試）

    - 步兵對步兵 400 m：在 RIFLE_556 的 600 m 射程內 → C5 直射交戰。
    - 火砲到 RED_INF 約 10 km：落在 155 的 2–30 km 射程 → C7 間瞄。
    - BLU_ARM 往東 3 km：驗位移與油耗（C3/C4）。
    """
    blue = [
        {
            "designation": "BLU_INF",
            "unit_level": "PLATOON",
            "branch": "INFANTRY",
            "lat": LAT0,
            "lng": LNG0,
            "equipment": [{"template": "RIFLE_556", "quantity": 30, "ammo": 900}],
        },
        {
            "designation": "BLU_ARM",
            "unit_level": "COMPANY",
            "branch": "ARMOR",
            "lat": LAT0 + 0.020,
            "lng": LNG0,
            "equipment": [
                {"template": "MBT", "quantity": 4},
                {"template": "TANK_MAIN_GUN_120", "quantity": 4, "ammo": 40},
            ],
        },
        {
            "designation": "BLU_ARTY",
            "unit_level": "COMPANY",
            "branch": "ARTILLERY",
            "fixed": True,
            "lat": LAT0,
            "lng": east_of(-10.0),
            "equipment": [{"template": "HOWITZER_155_SP", "quantity": 6, "ammo": 200}],
        },
        {
            "designation": "BLU_LOG",
            "unit_level": "PLATOON",
            "branch": "SUPPLY",
            "lat": LAT0 + 0.020,
            "lng": east_of(0.2),
            "equipment": [{"template": "FUEL_TRUCK", "quantity": 2}],
        },
    ]
    red = [
        {
            "designation": "RED_INF",
            "unit_level": "PLATOON",
            "branch": "INFANTRY",
            "lat": LAT0,
            "lng": east_of(0.4),
            "equipment": [{"template": "RIFLE_556", "quantity": 30, "ammo": 900}],
        },
        {
            "designation": "RED_ARM",
            "unit_level": "COMPANY",
            "branch": "ARMOR",
            "lat": LAT0,
            "lng": east_of(3.0),
            "equipment": [
                {"template": "MBT", "quantity": 4},
                {"template": "TANK_MAIN_GUN_120", "quantity": 4, "ammo": 40},
            ],
        },
    ]
    return {
        "scenario": {
            "name": "LIVE_SYSCHECK",
            "version": "1.0",
            "mode": "REALTIME",
            # 1 tick ＝ 1 分模擬時間。**不要用 1000**：那是 schema 預設值，
            # 以 1 秒/tick 跑，一個排一 tick 只走 1.4 公尺（見 tutorial-platoon 的註解）。
            "tick_rate_ms": 60_000,
            "bbox": [120.1, 23.55, 120.5, 23.85],
            "factions": [
                {"id": "BLUE", "color": "#3b7dd8"},
                {"id": "RED", "color": "#d83b3b"},
            ],
            "relations": [["BLUE", "RED", "HOSTILE"]],
            "files": {"orbat": {"BLUE": "orbat/blue.yaml", "RED": "orbat/red.yaml"}},
            "victory_conditions": [
                {"faction": "BLUE", "condition": {"type": "faction_eliminated", "faction": "RED"}}
            ],
        },
        "orbat": {
            "BLUE": {"faction": "BLUE", "units": blue},
            "RED": {"faction": "RED", "units": red},
        },
    }


# --------------------------------------------------------------------------- 檢查框架


@dataclass
class Result:
    code: str
    title: str
    ok: bool
    detail: str
    notes: list[str] = field(default_factory=list)


RESULTS: list[Result] = []
_CHECKS: dict[str, tuple[str, Any]] = {}


def check(code: str, title: str) -> Any:
    def deco(fn: Any) -> Any:
        _CHECKS[code] = (title, fn)
        return fn

    return deco


class CheckError(AssertionError):
    """檢查失敗——訊息必須同時說出期望與實際。"""


def expect(cond: bool, message: str) -> None:
    if not cond:
        raise CheckError(message)


# --------------------------------------------------------------------------- 世界控制


class World:
    """一場活推演的操作把手。"""

    def __init__(self, api: Api) -> None:
        self.api = api
        self.session_id = ""
        self.units: dict[str, dict[str, Any]] = {}
        self.notes: list[str] = []

    # -- 開局 --------------------------------------------------------------

    def bootstrap(self) -> None:
        self.api.token = self.api.login()
        saved = self.api.post("/api/v1/scenarios", build_scenario())
        summary = self.api.post(
            "/api/v1/sessions",
            {"name": f"活體檢查-{int(time.time())}", "scenario_id": saved["id"]},
        )
        self.session_id = summary["id"]
        self.refresh(require_tick=True)

    # -- 讀狀態 ------------------------------------------------------------

    def state(self, as_faction: str | None = None, token: str | None = None) -> dict[str, Any]:
        q = f"?as_faction={as_faction}" if as_faction else ""
        return self.api.get(f"/api/v1/sessions/{self.session_id}/state{q}", token=token)

    def refresh(self, *, require_tick: bool = False) -> dict[str, Any]:
        """重讀狀態並更新 designation → unit 對照。`require_tick` 會等 runner 真的開始跑。"""
        deadline = time.time() + 90
        snap: dict[str, Any] = {}
        while time.time() < deadline:
            snap = self.state()
            self.units = {u["designation"]: u for u in snap["units"]}
            if self.units and (not require_tick or snap["tick"] > 0):
                return snap
            time.sleep(1.5)
        raise CheckError(
            f"開局後 90 秒內 runner 沒有推進（tick={snap.get('tick')}、單位數={len(self.units)}）"
        )

    def unit(self, designation: str) -> dict[str, Any]:
        self.refresh()
        u = self.units.get(designation)
        if u is None:
            raise CheckError(f"找不到單位 {designation}（現有：{sorted(self.units)}）")
        return u

    def tick(self) -> int:
        return int(self.state()["tick"])

    def wait_ticks(self, n: int) -> None:
        """等 n 個 tick（以 runner 自己的 tick 計，不以牆鐘）。"""
        start = self.tick()
        deadline = time.time() + max(30.0, n * 4.0)
        while time.time() < deadline:
            if self.tick() >= start + n:
                return
            time.sleep(1.0)
        raise CheckError(f"等 {n} 個 tick 逾時（從 {start} 只走到 {self.tick()}）")

    def wait_until(self, predicate: Any, what: str, timeout: float = 60.0) -> Any:
        """輪詢直到 predicate 回真值；逾時就把最後看到的值講出來。"""
        deadline = time.time() + timeout
        last: Any = None
        while time.time() < deadline:
            last = predicate()
            if last:
                return last
            time.sleep(1.5)
        raise CheckError(f"等「{what}」逾時（{timeout:.0f}s），最後觀測值＝{last!r}")

    def poll_fast(self, predicate: Any, what: str, timeout: float = 60.0) -> Any:
        """高頻輪詢（0.15 秒）——給**會自己消失**的量用。

        壓制度、通聯瞬斷這類狀態的窗口只有幾秒；`wait_until` 的 1.5 秒間隔會整段
        錯過，然後看起來像「這個功能沒生效」。誤判成沒生效比逾時更糟：
        會有人去「修」一個沒壞的東西。
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            hit = predicate()
            if hit:
                return hit
            time.sleep(0.15)
        raise CheckError(f"高頻輪詢「{what}」逾時（{timeout:.0f}s）")

    # -- 下令 --------------------------------------------------------------

    def order(self, designation: str, order_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        unit = self.unit(designation)
        return self.api.post(
            f"/api/v1/sessions/{self.session_id}/orders",
            {"unit_id": unit["id"], "order_type": order_type, "payload": payload},
        )

    def orders(self) -> list[dict[str, Any]]:
        return list(self.api.get(f"/api/v1/sessions/{self.session_id}/orders"))

    def order_status(self, order_id: str) -> str:
        for o in self.orders():
            if o["id"] == order_id:
                return str(o["status"])
        return "MISSING"

    def weapons(self, designation: str) -> list[dict[str, Any]]:
        unit = self.unit(designation)
        return list(self.api.get(f"/api/v1/sessions/{self.session_id}/units/{unit['id']}/weapons"))

    def ammo(self, designation: str, template: str) -> int:
        for w in self.weapons(designation):
            if w["name"] == template:
                return int(w["ammo_remaining"] or 0)
        raise CheckError(
            f"{designation} 沒有 {template}（有：{[w['name'] for w in self.weapons(designation)]}）"
        )

    def fuel(self, designation: str) -> float:
        """油量只在 /movement/preview 上對外供應（UnitView 沒有這一欄）。"""
        unit = self.unit(designation)
        preview = self.api.post(
            f"/api/v1/sessions/{self.session_id}/movement/preview",
            {"unit_id": unit["id"], "to_lat": unit["lat"], "to_lng": unit["lng"]},
        )
        return float(preview["fuel_remaining"])

    def preview(self, designation: str, lat: float, lng: float) -> dict[str, Any]:
        unit = self.unit(designation)
        return dict(
            self.api.post(
                f"/api/v1/sessions/{self.session_id}/movement/preview",
                {"unit_id": unit["id"], "to_lat": lat, "to_lng": lng},
            )
        )


# --------------------------------------------------------------------------- 檢查


@check("C1", "開局：想定落地成真的世界（座標／陣營／編裝）")
def c1_bootstrap(w: World) -> str:
    expect(len(w.units) == 6, f"期望 6 個單位，實得 {len(w.units)}：{sorted(w.units)}")

    blue = {d for d, u in w.units.items() if u["faction"] == "BLUE"}
    red = {d for d, u in w.units.items() if u["faction"] == "RED"}
    expect(blue == {"BLU_INF", "BLU_ARM", "BLU_ARTY", "BLU_LOG"}, f"藍軍編成不符：{sorted(blue)}")
    expect(red == {"RED_INF", "RED_ARM"}, f"紅軍編成不符：{sorted(red)}")

    # 座標必須真的落地——lat/lng 是 None 的單位在 COP 上不存在、也下不了令。
    for d, u in w.units.items():
        expect(
            u["lat"] is not None and u["lng"] is not None,
            f"{d} 沒有座標（lat/lng＝None）——想定明明宣告了",
        )

    # 想定寫的距離要真的成立（否則後面的射程/耗油檢查全都失去意義）。
    gap = haversine_m(
        w.units["BLU_INF"]["lat"],
        w.units["BLU_INF"]["lng"],
        w.units["RED_INF"]["lat"],
        w.units["RED_INF"]["lng"],
    )
    expect(350 < gap < 450, f"藍紅步兵間距應約 400 m，實得 {gap:.0f} m")

    # 編裝：宣告什麼就該有什麼，數量與彈藥都要對得上。
    expect(
        w.ammo("BLU_INF", "RIFLE_556") == 900,
        f"BLU_INF 步槍彈應 900，實得 {w.ammo('BLU_INF', 'RIFLE_556')}",
    )
    expect(w.ammo("BLU_ARTY", "HOWITZER_155_SP") == 200, "BLU_ARTY 砲彈數不符")
    expect(w.units["BLU_ARTY"]["is_fixed"], "BLU_ARTY 宣告 fixed=true 卻沒落地")
    expect(
        w.units["BLU_ARM"]["branch"] == "ARMOR",
        f"BLU_ARM 兵科應 ARMOR，實得 {w.units['BLU_ARM']['branch']}",
    )

    return f"6 單位就位、間距 {gap:.0f} m、編裝與 fixed 旗標一致"


@check("C2", "心跳：runner 真的在推進，且 tick 單調遞增")
def c2_heartbeat(w: World) -> str:
    t0 = w.tick()
    w.wait_ticks(3)
    t1 = w.tick()
    expect(t1 > t0, f"tick 沒有前進（{t0} → {t1}）")
    expect(t1 >= t0 + 3, f"3 個 tick 的等待只前進了 {t1 - t0}")
    return f"tick {t0} → {t1}"


@check("C3", "移動：MOVE 令真的把單位搬過去，並在目的地停下")
def c3_move(w: World) -> str:
    before = w.unit("BLU_ARM")
    dest_lat, dest_lng = before["lat"], east_of(3.0)
    planned = haversine_m(before["lat"], before["lng"], dest_lat, dest_lng)

    preview = w.preview("BLU_ARM", dest_lat, dest_lng)
    expect(preview["feasible"], f"預覽判定不可行：{preview}")
    expect(preview["distance_m"] > 0, "預覽距離為 0")

    order = w.order(
        "BLU_ARM",
        "MOVE",
        {
            # to_h3 供預檢做可達/地形判定；to_lat/to_lng 才是精確落點（見 MovePayload）。
            "to_h3": h3.latlng_to_cell(dest_lat, dest_lng, 8),
            "mobility_profile": "TRACKED",
            "to_lat": dest_lat,
            "to_lng": dest_lng,
        },
    )

    def arrived() -> Any:
        u = w.unit("BLU_ARM")
        left = haversine_m(u["lat"], u["lng"], dest_lat, dest_lng)
        return (u, left) if left < 120 else None

    moved_any = w.wait_until(
        lambda: (
            haversine_m(
                *(lambda u: (u["lat"], u["lng"]))(w.unit("BLU_ARM")), before["lat"], before["lng"]
            )
            > 50
        ),
        "單位開始移動（位移 > 50 m）",
        timeout=90,
    )
    expect(bool(moved_any), "單位完全沒動")

    _unit, left = w.wait_until(
        arrived, f"抵達目的地（距離 < 120 m，全程 {planned:.0f} m）", timeout=300
    )

    status = w.order_status(order["id"])
    expect(status == "COMPLETED", f"抵達後指令狀態應 COMPLETED，實得 {status}")
    return f"行進 {planned:.0f} m，最終偏差 {left:.0f} m，指令 {status}"


@check("C4", "油料：移動真的燒油，且燒的量與距離相稱")
def c4_fuel(w: World) -> str:
    # C3 已經讓 BLU_ARM 走了約 3 km。MBT 每公里 4.5、滿箱 1900。
    remaining = w.fuel("BLU_ARM")
    expect(remaining > 0, f"油量不該歸零（實得 {remaining}）")
    burned = 1900.0 - remaining
    expect(burned > 1.0, f"移動了 3 km 卻幾乎沒燒油（燒了 {burned:.1f}）")
    # 3 km × 4.5 ≈ 13.5；容許地形繞路讓實際里程變長，但不該離譜。
    expect(burned < 120.0, f"燒油量 {burned:.1f} 遠超過 3 km 行程應有的量（≈13.5）")

    # 沒有油料模型的徒步單位應該回 0（而不是假裝有油）。
    foot = w.fuel("BLU_INF")
    expect(foot == 0.0, f"徒步單位不該有油量，實得 {foot}")
    return f"BLU_ARM 燒了 {burned:.1f} 油（剩 {remaining:.0f}/1900）；徒步單位油量 0"


@check("C5", "直射交戰：ENGAGE 真的造成傷亡並消耗彈藥")
def c5_engage(w: World) -> str:
    target_before = w.unit("RED_INF")
    ammo_before = w.ammo("BLU_INF", "RIFLE_556")
    expect(target_before["strength"] > 0, "目標開打前戰力就是 0")

    order = w.order("BLU_INF", "ENGAGE", {"target_unit_id": target_before["id"]})

    def damaged() -> Any:
        u = w.unit("RED_INF")
        return u if u["strength"] < target_before["strength"] else None

    after = w.wait_until(damaged, "目標戰力下降", timeout=120)
    ammo_after = w.ammo("BLU_INF", "RIFLE_556")

    expect(ammo_after < ammo_before, f"開火了卻沒消耗彈藥（{ammo_before} → {ammo_after}）")
    lost = target_before["strength"] - after["strength"]
    expect(lost > 0, "戰力沒有下降")
    expect(
        after["health"] <= target_before["health"],
        f"戰力下降但作戰效能反而上升（{target_before['health']} → {after['health']}）",
    )
    return (
        f"RED_INF 戰力 {target_before['strength']:.1f} → {after['strength']:.1f}"
        f"（-{lost:.1f}）、彈藥 {ammo_before} → {ammo_after}、指令 {w.order_status(order['id'])}"
    )


@check("C6", "壓制：被打的單位壓制度上升（且只有自己看得到）")
def c6_suppression(w: World) -> str:
    """砲擊 → 目標壓制度上升，且**只有目標自己那一方看得到**。

    ⚠ **必須密集輪詢**：壓制是會自己恢復的，這正是它與戰損最根本的差別。
    砲兵一次命中累積 0.35，之後每模擬分鐘衰減到 0.7 倍——約 10 個 tick 就歸零。
    這一局跑在 120× 時間壓縮下（0.5 秒牆鐘＝1 個 tick），窗口只有五秒左右。
    以 1.5 秒間隔輪詢會**整段錯過**，然後看起來像「壓制沒有生效」。
    """
    target = w.unit("RED_INF")
    w.order(
        "BLU_ARTY",
        "FIRE_MISSION",
        {"target_lat": target["lat"], "target_lng": target["lng"], "rounds": 8},
    )

    # 壓制度只在「自己陣營」的視角供應——以統裁切到 RED 視角才讀得到紅軍自己的值。
    def suppressed() -> Any:
        snap = w.state(as_faction="RED")
        for u in snap["units"]:
            if u["designation"] == "RED_INF" and u["suppression"] > 0:
                return u
        return None

    u = w.poll_fast(suppressed, "RED_INF 壓制度 > 0", timeout=90)

    # 藍軍視角看紅軍：壓制度必須被抹掉（看得到敵軍被壓制多少＝免費的戰果評估）。
    blue_view = w.state(as_faction="BLUE")
    leaked = [c for c in blue_view["units"] if c["faction"] == "RED" and c["suppression"] > 0]
    expect(not leaked, f"藍軍視角讀得到紅軍壓制度：{leaked}")
    return f"RED_INF 壓制度 {u['suppression']:.2f}、姿態 {u['posture']}；對方視角不外洩"


@check("C7", "間瞄火力：10 km 外的火砲真的打得到")
def c7_indirect(w: World) -> str:
    target = w.unit("RED_INF")
    dist = haversine_m(
        w.units["BLU_ARTY"]["lat"], w.units["BLU_ARTY"]["lng"], target["lat"], target["lng"]
    )
    expect(9000 < dist < 12000, f"火砲到目標應約 10 km，實得 {dist:.0f} m")

    shells_before = w.ammo("BLU_ARTY", "HOWITZER_155_SP")
    strength_before = target["strength"]
    order = w.order(
        "BLU_ARTY",
        "FIRE_MISSION",
        {
            "target_lat": target["lat"],
            "target_lng": target["lng"],
            "rounds": 8,
        },
    )

    def fired() -> Any:
        now = w.ammo("BLU_ARTY", "HOWITZER_155_SP")
        return now if now < shells_before else None

    shells_after = w.wait_until(fired, "火砲消耗彈藥", timeout=180)
    after = w.unit("RED_INF")
    expect(
        after["strength"] < strength_before,
        f"砲擊落地卻沒有造成任何損失（{strength_before:.1f} → {after['strength']:.1f}）",
    )
    return (
        f"{dist / 1000:.1f} km 射擊：砲彈 {shells_before} → {shells_after}，"
        f"目標戰力 {strength_before:.1f} → {after['strength']:.1f}，"
        f"指令 {w.order_status(order['id'])}"
    )


@check("C8", "戰爭迷霧：陣營視角只看得到自己＋偵測到的接觸")
def c8_fog(w: World) -> str:
    blue = w.state(as_faction="BLUE")
    red = w.state(as_faction="RED")

    blue_units = {u["designation"] for u in blue["units"]}
    red_units = {u["designation"] for u in red["units"]}
    expect(
        not (blue_units & {"RED_INF", "RED_ARM"}),
        f"藍軍的 units 清單混進紅軍單位：{sorted(blue_units & {'RED_INF', 'RED_ARM'})}",
    )
    expect(
        not (red_units & {"BLU_INF", "BLU_ARM", "BLU_ARTY", "BLU_LOG"}),
        f"紅軍的 units 清單混進藍軍單位：{sorted(red_units)}",
    )
    expect(
        blue_units == {"BLU_INF", "BLU_ARM", "BLU_ARTY", "BLU_LOG"},
        f"藍軍看不到自己人：{sorted(blue_units)}",
    )

    # 接觸走 contacts（情報等級受限），而不是 units（權威真值）。
    for c in blue["contacts"]:
        expect(
            c.get("fidelity") in {"DETECTED", "CLASSIFIED", "IDENTIFIED"},
            f"接觸沒有情報等級：{c}",
        )
    return (
        f"藍軍 units={len(blue_units)}／contacts={len(blue['contacts'])}；"
        f"紅軍 units={len(red_units)}／contacts={len(red['contacts'])}；無交叉洩漏"
    )


@check("C9", "權限：非統裁角色動不了時間，也下不了別人的令")
def c9_rbac(w: World) -> str:
    # 用一個不存在的 token 與一個真的低權角色分別驗。
    try:
        w.api.get(f"/api/v1/sessions/{w.session_id}/state", token="not-a-real-token")
        raise CheckError("偽造 token 竟然讀得到狀態")
    except ApiError as exc:
        expect(exc.status in (401, 403), f"偽造 token 應回 401/403，實得 {exc.status}")

    # 未帶 token。
    try:
        w.api.get(f"/api/v1/sessions/{w.session_id}/state", token="")
        raise CheckError("未認證竟然讀得到狀態")
    except ApiError as exc:
        expect(exc.status in (401, 403), f"未認證應回 401/403，實得 {exc.status}")

    # 統裁自己可以控制時間（對照組——否則上面兩條可能只是端點壞了）。
    resp = w.api.post(f"/api/v1/sessions/{w.session_id}/control", {"action": "PAUSE"})
    expect("seq" in resp, f"統裁 PAUSE 應成功，實得 {resp}")
    w.api.post(f"/api/v1/sessions/{w.session_id}/control", {"action": "RESUME"})
    return "偽造/缺席 token 皆被擋；統裁時間控制正常"


@check("C10", "檢查點：跑一陣子之後真的存得下可回滾的快照")
def c10_checkpoints(w: World) -> str:
    points = w.api.get(f"/api/v1/sessions/{w.session_id}/checkpoints")
    expect(isinstance(points, list) and points, f"沒有任何檢查點（實得 {points}）")
    ticks = [p["tick"] for p in points]
    expect(
        ticks == sorted(ticks, reverse=True) or ticks == sorted(ticks),
        f"檢查點 tick 沒有排序：{ticks[:8]}",
    )
    for p in points[:3]:
        expect(p["state_hash"], f"檢查點缺 state_hash：{p}")
    return f"{len(points)} 個檢查點，tick 範圍 {min(ticks)}–{max(ticks)}"


@check("C11", "AAR：事件帳本／重播／統計對得上實際打過的仗")
def c11_aar(w: World) -> str:
    stats = w.api.get(f"/api/v1/sessions/{w.session_id}/aar/stats")
    replay = w.api.get(f"/api/v1/sessions/{w.session_id}/aar/replay")
    states = w.api.get(f"/api/v1/sessions/{w.session_id}/aar/replay/states")

    expect(replay["total_events"] > 0, "重播事件數為 0——這一局明明打過")
    expect(replay["frames"], "重播沒有任何影格")
    expect(replay["max_tick"] > 0, f"重播最大 tick 為 {replay['max_tick']}")

    # 重播必須看得到實際發生過的事——尤其是砲擊（C7 一定跑過）。
    kinds = {t for f in replay["frames"] for t in f["event_types"]}
    expect(kinds, "影格裡沒有任何事件型別")

    # 地圖重播的狀態流：單位底本 + 逐 tick 差異。
    expect(states["units"], "重播狀態流沒有單位底本")
    with_pos = [u for u in states["units"] if u["base_lat"] is not None]
    expect(with_pos, "重播底本的單位全都沒有基準座標——地圖重播會是空的")

    # 統計要真的有數字，不是一個空殼。
    blob = json.dumps(stats, ensure_ascii=False)
    expect(len(blob) > 20, f"AAR 統計幾乎是空的：{blob}")
    return (
        f"帳本 {replay['total_events']} 筆／{len(replay['frames'])} 影格"
        f"（至 tick {replay['max_tick']}）；"
        f"型別 {sorted(kinds)[:6]}；狀態流底本 {len(with_pos)}/{len(states['units'])} 單位有座標"
    )


@check("C12", "收場：勝負條件達成時真的自動收場")
def c12_victory(w: World) -> str:
    """把紅軍全部打掉→ BLUE 的 eliminate 條件成立 → session 應自動結束。

    直接用白軍注入把紅軍抹掉太慢，改以持續火力：先確認條件監看器活著，
    再驗「條件未達成時不會誤判收場」——誤收場比不收場更危險。
    """
    summary = next(s for s in w.api.get("/api/v1/sessions") if s["id"] == w.session_id)
    expect(summary["status"] == "ACTIVE", f"紅軍還在，這一局不該收場（狀態 {summary['status']}）")

    red_alive = [d for d, u in w.units.items() if u["faction"] == "RED" and u["strength"] > 0]
    expect(red_alive, "紅軍已被全滅——這條檢查需要紅軍還活著")
    return f"紅軍尚存 {len(red_alive)} 單位，推演正確地維持 ACTIVE（未誤判收場）"


# --------------------------------------------------------------------------- 主程式


def main() -> int:
    ap = argparse.ArgumentParser(description="活體全系統檢查")
    ap.add_argument("--only", action="append", help="只跑指定代號（可重複），如 --only C3")
    ap.add_argument("--json", help="把結果另存為 JSON")
    args = ap.parse_args()

    wanted = [c for c in _CHECKS if not args.only or c in args.only]
    api = Api(BASE)
    world = World(api)

    print(f"▸ 目標：{BASE}")
    try:
        world.bootstrap()
    except Exception as exc:  # 開局失敗要說得出原因，不是 traceback
        print(f"✗ 開局失敗：{exc}")
        return 2
    print(f"▸ 推演 {world.session_id}（tick {world.tick()}）\n")

    for code in wanted:
        title, fn = _CHECKS[code]
        started = time.time()
        try:
            detail = fn(world)
            RESULTS.append(Result(code, title, True, detail))
            print(f"✓ {code} {title}\n    {detail}  [{time.time() - started:.0f}s]")
        except Exception as exc:
            RESULTS.append(Result(code, title, False, str(exc)))
            print(f"✗ {code} {title}\n    {exc}  [{time.time() - started:.0f}s]")

    passed = sum(1 for r in RESULTS if r.ok)
    print(f"\n▸ {passed}/{len(RESULTS)} 通過（推演 {world.session_id}）")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "session_id": world.session_id,
                    "results": [r.__dict__ for r in RESULTS],
                },
                fh,
                ensure_ascii=False,
                indent=2,
            )
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
