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
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, cast

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
        """發一次請求。**帶著自己的 token 時，遇到 401 會重新登入再試一次。**

        ⚠ 這不是「順手加的韌性」：access token 只有 15 分鐘，而後勤檢查要等**模擬日**
        （1 模擬日 ＝ 12 分鐘牆鐘）。第一次跑 C14 就是在等第一階的路上整支工具被
        `AUTH_TOKEN_EXPIRED` 打斷的——症狀是「跑了 15 分鐘然後說 401」，
        看起來像後勤壞了，其實是這支工具自己的壽命不夠長。

        重試**只在使用 `self.token` 時**發生：C9 用明確的 `token=` 引數驗偽造/缺席 token，
        那兩條必須照樣拿到 401，否則權限檢查會被自己的重試機制蓋掉。
        """
        explicit = token is not None
        try:
            return self._once(method, path, body, token if explicit else self.token)
        except ApiError as exc:
            # 登入端點自己不能重試——帳密錯的 401 會變成無窮遞迴。
            if explicit or exc.status != 401 or path.endswith("/auth/login"):
                raise
            self.token = self.login()  # token 過期 → 重新登入一次
            return self._once(method, path, body, self.token)

    def _once(self, method: str, path: str, body: Any, tok: str | None) -> Any:
        req = urllib.request.Request(
            self.base + path, method=method, headers={"content-type": "application/json"}
        )
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


# 一條檢查：拿到世界把手，回一句「實際觀測到什麼」的敘述（失敗時丟 CheckError）。
CheckFn = Callable[["World"], str]

RESULTS: list[Result] = []
_CHECKS: dict[str, tuple[str, CheckFn]] = {}


def check(code: str, title: str) -> Callable[[CheckFn], CheckFn]:
    def deco(fn: CheckFn) -> CheckFn:
        _CHECKS[code] = (title, fn)
        return fn

    return deco


class CheckError(AssertionError):
    """檢查失敗——訊息必須同時說出期望與實際。"""


def expect(cond: object, message: str) -> None:
    """`cond` 收 object 而非 bool——空清單/空集合本身就是「沒有」，
    逼呼叫端每次寫 `len(x) > 0` 只會讓失敗訊息更難讀。"""
    if not cond:
        raise CheckError(message)


# --------------------------------------------------------------------------- 世界控制


class World:
    """一場活推演的操作把手。"""

    def __init__(self, api: Api) -> None:
        self.api = api
        self.session_id = ""
        self.scenario_id = ""
        self.units: dict[str, dict[str, Any]] = {}
        self.notes: list[str] = []

    # -- 開局 --------------------------------------------------------------

    def bootstrap(self, scenario: dict[str, Any] | None = None, label: str = "活體檢查") -> None:
        """開一局全新的推演。`scenario`/`label` 讓第二張想定（後勤，見 C13）能共用這條路徑。"""
        if self.api.token is None:
            self.api.token = self.api.login()
        saved = self.api.post("/api/v1/scenarios", scenario or build_scenario())
        self.scenario_id = str(saved["id"])
        summary = self.api.post(
            "/api/v1/sessions",
            {"name": f"{label}-{int(time.time())}", "scenario_id": saved["id"]},
        )
        self.session_id = summary["id"]
        self.refresh(require_tick=True)

    def teardown(self) -> None:
        """把自己開的局與想定刪掉。**只刪自己建的**——id 是 bootstrap 當下記下來的。"""
        for path in (
            f"/api/v1/sessions/{self.session_id}" if self.session_id else "",
            f"/api/v1/scenarios/{self.scenario_id}" if self.scenario_id else "",
        ):
            if not path:
                continue
            try:
                self.api.call("DELETE", path)
            except Exception as exc:  # 清不掉要說出來，不要靜靜留一局垃圾在系統裡
                print(f"  ⚠ 清理失敗 {path}：{exc}")

    # -- 讀狀態 ------------------------------------------------------------

    def state(self, as_faction: str | None = None, token: str | None = None) -> dict[str, Any]:
        q = f"?as_faction={as_faction}" if as_faction else ""
        raw = self.api.get(f"/api/v1/sessions/{self.session_id}/state{q}", token=token)
        return cast(dict[str, Any], raw)

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
        raw = self.api.post(
            f"/api/v1/sessions/{self.session_id}/orders",
            {"unit_id": unit["id"], "order_type": order_type, "payload": payload},
        )
        return cast(dict[str, Any], raw)

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


@check("C4", "油料：滿箱是整個編成的量，移動真的燒油且與里程相稱")
def c4_fuel(w: World) -> str:
    """兩件事一起驗，因為它們曾經互相掩蓋。

    1. **滿箱＝編成的量**。容量與油耗都乘了建制數，但「惰性滿油」曾經只填一台車的油
       ——4 輛 MBT 的連隊開局只有 1/4 的油卻以 4 倍速率消耗，續航從 420 km 掉到 105 km。
       畫面上看不出來（油量是個沒有基準的數字），症狀只有「怎麼開沒多遠就拋錨了」。
    2. **移動真的扣油**，而且扣的量與實際里程相稱。

    這裡不寫死任何常數當基準——改用「沒動過的同型單位」當對照組，
    以及「自己移動前後的差」當量測值。寫死的基準會在編裝一改就變成假紅燈。
    """
    # 對照組：RED_ARM 與 BLU_ARM 同編裝（4 輛 MBT），且從未移動 → 應為滿箱。
    full = w.fuel("RED_ARM")
    expect(full > 0, f"沒動過的裝甲單位油量不該是 0（實得 {full}）")
    # MBT 單車 1900 × 4 輛。這是想定寫死的編裝，故可直接比對。
    expect(
        abs(full - 1900.0 * 4) < 1.0,
        f"4 輛 MBT 的滿箱應為 {1900 * 4}，實得 {full}——只填一台車的油會讓續航變成 1/4",
    )

    # 量測：讓 BLU_ARM 再走 1 km，看油量掉多少。
    before = w.fuel("BLU_ARM")
    expect(before > 0, f"BLU_ARM 開走前就沒油了（{before}）")
    unit = w.unit("BLU_ARM")
    dest_lat, dest_lng = unit["lat"], unit["lng"] + 1.0 / KM_PER_DEG_LNG
    w.order(
        "BLU_ARM",
        "MOVE",
        {
            "to_h3": h3.latlng_to_cell(dest_lat, dest_lng, 8),
            "mobility_profile": "TRACKED",
            "to_lat": dest_lat,
            "to_lng": dest_lng,
        },
    )

    def arrived() -> Any:
        u = w.unit("BLU_ARM")
        return u if haversine_m(u["lat"], u["lng"], dest_lat, dest_lng) < 120 else None

    w.wait_until(arrived, "BLU_ARM 走完這 1 km", timeout=180)
    after = w.fuel("BLU_ARM")
    burned = before - after
    # 1 km × 4.5/車 × 4 車 ＝ 18。地形繞路會讓實際里程長一些，但不該離譜。
    expect(burned > 0, f"走了 1 km 卻沒扣油（{before} → {after}）")
    expect(
        4.0 < burned < 90.0,
        f"1 km 燒了 {burned:.1f}（預期約 18，容許繞路）——量級不對",
    )

    # 沒有油料模型的徒步單位應該回 0（而不是假裝有油）。
    foot = w.fuel("BLU_INF")
    expect(foot == 0.0, f"徒步單位不該有油量，實得 {foot}")
    return (
        f"對照組滿箱 {full:.0f}（=1900×4）；BLU_ARM 走 1 km 燒 {burned:.1f}"
        f"（剩 {after:.0f}）；徒步單位油量 0"
    )


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


# --------------------------------------------------------------------------- 後勤（WP-C7）
#
# ## 為什麼後勤自己開一局，不併進上面那張想定
#
# 斷補階梯以**模擬日**計，而 1 模擬日 ＝ 1440 tick ＝ 12 分鐘牆鐘（`pace_compression` 120，
# 1 tick ＝ 1 模擬分鐘 ＝ 0.5 秒牆鐘；本機實測 10 tick/5 秒）。併在同一局的話，
# 「某單位還剩幾天口糧」取決於 C1–C12 跑了多久——那是一個沒人控制得住的變數，
# 於是「開打前還沒斷補」這個前提會時而成立時而不成立，檢查本身變成擲骰。
# 自己開一局，時間軸從 0 起算，每個階段的前提都可以**直接斷言**而不是祈禱。
#
# ## 這一組檢查在防哪一種假綠燈
#
# C7 的三張子卡測試全綠，是因為測試自己 `put_unit` 了 supply 熱狀態鍵——繞過了
# 「想定宣告 → DB → `seed_combat_state` → 熱狀態」這一整段。所以這裡**一格水位都不自己餵**：
# 全部由想定宣告，經真的 loader、真的 runner，再從對外 API 讀回來。
# 讀不到，就是那條鏈斷了。

LOGI_TICK_MS = 60_000  # 與想定宣告的 tick_rate_ms 一致（1 tick ＝ 1 模擬分鐘）
TICKS_PER_SIM_DAY = 86_400_000 // LOGI_TICK_MS  # 1440

# 後勤想定的番號（與主想定分開命名，避免兩張想定的檢查互相參照時看錯單位）。
FED = "LOG_FED"  # 補給充足的對照組射手
HUNGRY = "LOG_HUNGRY"  # 會斷補的射手（單一武器 → 走齊射路徑）
MIXED = "LOG_MIXED"  # 會斷補的射手（雙武器 → 走聯合兵種路徑）
ARTY = "LOG_ARTY"  # 打補給點的火砲（無補給宣告＝中性對照）
TARGET = "LOG_TGT"  # 挨打的目標（無補給宣告＝中性對照）
DRAW = "LOG_DRAW"  # 向補給點拉貨的下游單位
DEPOT = "紅軍前進補給點"

TGT_STRENGTH = 100.0  # 每次齊射前把目標補回這個戰力（見 `fire_volley`）

# C14 要走到第幾個斷補階梯（模擬日）。**這個數字就是牆鐘成本**：1 模擬日 ＝ 12 分鐘。
# 預設 3.0 ＝ 規格驗收條文寫的天數，也是唯一能「逐發」斷言戰損下降的那一階（見 C14 說明）。
_STARVE_DAYS = float(os.environ.get("STARVE_DAYS", "3.0"))


def build_logistics_scenario() -> dict[str, Any]:
    """後勤驗收專用想定。每個數字都是為了讓某一條物理在**檢查跑得完的時間內**可觀測。

    佈局：

        LOG_ARTY(155×6)        LOG_FED/LOG_HUNGRY/LOG_MIXED      LOG_TGT
        -2.0 km                0 km（三個射手同座標）             +0.4 km
        紅軍補給點 -12.0 km ／ LOG_DRAW 在其北方 500 m

    - 三個射手**同一個座標**：射程、地形遮蔽、天氣對三者完全相同，於是「斷補的那個打得比較差」
      不可能是位置差異造成的。控制變因用「構造上相同」而不是「事後統計修正」。
    - LOG_DRAW 距補給點 500 m：在撥交半徑 3 km 內，但在 155 的殺傷半徑（60 m）與
      壓制半徑（180 m）外——**打掉補給點不會順手打死下游單位**，否則「水位不再回升」
      會與「單位死了」混在一起，分不出是哪一個造成的。
    - 火砲距補給點 10 km：落在 155 的 2–30 km 射程內。
    - 勝負條件刻意寫成一條**永遠不會成立**的（RED 要殲滅 BLUE，而 BLUE 全程沒人打它）：
      這一局會被反覆打到 RED 目標見底，若條件寫成「殲滅 RED」，推演會中途自動收場、
      runner 停止推進，後面的等待就會逾時，而症狀看起來像「補給系統壞了」。
    """
    blue = [
        {
            # 對照組：容量 30 日份——它必須撐過整個斷補等待（預設 3 模擬日，加上前置約 0.4 日）。
            # 若照「3 日份」的編裝通則給 3，它會在第 3 天跟著斷補，對照組就沒了。
            "designation": FED,
            "unit_level": "PLATOON",
            "branch": "INFANTRY",
            "lat": LAT0,
            "lng": LNG0,
            "equipment": [{"template": "RIFLE_556", "quantity": 30, "ammo": 9000}],
            "supply": {"I": {"capacity": 30}, "IX": {"capacity": 20}},
        },
        {
            # 斷補組：3 日份的編制，但**出發時只剩 0.35 日份**（＝已經斷補線好幾天的部隊）。
            # 0.35 日 ＝ 504 tick ≈ 4.2 分鐘牆鐘：夠 C13 量完消耗率、夠 C14 打完斷補前的基準，
            # 又不必為了「等它吃完」多花十幾分鐘。
            "designation": HUNGRY,
            "unit_level": "PLATOON",
            "branch": "INFANTRY",
            "lat": LAT0,
            "lng": LNG0,
            "equipment": [{"template": "RIFLE_556", "quantity": 30, "ammo": 9000}],
            "supply": {"I": {"capacity": 3, "on_hand": 0.35}, "IX": {"capacity": 20}},
        },
        {
            # 與 HUNGRY 同時斷補，差別**只有**多一種武器系統 → 裁決走 `_resolve_combined`。
            # 那條路徑有沒有把斷補算進去，是 SPEC「斷補的裝甲連效能下降」在聯合兵種部隊上
            # 成不成立的關鍵，而它不會出現在任何單元測試的視野裡（測試都只給一種武器）。
            "designation": MIXED,
            "unit_level": "PLATOON",
            "branch": "INFANTRY",
            "lat": LAT0,
            "lng": LNG0,
            "equipment": [
                {"template": "RIFLE_556", "quantity": 30, "ammo": 9000},
                {"template": "AUTOCANNON_30", "quantity": 4, "ammo": 2000},
            ],
            "supply": {"I": {"capacity": 3, "on_hand": 0.35}, "IX": {"capacity": 20}},
        },
        {
            # 無 supply 宣告＝中性對照（C13 驗它的水位欄位維持「無」）。
            "designation": ARTY,
            "unit_level": "COMPANY",
            "branch": "ARTILLERY",
            "fixed": True,
            "lat": LAT0,
            "lng": east_of(-2.0),
            "equipment": [{"template": "HOWITZER_155_SP", "quantity": 6, "ammo": 200}],
        },
    ]
    red = [
        {
            # 挨打的目標，也是第二個中性對照。連級 → platform_count 120 →
            # 每平台戰力 0.83，一次齊射掉 ~20 點（打不死，但看得出差別）。
            "designation": TARGET,
            "unit_level": "COMPANY",
            "branch": "INFANTRY",
            "lat": LAT0,
            "lng": east_of(0.4),
            "equipment": [{"template": "RIFLE_556", "quantity": 120, "ammo": 900}],
        },
        {
            # 容量 0.02 日份是刻意的**小**：撥交→耗盡→再撥交的一圈只要 ~20 tick（10 秒牆鐘），
            # 於是「水位真的會回升」與「打掉之後不再回升」都能在一次檢查的期限內反覆觀測到。
            # 給 3 日份的話，一圈要 2.1 模擬日 ＝ 25 分鐘，這條檢查就變成沒人跑得完。
            "designation": DRAW,
            "unit_level": "PLATOON",
            "branch": "SUPPLY",
            "lat": LAT0 + 0.0045,  # 補給點北方約 500 m
            "lng": east_of(-12.0),
            "equipment": [{"template": "RIFLE_556", "quantity": 10, "ammo": 100}],
            "supply": {"I": {"capacity": 0.02}},
        },
    ]
    return {
        "scenario": {
            "name": "LIVE_SYSCHECK_LOGISTICS",
            "version": "1.0",
            "mode": "REALTIME",
            "tick_rate_ms": LOGI_TICK_MS,
            "bbox": [120.1, 23.55, 120.5, 23.85],
            "factions": [
                {"id": "BLUE", "color": "#3b7dd8"},
                {"id": "RED", "color": "#d83b3b"},
            ],
            "relations": [["BLUE", "RED", "HOSTILE"]],
            "files": {"orbat": {"BLUE": "orbat/blue.yaml", "RED": "orbat/red.yaml"}},
            # 永遠不會成立的條件——理由見 docstring。
            "victory_conditions": [
                {"faction": "RED", "condition": {"type": "faction_eliminated", "faction": "BLUE"}}
            ],
            "supply_points": [
                {
                    "name": DEPOT,
                    "faction": "RED",
                    "lat": LAT0,
                    "lng": east_of(-12.0),
                    "stock": {"I": 50, "IX": 50},
                }
            ],
        },
        "orbat": {
            "BLUE": {"faction": "BLUE", "units": blue},
            "RED": {"faction": "RED", "units": red},
        },
    }


_LOGI: World | None = None


def logistics_world(w: World) -> World:
    """後勤那一局的把手（第一次呼叫時開局）。三條檢查共用同一局＝共用同一條時間軸。"""
    global _LOGI
    if _LOGI is None:
        lw = World(w.api)  # 共用已登入的 Api（同一個 token）
        lw.bootstrap(build_logistics_scenario(), label="後勤活體檢查")
        note(f"後勤推演 {lw.session_id}（tick {lw.tick()}）")
        _LOGI = lw
    return _LOGI


def note(message: str) -> None:
    """長時間等待中的進度訊息。跑 3 個模擬日要 36 分鐘，畫面上沒有動靜的話，
    操作員無從分辨「還在等」與「掛住了」。"""
    print(f"    · {message}", flush=True)


def units_now(w: World) -> dict[str, dict[str, Any]]:
    """輕量單位快照（只打 `/units`，不像 `refresh()` 連敵情與標註一起抓）。

    輪詢水位一秒好幾次，抓整包狀態會讓這支工具自己變成負載來源。
    """
    raw = w.api.get(f"/api/v1/sessions/{w.session_id}/units")
    return {u["designation"]: u for u in raw}


def supply_of(unit: dict[str, Any], supply_class: str) -> dict[str, Any] | None:
    """單位某一補給類別的水位視圖；**未編制該類別 → None**（不是 0）。"""
    for level in unit.get("supply") or []:
        if level["supply_class"] == supply_class:
            return dict(level)
    return None


def on_hand(unit: dict[str, Any], supply_class: str = "I") -> float:
    level = supply_of(unit, supply_class)
    expect(level is not None, f"{unit['designation']} 沒有 Class {supply_class} 水位——宣告掉了")
    assert level is not None  # for type checkers
    return float(level["on_hand"])


def sim_days(ticks: int) -> float:
    return ticks * LOGI_TICK_MS / 86_400_000


def wait_for(
    lw: World,
    predicate: Callable[[dict[str, dict[str, Any]]], Any],
    what: str,
    timeout: float,
    progress: Callable[[dict[str, dict[str, Any]]], str] | None = None,
) -> Any:
    """輪詢 `/units` 直到 predicate 回真值。逾時 → 說出期望與最後觀測值。

    `progress` 每 30 秒印一次——這裡的等待動輒十幾分鐘（斷補階梯以模擬日計）。
    """
    deadline = time.time() + timeout
    last_report = 0.0
    seen: Any = None
    while time.time() < deadline:
        snap = units_now(lw)
        seen = predicate(snap)
        if seen:
            return seen
        if progress is not None and time.time() - last_report > 30:
            last_report = time.time()
            note(f"等「{what}」…{progress(snap)}（已 {int(time.time() - deadline + timeout)}s）")
        time.sleep(1.0)
    raise CheckError(f"等「{what}」逾時（{timeout:.0f}s），最後觀測＝{seen!r}")


# -- 齊射量測 ---------------------------------------------------------------


@dataclass
class Volley:
    """一次齊射的觀測值。

    `rounds`（實際擊發彈數）是**確定性**的：`_resolve_volley` 的發射數 ＝
    ceil(建制數 × 效能 × 射速)，一顆骰子都不擲。斷補倍率乘進「效能」，
    所以彈數是這條鏈上**唯一不需要統計就能比較**的量。
    `loss`（造成的戰力損失）另外乘了一個 U(0.8, 1.2) 的離散因子，要靠樣本或靠
    「區間不重疊」才能下結論。
    """

    shooter: str
    phase: str
    rounds: int
    loss: float


_VOLLEYS: list[Volley] = []


def phase_volleys(shooter: str, phase: str) -> list[Volley]:
    return [v for v in _VOLLEYS if v.shooter == shooter and v.phase == phase]


def weapon_ammo(lw: World, designation: str) -> int:
    """該單位所有武器的彈藥總和（聯合兵種單位有兩種武器，要一起算）。"""
    return sum(int(w["ammo_remaining"] or 0) for w in lw.weapons(designation))


def restore_target(lw: World) -> None:
    """把目標補回滿編。

    **每一發齊射前都要做**：齊射的戰損被夾在「目標當前戰力」以下（`loss = min(raw, current)`），
    目標剩 5 點時再打一次，量到的是 5 不是射手的輸出——那會讓斷補的射手看起來與吃飽的一樣強。
    """
    unit = units_now(lw)[TARGET]
    lw.api.call(
        "PATCH",
        f"/api/v1/sessions/{lw.session_id}/units/{unit['id']}",
        {"current_strength": TGT_STRENGTH},
    )
    # 編輯走 live_unit 命令通道，由 runner 在下一個 tick 的 pre_tick 套進熱狀態。
    # DB 立刻就變了（`/units` 讀的是 DB），所以**不能**用讀回來當作已生效的證據。
    lw.wait_ticks(2)


def wait_order_done(lw: World, order_id: str, timeout: float = 120.0) -> str:
    deadline = time.time() + timeout
    status = "MISSING"
    while time.time() < deadline:
        status = lw.order_status(order_id)
        if status in {"COMPLETED", "REJECTED", "CANCELLED"}:
            return status
        time.sleep(0.5)
    raise CheckError(f"指令 {order_id} 在 {timeout:.0f}s 內沒有結案（停在 {status}）")


def fire_volley(lw: World, shooter: str, phase: str) -> Volley:
    """一發齊射：補滿目標 → 下 ENGAGE → 記彈數與戰損。"""
    restore_target(lw)
    ammo_before = weapon_ammo(lw, shooter)
    target_id = units_now(lw)[TARGET]["id"]
    order = lw.order(shooter, "ENGAGE", {"target_unit_id": target_id})
    status = wait_order_done(lw, order["id"])
    expect(status == "COMPLETED", f"{shooter} 的 ENGAGE 沒有完成（{status}）")

    def hit(snap: dict[str, dict[str, Any]]) -> Any:
        # 戰損落到 DB 與彈藥落到 DB 是同一個 tick 的事，但兩者不保證同一次 HTTP 讀到；
        # 以「戰力真的掉了」為準——齊射路徑的損失是期望值算的，命中率 > 0 就一定 > 0。
        return snap[TARGET] if float(snap[TARGET]["strength"]) < TGT_STRENGTH - 1e-6 else None

    after = wait_for(lw, hit, f"{shooter} 的齊射造成戰損", timeout=60)
    ammo_after = weapon_ammo(lw, shooter)
    volley = Volley(
        shooter=shooter,
        phase=phase,
        rounds=ammo_before - ammo_after,
        loss=TGT_STRENGTH - float(after["strength"]),
    )
    expect(
        volley.rounds > 0,
        f"{shooter} 打了一發齊射卻沒消耗彈藥（{ammo_before} → {ammo_after}）",
    )
    _VOLLEYS.append(volley)
    return volley


def volley_round(lw: World, phase: str, shots: int) -> None:
    """一個階段的齊射：三個射手**交錯**各打 `shots` 發。

    交錯而不是一個打完換下一個：天氣、光照、目標姿態都會隨時間漂，交錯讓那個漂
    平均地落在三個射手身上，於是「同一階段內誰打得比較差」仍然只反映補給狀態。
    """
    for i in range(shots):
        for shooter in (FED, HUNGRY, MIXED):
            v = fire_volley(lw, shooter, phase)
            note(f"[{phase}] {shooter} 第 {i + 1} 發：彈 {v.rounds}、戰損 {v.loss:.2f}")


# -- C13 ---------------------------------------------------------------------


@check("C13", "補給消耗：宣告了補給的單位真的在吃，且吃的量與經過的模擬日相稱")
def c13_supply_burn(w: World) -> str:
    """三件事一起驗，因為它們**互相掩蓋**：

    1. 播種鏈通不通（想定宣告 → DB → `seed_combat_state` → 熱狀態 → API）。
       讀不到水位就沒有下文——C7 的所有單元測試都是自己 `put_unit` 繞過這一段的。
    2. 消耗真的發生，而且量與經過的模擬時間相稱（不是「有動就算」）。
    3. Class IX 的消耗恰好是 Class I 的一半——**逐類別的率是真的**，
       不是全域一個計數器在動。這一條能抓到「所有類別共用一個率」這種假實作。

    中性對照在同一局裡：沒宣告補給的兩個單位，水位欄位必須始終是「無」。
    """
    lw = logistics_world(w)

    snap = units_now(lw)
    for designation in (FED, HUNGRY, MIXED, DRAW):
        expect(
            snap[designation].get("supply"),
            f"{designation} 宣告了補給，API 卻回空清單——"
            f"想定→DB→熱狀態這條播種鏈斷了（實得 {snap[designation].get('supply')!r}）",
        )
    # 宣告的容量要一路原封不動地到得了 API（capacity 是編制，執行期不會動它）。
    fed_i = supply_of(snap[FED], "I")
    hungry_i = supply_of(snap[HUNGRY], "I")
    assert fed_i is not None and hungry_i is not None
    expect(
        abs(float(fed_i["capacity"]) - 30.0) < 1e-6,
        f"{FED} 宣告 capacity 30，API 回 {fed_i['capacity']}",
    )
    expect(
        abs(float(hungry_i["capacity"]) - 3.0) < 1e-6,
        f"{HUNGRY} 宣告 capacity 3，API 回 {hungry_i['capacity']}",
    )
    expect(
        float(hungry_i["on_hand"]) <= 0.35 + 1e-6,
        f"{HUNGRY} 宣告 on_hand 0.35（開局就短缺），API 回 {hungry_i['on_hand']}"
        "——`on_hand` 省略才等於滿載，明寫的值不該被忽略",
    )

    # 中性：沒宣告的單位一格都不長。
    for designation in (ARTY, TARGET):
        expect(
            snap[designation]["supply"] == [],
            f"{designation} 沒有宣告任何補給，卻長出水位 {snap[designation]['supply']!r}"
            "——中性保證破了（既有想定會全軍看起來在挨餓）",
        )
        expect(
            snap[designation]["starved_days"] == 0.0,
            f"{designation} 沒宣告補給卻有斷補天數 {snap[designation]['starved_days']}",
        )

    # 量測：同一份 `/state` 取 tick 與水位，避免兩次 HTTP 之間的時間差混進來。
    def sample() -> tuple[int, float, float]:
        state = lw.state()
        by_designation = {u["designation"]: u for u in state["units"]}
        fed = by_designation[FED]
        return int(state["tick"]), on_hand(fed, "I"), on_hand(fed, "IX")

    t0, i0, ix0 = sample()
    lw.wait_ticks(120)  # 120 模擬分鐘 ≈ 60 秒牆鐘
    t1, i1, ix1 = sample()

    elapsed = t1 - t0
    days = sim_days(elapsed)
    burn_i, burn_ix = i0 - i1, ix0 - ix1
    expect(burn_i > 0, f"{FED} 宣告了 Class I 卻沒有消耗（{i0} → {i1}，經過 {elapsed} tick）")
    expect(burn_ix > 0, f"{FED} 宣告了 Class IX 卻沒有消耗（{ix0} → {ix1}）")
    # 校準錨點：Class I 1.0 份/模擬日（存量的單位就是「補給日」）、Class IX 0.5 點/日。
    # 容差 12%：水位在 API 端捨到小數第三位、tick 與熱狀態取樣也會差一兩個 tick。
    expect(
        abs(burn_i - days) <= max(0.12 * days, 0.0015),
        f"Class I 消耗與經過時間不相稱：{elapsed} tick ＝ {days:.4f} 模擬日，"
        f"期望消耗 ≈{days:.4f} 份（1.0 份/日），實得 {burn_i:.4f}",
    )
    expect(
        abs(burn_ix - days * 0.5) <= max(0.12 * days * 0.5, 0.0015),
        f"Class IX 消耗與經過時間不相稱：期望 ≈{days * 0.5:.4f}（0.5 點/日），實得 {burn_ix:.4f}",
    )
    expect(
        snap[FED]["starved_days"] == 0.0,
        f"{FED} 還有 {i1:.2f} 份口糧卻已在累積斷補天數",
    )
    return (
        f"{elapsed} tick（{days:.4f} 模擬日）：Class I {i0:.4f}→{i1:.4f}（-{burn_i:.4f}，"
        f"期望 {days:.4f}）、Class IX {ix0:.4f}→{ix1:.4f}（-{burn_ix:.4f}，"
        f"期望 {days * 0.5:.4f}）；{ARTY}/{TARGET} 水位維持「無」"
    )


# -- C14 ---------------------------------------------------------------------


def _starve_steps(target_days: float) -> list[tuple[float, float]]:
    """要走過的斷補階梯（天數, 效能倍率），取自 `adjudication/supply.STARVATION_STEPS`。"""
    ladder = [(1.0, 0.9), (2.0, 0.75), (3.0, 0.5), (5.0, 0.25)]
    return [step for step in ladder if step[0] <= target_days + 1e-9]


@check("C14", "斷補階梯：斷補的部隊真的打得比較差（彈數確定性下降＋戰損同步下降）")
def c14_starvation(w: World) -> str:
    """SPEC 驗收條文「斷補的裝甲連 3 模擬日後效能階梯下降」的活體版。

    ## 「效能降低」怎麼觀測

    兩個量一起看，因為它們各自補對方的洞：

    - **每次齊射的實際擊發彈數**——`_resolve_volley` 的發射數 ＝ ceil(建制數 × 效能 × 射速)，
      不擲骰。斷補倍率乘進「效能」，所以彈數是**確定性**的：吃飽 90 發、×0.9 剩 81、
      ×0.75 剩 68、×0.5 剩 45。一發齊射就能下結論，不必統計。
    - **對目標造成的戰力損失**——同一條式子再乘一個 U(0.8, 1.2) 的離散因子。它會漂，
      但在 ×0.5 那一階，斷補側的上界（0.5×1.2 ＝ 0.6）低於吃飽側的下界（1.0×0.8 ＝ 0.8），
      於是「**每一發**都比對照組低」是可以斷言的，同樣不必統計。
      （×0.9／×0.75 兩階做不到區間不重疊，所以那兩階只斷言彈數，不對戰損下結論——
      用一次抽樣去說「戰損降了 10%」是在講一個自己都不相信的話。）

    ## 對照組是**同一時刻**的另一個射手，不是同一個射手的過去

    跨階段比較會把天氣、光照、目標姿態的漂移一起算進去（這一局要跑 3 個模擬日）。
    `LOG_FED` 與 `LOG_HUNGRY` 同座標、同編裝、同彈藥、同目標，唯一的差別是補給宣告，
    而且兩者在同一分鐘內交錯開火——階段內的比較不需要任何修正。
    """
    lw = logistics_world(w)
    target_days = _STARVE_DAYS
    steps = _starve_steps(target_days)
    expect(steps, f"STARVE_DAYS={target_days} 連第一階（1 模擬日）都到不了，這條檢查沒有意義")

    # -- 基準：兩個射手都還吃得飽 --------------------------------------------
    snap = units_now(lw)
    for designation in (FED, HUNGRY, MIXED):
        expect(
            snap[designation]["starved_days"] == 0.0,
            f"{designation} 在基準階段就已經斷補 {snap[designation]['starved_days']} 日"
            "——前置步驟花掉太多時間了（本檢查需要一段「還沒斷補」的基準）",
        )
        expect(
            on_hand(snap[designation], "I") > 0,
            f"{designation} 在基準階段口糧已見底（{on_hand(snap[designation], 'I')}）",
        )
    volley_round(lw, "FED", shots=3)

    base_fed = [v.rounds for v in phase_volleys(FED, "FED")]
    base_hungry = [v.rounds for v in phase_volleys(HUNGRY, "FED")]
    expect(
        len(set(base_fed)) == 1 and len(set(base_hungry)) == 1,
        f"基準階段的彈數本身就在跳（{FED}={base_fed}、{HUNGRY}={base_hungry}）"
        "——確定性前提不成立，後面的比較無效",
    )
    expect(
        base_fed[0] == base_hungry[0],
        f"兩個射手在都吃飽時的彈數就不一樣（{FED}={base_fed[0]}、{HUNGRY}={base_hungry[0]}）"
        "——它們的編裝/戰力/距離應該完全相同，對照組不成立",
    )
    base = base_fed[0]

    # -- 等口糧見底 ----------------------------------------------------------
    ran_dry = wait_for(
        lw,
        lambda s: s[HUNGRY] if on_hand(s[HUNGRY], "I") <= 0.0 else None,
        f"{HUNGRY} 口糧見底",
        timeout=600,
        progress=lambda s: f"{HUNGRY} 剩 {on_hand(s[HUNGRY], 'I'):.4f} 份",
    )
    expect(
        float(ran_dry["starved_days"]) >= 0.0,
        "見底了卻沒有斷補天數欄位",
    )
    dry_tick = lw.tick()
    note(f"{HUNGRY} 於 tick {dry_tick} 見底，開始累積斷補天數")

    # -- 逐階驗證 ------------------------------------------------------------
    results: list[str] = []
    for days_threshold, modifier in steps:
        phase = f"STARVED_{days_threshold:g}D"

        def starved_enough(s: dict[str, dict[str, Any]], d: float = days_threshold) -> Any:
            return s[HUNGRY] if float(s[HUNGRY]["starved_days"]) >= d else None

        hit = wait_for(
            lw,
            starved_enough,
            f"斷補累積到 {days_threshold:g} 模擬日",
            # 1 模擬日 ＝ 12 分鐘牆鐘；給 1.6 倍的餘裕（其他推演局也在同一台機器上跑）。
            timeout=days_threshold * 1200 + 600,
            progress=lambda s: f"斷補 {float(s[HUNGRY]['starved_days']):.3f} 日",
        )
        starved_days = float(hit["starved_days"])
        # 斷補天數不是隨便長的：它必須與「見底之後經過了多少 tick」對得上。
        elapsed_days = sim_days(lw.tick() - dry_tick)
        expect(
            abs(starved_days - elapsed_days) <= max(0.1 * elapsed_days, 0.02),
            f"斷補天數與經過時間對不上：見底後過了 {elapsed_days:.3f} 模擬日，"
            f"`starved_days` 卻是 {starved_days:.3f}",
        )
        expect(
            float(units_now(lw)[FED]["starved_days"]) == 0.0,
            f"對照組 {FED} 也開始斷補了——它宣告了 30 日份，不該在此時見底",
        )

        volley_round(lw, phase, shots=3)
        fed_rounds = {v.rounds for v in phase_volleys(FED, phase)}
        hungry_rounds = {v.rounds for v in phase_volleys(HUNGRY, phase)}
        expect(
            fed_rounds == {base},
            f"對照組 {FED} 的彈數在 {phase} 變了（{sorted(fed_rounds)} vs 基準 {base}）"
            "——有別的東西在影響效能，這一階的比較不乾淨",
        )
        predicted = math.ceil(base * modifier)
        actual = sorted(hungry_rounds)
        expect(
            len(hungry_rounds) == 1 and abs(actual[0] - predicted) <= 1,
            f"{phase}：斷補 {starved_days:.2f} 日應套 ×{modifier} → 每次齊射 {predicted} 發"
            f"（基準 {base}），實得 {actual}",
        )
        expect(
            actual[0] < base,
            f"{phase}：斷補了彈數卻沒下降（{actual[0]} vs 基準 {base}）"
            "——`supply_effectiveness` 沒有乘進射手效能",
        )
        line = f"{days_threshold:g}日(×{modifier})：{base}→{actual[0]} 發"

        if modifier <= 0.5:
            # 只有這一階區間不重疊，戰損才能逐發斷言（見 docstring）。
            fed_losses = [v.loss for v in phase_volleys(FED, phase)]
            hungry_losses = [v.loss for v in phase_volleys(HUNGRY, phase)]
            expect(
                max(hungry_losses) < min(fed_losses),
                f"{phase}：斷補側的戰損沒有整段低於對照組"
                f"（斷補 {[round(x, 2) for x in hungry_losses]}、"
                f"對照 {[round(x, 2) for x in fed_losses]}）"
                "——×0.5 對 ×1.0 的離散區間本來就不該重疊",
            )
            ratio = (sum(hungry_losses) / len(hungry_losses)) / (sum(fed_losses) / len(fed_losses))
            expect(
                0.30 <= ratio <= 0.72,
                f"{phase}：戰損比 {ratio:.3f} 不在 ×0.5 的合理範圍（含 ±20% 離散）",
            )
            line += f"、戰損比 {ratio:.3f}"
        results.append(line)
        note(f"[{phase}] 通過：{line}")

    return f"斷補 {steps[-1][0]:g} 模擬日走完階梯 —— " + "；".join(results)


# -- C15 ---------------------------------------------------------------------


def _depot(lw: World) -> dict[str, Any]:
    feats = lw.api.get(f"/api/v1/sessions/{lw.session_id}/map-features")
    points = [f for f in feats if f["kind"] == "SUPPLY_POINT"]
    kinds = sorted({f["kind"] for f in feats})
    expect(points, f"本局沒有任何 SUPPLY_POINT 標註——想定宣告的補給點沒落地（實得 {kinds}）")
    return dict(points[0])


def _ledger_kinds(lw: World) -> set[str]:
    """本局帳本裡出現過的事件型別。

    水位變了但帳本上沒有那一筆，等於這件事**在 AAR 上不存在**——檢討會問「補給什麼時候
    斷的」時沒有任何東西可查。所以撥交與摧毀都要在這裡看得到，不能只看得到熱狀態的數字。
    """
    replay = lw.api.get(f"/api/v1/sessions/{lw.session_id}/aar/replay")
    return {t for f in replay["frames"] for t in f["event_types"]}


def _watch_level(lw: World, designation: str, seconds: float) -> tuple[list[float], int]:
    """盯著某單位的 Class I 水位看一段時間，回（取樣序列, 上升次數）。

    「回升」＝相鄰兩次取樣之間水位變高。用**次數**而不是「最後有沒有比較高」：
    撥交是週期性的（掉到再訂購水位才拉一次），只看頭尾很容易剛好落在同一個相位上。
    """
    samples: list[float] = []
    rises = 0
    deadline = time.time() + seconds
    while time.time() < deadline:
        value = on_hand(units_now(lw)[designation], "I")
        if samples and value > samples[-1] + 1e-9:
            rises += 1
        samples.append(value)
        time.sleep(0.8)
    return samples, rises


@check("C15", "打掉補給點：下游水位不再回升（再建一個新的又回升，證明不是整條鏈死了）")
def c15_supply_point(w: World) -> str:
    """SPEC 驗收條文「打掉補給點後下游單位水位不再回升」的活體版。

    ## 為什麼最後還要再建一個補給點

    「打掉之後不再回升」單獨看是**弱證據**——撥交整條鏈在中途因為任何原因死掉，
    症狀一模一樣。所以打完之後用 `POST /map-features` 新建一個補給點：水位若又開始回升，
    才證明剛才的「不回升」確實是**那個補給點被打掉**造成的，而不是後勤系統整個罷工。
    這一步同時驗到第二條建立路徑（想定宣告 vs 白軍在 COP 上圈）。
    """
    lw = logistics_world(w)
    depot = _depot(lw)
    expect(
        depot["geometry_type"] == "POINT" and len(depot["geometry"]) >= 2,
        f"補給點幾何不是 POINT [lng, lat]：{depot['geometry_type']} {depot['geometry']}",
    )
    lng, lat = float(depot["geometry"][0]), float(depot["geometry"][1])
    expect(
        abs(lat - LAT0) < 0.01 and abs(lng - east_of(-12.0)) < 0.01,
        f"補給點座標落在 ({lat:.4f}, {lng:.4f})，想定宣告的是 "
        f"({LAT0:.4f}, {east_of(-12.0):.4f})——GeoJSON 的 [lng, lat] 寫反了？",
    )
    expect(
        float((depot["attributes"].get("stock") or {}).get("I", 0)) > 0,
        f"補給點庫存沒落地：{depot['attributes']!r}",
    )
    expect(depot["owner_faction"] == "RED", f"補給點歸屬應為 RED，實得 {depot['owner_faction']}")

    # 1. 回升：撥交真的在發生 -------------------------------------------------
    before, rises = _watch_level(lw, DRAW, seconds=45)
    expect(
        rises >= 2,
        f"{DRAW} 在補給點旁 45 秒內只回升 {rises} 次（水位序列 {before[:12]}…）"
        "——自動撥交沒有在跑，後面的『不再回升』就沒有對照",
    )
    note(f"摧毀前：{DRAW} 水位回升 {rises} 次（{min(before):.4f}–{max(before):.4f}）")
    expect(
        "RESUPPLIED" in _ledger_kinds(lw),
        "水位回升了，帳本上卻沒有 RESUPPLIED——這件事在 AAR 上等於沒發生過"
        f"（帳本現有型別：{sorted(_ledger_kinds(lw))}）",
    )

    # 2. 打掉它 --------------------------------------------------------------
    # 走**真正的生產路徑**：砲兵火力任務 → `fire_wiring._destroy_supply_points` → `destroy_at`。
    # 用白軍刪除 map feature 也做得到同一件事，但那不是規格說的「打擊敵後勤」，
    # 而且會跳過火力鏈這一段（該段全 repo 零測試）。
    order = lw.order(ARTY, "FIRE_MISSION", {"target_lat": lat, "target_lng": lng, "rounds": 8})
    status = wait_order_done(lw, order["id"])
    expect(status == "COMPLETED", f"火力任務沒有完成（{status}）——打不到就談不上摧毀")

    def destroyed(_: Any = None) -> Any:
        feats = lw.api.get(f"/api/v1/sessions/{lw.session_id}/map-features")
        for f in feats:
            if f["id"] == depot["id"] and (f["attributes"] or {}).get("destroyed"):
                return f
        return None

    killed = w.wait_until(destroyed, "補給點被標記為已摧毀", timeout=90)
    note(f"補給點 {killed['label']} 已摧毀（仍留在圖上供 AAR 檢視）")
    expect(
        "SUPPLY_POINT_DESTROYED" in _ledger_kinds(lw),
        "補給點被標記為已摧毀，帳本上卻沒有 SUPPLY_POINT_DESTROYED"
        "——AAR 會查不到「後勤是什麼時候、被誰打斷的」",
    )

    # 3. 不再回升 ------------------------------------------------------------
    after, rises_after = _watch_level(lw, DRAW, seconds=45)
    expect(
        rises_after == 0,
        f"補給點被打掉之後 {DRAW} 的水位還在回升 {rises_after} 次（{after[:12]}…）"
        "——打擊敵後勤沒有效果",
    )
    expect(
        after[-1] < after[0] or after[-1] == 0.0,
        f"補給點被打掉之後水位既不回升也不下降（{after[0]:.4f} → {after[-1]:.4f}）"
        "——消耗停了？那斷補永遠不會發生",
    )
    drained = wait_for(
        lw,
        lambda s: s[DRAW] if on_hand(s[DRAW], "I") <= 0.0 else None,
        f"{DRAW} 在失去補給點後耗盡",
        timeout=180,
        progress=lambda s: f"剩 {on_hand(s[DRAW], 'I'):.4f} 份",
    )
    expect(
        float(drained["starved_days"]) >= 0.0,
        "耗盡了卻沒有 starved_days 欄位",
    )

    # 4. 對照：新建一個補給點，水位應該又回升 ---------------------------------
    created = lw.api.post(
        f"/api/v1/sessions/{lw.session_id}/map-features",
        {
            "kind": "SUPPLY_POINT",
            "geometry_type": "POINT",
            "geometry": [lng, lat],
            "owner_faction": "RED",
            "label": "紅軍補給點（重建）",
            "attributes": {"stock": {"I": 50, "IX": 50}},
        },
    )
    expect(created["kind"] == "SUPPLY_POINT", f"建立回應不是補給點：{created}")
    recovered = wait_for(
        lw,
        lambda s: s[DRAW] if on_hand(s[DRAW], "I") > 0.0 else None,
        f"{DRAW} 從新建的補給點拉到貨",
        timeout=120,
        progress=lambda s: f"仍是 {on_hand(s[DRAW], 'I'):.4f} 份",
    )
    return (
        f"摧毀前回升 {rises} 次；火力任務摧毀補給點後 45 秒內回升 {rises_after} 次、"
        f"水位 {after[0]:.4f}→0；以 API 新建補給點後回到 {on_hand(recovered, 'I'):.4f} 份"
    )


# -- C16 ---------------------------------------------------------------------


@check("C16", "斷補對聯合兵種部隊也要生效（多武器單位走的是另一條裁決路徑）")
def c16_combined_starvation(w: World) -> str:
    """C14 的齊射裡一起打的第三個射手：與 `LOG_HUNGRY` 同時斷補，只多帶一種武器。

    多帶一種武器就會落到 `_resolve_combined`（SPEC_EXTEND P2 的聯合兵種加總），
    那是與齊射、聚合並列的**第三條**裁決路徑。規格的驗收條文說的是「斷補的裝甲連」，
    而真實的裝甲連常常就是多武器系統的單位——這條路徑若沒把斷補算進去，
    那句驗收條文對它要適用的對象剛好不成立。

    本檢查靠 C14 蒐集到的齊射資料；單獨跑 `--only C16` 沒有資料可看。
    """
    expect(
        phase_volleys(MIXED, "FED"),
        "沒有基準階段的資料——本檢查需要 C14 先跑（它會順便讓 LOG_MIXED 一起開火）",
    )
    starved_phases = sorted({v.phase for v in _VOLLEYS if v.phase.startswith("STARVED_")})
    expect(starved_phases, "沒有斷補階段的資料——C14 沒有跑完")

    base = {v.rounds for v in phase_volleys(MIXED, "FED")}
    expect(len(base) == 1, f"{MIXED} 基準階段的彈數就在跳：{sorted(base)}")
    base_rounds = base.pop()

    lines = []
    unaffected = []
    for phase in starved_phases:
        rounds = sorted({v.rounds for v in phase_volleys(MIXED, phase)})
        hungry = sorted({v.rounds for v in phase_volleys(HUNGRY, phase)})
        lines.append(f"{phase}: {MIXED} {base_rounds}→{rounds}（單武器對照 {hungry}）")
        if rounds and rounds[0] >= base_rounds:
            unaffected.append(phase)
    expect(
        not unaffected,
        f"{MIXED} 與 {HUNGRY} 同時斷補，彈數卻一發都沒少（{'；'.join(lines)}）"
        "——聯合兵種裁決路徑沒有套用斷補效能；"
        "`adjudication/adjudicator.py` 的 `_resolve_combined` 缺 `supply_effectiveness`，"
        "而齊射（單武器）與聚合（營級）兩條路徑都有",
    )
    return "；".join(lines)


# --------------------------------------------------------------------------- 主程式


def main() -> int:
    global _STARVE_DAYS
    ap = argparse.ArgumentParser(description="活體全系統檢查")
    ap.add_argument("--only", action="append", help="只跑指定代號（可重複），如 --only C3")
    ap.add_argument("--json", help="把結果另存為 JSON")
    ap.add_argument(
        "--starve-days",
        type=float,
        default=_STARVE_DAYS,
        help="C14 要走到第幾個斷補階梯（模擬日）。**1 模擬日 ≈ 12 分鐘牆鐘**，"
        "預設 3.0 是規格驗收條文寫的那個數字（也是唯一能逐發斷言戰損下降的那一階）；"
        "冒煙測試可用 1.0",
    )
    ap.add_argument(
        "--keep-logistics",
        action="store_true",
        help="保留後勤那一局（預設跑完就刪）。失敗要進去看現場時用",
    )
    args = ap.parse_args()
    _STARVE_DAYS = args.starve_days

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

    # 後勤那一局是本工具自己開的，跑完就收——**只刪自己建的**（id 在 bootstrap 當下記下）。
    if _LOGI is not None:
        if args.keep_logistics:
            print(f"\n▸ 後勤推演保留：{_LOGI.session_id}（--keep-logistics）")
        else:
            _LOGI.teardown()

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
