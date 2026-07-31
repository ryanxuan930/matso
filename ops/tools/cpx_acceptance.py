"""迷你 CPX 驗收——以 `armor-breakthrough` 想定跑完整條演習流程並逐環斷言。

## 這支工具要回答的問題

SPEC_V2 §7 的 V2.1 出場條文：「以 armor-breakthrough 想定跑一場 **4 席位＋白軍 MSEL 誘導
＋火協審批**的迷你 CPX，**全程事件鏈可評量**。」

那句話不是「這些端點都回 200」。所以本檔的每一環都問同一件事：
**觀測得到的事實有沒有照著規則改變？** 回 200 而世界沒動，在這裡算失敗。

與 `live_system_check.py` 的分工：那支驗「物理引擎會不會動」（移動/油料/交戰/補給），
用的是它自己現生的四單位想定；本支驗「**演習流程**會不會動」（演習專案 → 開局 →
席位 → 白軍誘導 → 火協審批 → 護欄 → 事件鏈），用的是磁碟上那張官方想定。
HTTP 客戶端直接沿用它的 `Api`——兩支工具對「怎麼跟服務講話」必須是同一份實作。

## 用法

    uv run python ops/tools/cpx_acceptance.py                # 正式驗收
    uv run python ops/tools/cpx_acceptance.py --json /tmp/cpx.json
    uv run python ops/tools/cpx_acceptance.py --keep         # 不清理（現場除錯用）
    uv run python ops/tools/cpx_acceptance.py --only S1,S2   # 只跑某幾環
    uv run python ops/tools/cpx_acceptance.py --no-msel      # 診斷模式，見下
    BASE=http://localhost:8000 uv run python ops/tools/cpx_acceptance.py

離開狀態碼：0＝七環全過，1＝有環節未過。

`--no-msel` 是**診斷**不是驗收：含 MSEL 的局目前每 tick 崩潰、tick 恆為 0，
S3–S7 因而全部量不到。拿掉 MSEL 可以隔離出「其餘六環到底行不行」，同時證明
崩潰的成因就在 MSEL 那條路徑。這個模式永遠回報「未達成」。

前提：`cd ops/compose && docker compose up -d --wait`，且種子帳號存在。

## 紀律

- **只透過對外 HTTP API 操作**。不連 DB、不連 Redis——那兩條路看得到的東西，
  操作員與前端都看不到。
- **每一環都要有反例**。「S4 下不了 MOVE」若沒有「S3 下得了同一道 MOVE」當對照，
  就分不出是席位擋的還是別的東西壞了。這幾組對照是本檔的 mutation check：
  把席位/核准單/禁射區任一條拿掉，對應那條就會翻紅。
- **所有輪詢有 deadline**，逾時＝失敗而不是無限掛著。
- **自己建的自己收**。演習/推演/想定/帳號跑完就刪；名稱以 `_PROTECTED_PREFIXES`
  開頭的一律不碰（那是使用者的資料）。
  ⚠ 參數簽證是**全域**閘門（`governance/seal.active_seal` 不分演習）——留一張沒解除的
  簽證會讓整套系統的軍械庫永遠唯讀。故 finally 一定先解簽證再刪演習。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import yaml

# 同目錄的姊妹工具；ops/tools 沒有 __init__.py，故以檔案所在目錄入 sys.path。
sys.path.insert(0, str(Path(__file__).resolve().parent))

from live_system_check import Api, ApiError, haversine_m

REPO_ROOT = Path(__file__).resolve().parents[2]
SCENARIO_DIR = REPO_ROOT / "scenarios" / "examples" / "armor-breakthrough"
# 想定載不起來時的替代方案（回報中必須明講「驗收用的是替代想定」）。
FALLBACK_SCENARIO_DIR = REPO_ROOT / "scenarios" / "examples" / "joint-defense"

# 清理白名單的反面：名稱以這些字串開頭的資料**一律保留**（使用者既有資料）。
_PROTECTED_PREFIXES = ("玉山", "示範", "校準驗證局", "AI 自主推演", "測試演習")

# 本次跑批的識別碼——所有自建資料都掛這個前綴，清理時只認它。
RUN_TAG = f"CPX驗收-{uuid.uuid4().hex[:8]}"

# 四個席位。role 刻意不給 WHITE_CELL_STAFF/EXERCISE_DIRECTOR：那兩個角色在
# `c2.may_approve` 與下令驗證裡都有旁通，用它們當席位帳號等於把要驗的閘門關掉。
SEATS: tuple[tuple[str, str, str], ...] = (
    ("cdr", "COMMANDER", "COMMANDER"),
    ("s3", "STAFF", "S3_OPS"),
    ("fso", "STAFF", "FSO_FIRES"),
    ("s4", "STAFF", "S4_LOG"),
)


# --------------------------------------------------------------------------- 結果框架


class CheckError(AssertionError):
    """檢查失敗——訊息必須同時說出期望與實際。"""


def expect(cond: object, message: str) -> None:
    """`cond` 收 object 而非 bool：空 list/空 dict 本身就是「沒有」。"""
    if not cond:
        raise CheckError(message)


@dataclass
class Stage:
    code: str
    title: str
    status: str = "PENDING"  # PASS / FAIL / SKIP
    detail: str = ""
    facts: list[str] = field(default_factory=list)  # 逐條實測數值
    defects: list[str] = field(default_factory=list)  # 不中斷流程的失敗（見 `soft`）
    seconds: float = 0.0


STAGES: list[Stage] = []


def soft(st: Stage, cond: object, message: str) -> bool:
    """記下一個**失敗但不中斷**的斷言。

    有些缺陷會讓世界不對，卻不妨礙 CPX 繼續跑（例：想定的 ROE 在 HTTP 邊界掉了——
    局照樣開得起來，只是規則少一半）。用 `expect` 當場拋的話，後面六環全部變成
    「前置失敗」，我們就再也不知道那些環節到底行不行。
    **本函數不會把失敗變成通過**：這一環仍然記 FAIL，只是換個時機講。
    """
    if not cond:
        st.defects.append(message)
        return False
    return True


# --------------------------------------------------------------------------- 想定 bundle


def build_bundle(root: Path, *, drop_msel: bool = False) -> dict[str, Any]:
    """把磁碟上的 scenario package 攤成 `POST /api/v1/scenarios` 吃的記憶體 bundle。

    **不用 `load_scenario_package`**：那是 core 行程內的函式，走它等於繞過 HTTP 邊界，
    而「HTTP 邊界會不會掉東西」正是要驗的事情之一（見 S2 的 roe/overrides 斷言）。

    `drop_msel` 是 `--no-msel` 診斷模式的手：見該旗標的說明。
    """
    sc = _yaml(root / "scenario.yaml")
    files = sc.get("files") or {}
    bundle: dict[str, Any] = {"scenario": sc, "orbat": {}}
    for faction, rel in (files.get("orbat") or {}).items():
        bundle["orbat"][faction] = _yaml(root / rel)
    if files.get("msel") and not drop_msel:
        bundle["msel"] = _yaml(root / files["msel"])
    if files.get("roe"):
        bundle["roe"] = _yaml(root / files["roe"])
    overrides_dir = files.get("overrides_dir")
    if overrides_dir:
        matrix = root / overrides_dir / "mobility_matrix.json"
        if matrix.exists():
            bundle["overrides"] = {"mobility_matrix": json.loads(matrix.read_text("utf-8"))}
    return bundle


def _yaml(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], yaml.safe_load(path.read_text("utf-8")))


# --------------------------------------------------------------------------- 演習把手


class Cpx:
    """一場活演習的操作把手（含每個席位的 token）。"""

    def __init__(self, api: Api, scenario_dir: Path, *, no_msel: bool = False) -> None:
        self.api = api
        self.scenario_dir = scenario_dir
        # 診斷模式：把 MSEL 從 bundle 拿掉（見 `--no-msel`）。永遠不可能得到「達成」。
        self.no_msel = no_msel
        self.exercise_id = ""
        self.session_id = ""
        self.scenario_id = ""
        self.sealed = False
        self.users: dict[str, str] = {}  # 席位別名 → user_id
        self.tokens: dict[str, str] = {}  # 席位別名 → access token
        self.notes: list[str] = []

    # -- 讀狀態 ------------------------------------------------------------

    def state(self, token: str | None = None) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            self.api.get(f"/api/v1/sessions/{self.session_id}/state", token=token),
        )

    def units(self) -> dict[str, dict[str, Any]]:
        return {u["designation"]: u for u in self.state()["units"]}

    def unit(self, designation: str) -> dict[str, Any]:
        us = self.units()
        u = us.get(designation)
        if u is None:
            raise CheckError(f"找不到單位 {designation}（現有 {len(us)} 個：{sorted(us)[:8]}…）")
        return u

    def tick(self) -> int:
        return int(self.state()["tick"])

    def ammo(self, designation: str, template: str) -> int:
        unit = self.unit(designation)
        rows = self.api.get(f"/api/v1/sessions/{self.session_id}/units/{unit['id']}/weapons")
        for w in rows:
            if w["name"] == template:
                return int(w["ammo_remaining"] or 0)
        raise CheckError(f"{designation} 沒有 {template}（有：{[w['name'] for w in rows]}）")

    # -- 下令與申請 --------------------------------------------------------

    def order(
        self, seat: str, designation: str, order_type: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        unit = self.unit(designation)
        return cast(
            dict[str, Any],
            self.api.post(
                f"/api/v1/sessions/{self.session_id}/orders",
                {"unit_id": unit["id"], "order_type": order_type, "payload": payload},
                token=self.tokens[seat],
            ),
        )

    def orders(self, seat: str | None = None) -> list[dict[str, Any]]:
        token = self.tokens[seat] if seat else None
        return list(self.api.get(f"/api/v1/sessions/{self.session_id}/orders", token=token))

    def requests(self, seat: str) -> list[dict[str, Any]]:
        raw = self.api.get(f"/api/v1/sessions/{self.session_id}/requests", token=self.tokens[seat])
        return list(raw["requests"])

    def messages(self, seat: str) -> list[dict[str, Any]]:
        return list(
            self.api.get(f"/api/v1/sessions/{self.session_id}/messages", token=self.tokens[seat])
        )

    # -- 輪詢 --------------------------------------------------------------

    def wait_until(self, probe: Callable[[], Any], what: str, timeout: float = 60.0) -> Any:
        """輪詢直到 probe 回真值；逾時把最後觀測值講出來（不是只說「逾時」）。"""
        deadline = time.time() + timeout
        last: Any = None
        while time.time() < deadline:
            last = probe()
            if last:
                return last
            time.sleep(1.0)
        raise CheckError(f"等「{what}」逾時（{timeout:.0f}s），最後觀測值＝{last!r}")

    def wait_ticks(self, n: int, timeout: float = 90.0) -> int:
        start = self.tick()
        deadline = time.time() + timeout
        while time.time() < deadline:
            now = self.tick()
            if now >= start + n:
                return now
            time.sleep(1.0)
        raise CheckError(f"等 {n} 個 tick 逾時（{timeout:.0f}s；從 {start} 只走到 {self.tick()}）")


def expect_rejected(fn: Callable[[], Any], codes: set[str], what: str) -> dict[str, Any]:
    """要求某個呼叫**被拒**，且錯誤碼落在 `codes` 內。回傳解析後的錯誤 body。

    成功回傳＝失敗：「本來該擋卻放行」正是這類安全機制最危險的失效方式，
    訊息要直接把它講成「未被拒絕」而不是含糊的 assert。
    """
    try:
        fn()
    except ApiError as exc:
        # 契約的錯誤信封（core_api.yaml Error）：{"error": {code, message, details}}。
        raw: dict[str, Any] = {}
        try:
            raw = json.loads(exc.body)
        except (ValueError, TypeError):
            raw = {}
        inner = raw.get("error")
        err: dict[str, Any] = inner if isinstance(inner, dict) else {}
        code = str(err.get("code", ""))
        if code not in codes:
            raise CheckError(
                f"{what}：期望被拒且 code ∈ {sorted(codes)}，實得 HTTP {exc.status} code={code!r}"
                f" body={exc.body[:300]}"
            ) from exc
        return {
            "code": code,
            "message": err.get("message", ""),
            "details": err.get("details") or {},
            "_http_status": exc.status,
        }
    raise CheckError(f"{what}：期望被拒，實際**被接受**（這是放行了不該放行的動作）")


# --------------------------------------------------------------------------- S1 演習專案


def s1_exercise(c: Cpx, st: Stage) -> str:
    """演習專案建立 → 整備勾稽 → 參數簽證。

    三個斷言，每個都配一個反例或對照：
    1. 未勾整備推不動（對照：勾完就推得動）。
    2. 簽證前參數寫得進去、簽證後 403（同一個寫入動作，只差簽證狀態——這是對照組）。
    3. 簽證會自動勾 `params_sealed`，且 `matches=True`。
    """
    ex = c.api.post("/api/v1/exercises", {"name": f"{RUN_TAG}-演習"})
    c.exercise_id = ex["id"]
    expect(ex["phase"] == "PREP", f"新演習應在 PREP，實得 {ex['phase']}")
    required_prep = [i["key"] for i in ex["checklist"] if i["phase"] == "PREP" and i["required"]]
    expect(required_prep, "PREP 階段沒有任何必要整備項——勾稽機制等於不存在")
    st.facts.append(f"演習 {c.exercise_id[:8]} 建立於 PREP，PREP 必要勾稽 {len(required_prep)} 項")

    # (1) 反例：一項都沒勾就想推進。
    err = expect_rejected(
        lambda: c.api.call(
            "PATCH", f"/api/v1/exercises/{c.exercise_id}/phase", {"phase": "REHEARSAL"}
        ),
        {"EXERCISE_CHECKLIST_INCOMPLETE"},
        "未勾整備即推進階段",
    )
    missing = (err.get("details") or {}).get("missing") or []
    expect(
        set(missing) == set(required_prep),
        f"擋下時應列出全部未勾必要項 {sorted(required_prep)}，實得 {sorted(missing)}",
    )
    st.facts.append(f"未勾整備推進被擋：EXERCISE_CHECKLIST_INCOMPLETE missing={sorted(missing)}")

    # (1 對照) 勾完就推得動。
    for key in required_prep:
        c.api.call("PATCH", f"/api/v1/exercises/{c.exercise_id}/checklist/{key}", {"done": True})
    ex = c.api.call("PATCH", f"/api/v1/exercises/{c.exercise_id}/phase", {"phase": "REHEARSAL"})
    expect(ex["phase"] == "REHEARSAL", f"勾完應進 REHEARSAL，實得 {ex['phase']}")
    st.facts.append("勾滿 PREP 必要項後推進成功：PREP → REHEARSAL")

    # (2 對照組) 簽證**前**的參數寫入：必須寫得進去，否則後面的 403 證明不了任何事
    # （環境裡若已有別場演習的簽證生效，這裡就會先失敗，訊息會直接指名是哪一場）。
    #
    # 探針刻意選「把既有範本原封 PUT 回去」而不是新建一個：驗收工具不該在使用者的
    # 全域武器庫留下任何一列，而原封回寫既走得到 `require_params_unsealed`，
    # 又保證資料前後一致。
    templates = c.api.get("/api/v1/equipment-templates")
    expect(templates, "全域武器庫是空的——簽證閘門沒有東西可以驗")
    probe = templates[0]
    probe_body = {
        "name": probe["name"],
        "category": probe["category"],
        "base_stats": probe["base_stats"],
    }
    c.api.call("PUT", f"/api/v1/equipment-templates/{probe['id']}", probe_body)
    st.facts.append(f"簽證前：全域武器庫可寫（原封回寫 {probe['name']} → 200，對照組成立）")

    # (3) 簽證。
    seal = c.api.post(f"/api/v1/exercises/{c.exercise_id}/seal")
    c.sealed = True
    expect(seal["matches"], f"剛簽證就應 matches=True，實得 {seal}")
    ex = c.api.get(f"/api/v1/exercises/{c.exercise_id}")
    sealed_item = next(i for i in ex["checklist"] if i["key"] == "params_sealed")
    expect(sealed_item["done"], "簽證完成應自動勾 params_sealed，實得未勾")
    st.facts.append(f"簽證雜湊 {seal['content_hash'][:12]}…，params_sealed 自動勾稽")

    # (2) 反例：**同一個寫入動作**，簽證後必須 403。
    err = expect_rejected(
        lambda: c.api.call("PUT", f"/api/v1/equipment-templates/{probe['id']}", probe_body),
        {"PARAMS_SEALED"},
        "簽證後寫入全域參數",
    )
    expect(
        (err.get("details") or {}).get("exercise_id") == c.exercise_id,
        f"403 應指名是哪一場演習鎖的，實得 details={err.get('details')}",
    )
    st.facts.append(f"簽證後同一寫入被擋：HTTP {err['_http_status']} PARAMS_SEALED（指名本演習）")

    # (1b) 勾稽閘門在**每一階**都要在，不是只擋 PREP。此刻 params_sealed 已由簽證自動勾上，
    # 但 rehearsal_done 還沒——所以現在推進必須被擋，且只缺那一項。
    err = expect_rejected(
        lambda: c.api.call(
            "PATCH", f"/api/v1/exercises/{c.exercise_id}/phase", {"phase": "EXECUTION"}
        ),
        {"EXERCISE_CHECKLIST_INCOMPLETE"},
        "REHEARSAL 必要項未勾即進 EXECUTION",
    )
    expect(
        set(err["details"].get("missing") or []) == {"rehearsal_done"},
        f"此刻只該缺 rehearsal_done（params_sealed 已由簽證勾上），實得 {err['details']}",
    )
    st.facts.append("REHEARSAL 閘門同樣有效：只缺 rehearsal_done 時被擋（簽證項已自動勾）")

    c.api.call(
        "PATCH", f"/api/v1/exercises/{c.exercise_id}/checklist/rehearsal_done", {"done": True}
    )
    ex = c.api.call("PATCH", f"/api/v1/exercises/{c.exercise_id}/phase", {"phase": "EXECUTION"})
    expect(ex["phase"] == "EXECUTION", f"REHEARSAL → EXECUTION 失敗，實得 {ex['phase']}")
    st.facts.append("勾滿 REHEARSAL 必要項後推進成功：REHEARSAL → EXECUTION")
    return "PREP→REHEARSAL→EXECUTION 走通；未勾整備擋得住、簽證後參數寫入 403"


# --------------------------------------------------------------------------- S2 開局


def s2_session(c: Cpx, st: Stage) -> str:
    """由 armor-breakthrough 建 session 並掛上演習；斷言單位落地 + tick 在前進。"""
    bundle = build_bundle(c.scenario_dir, drop_msel=c.no_msel)
    saved = c.api.post("/api/v1/scenarios", bundle)
    c.scenario_id = saved["id"]
    st.facts.append(f"想定存檔 {saved['name']} v{saved['version']}（{c.scenario_id[:8]}）")

    # HTTP 邊界有沒有掉東西：磁碟上宣告了 roe/overrides，回讀時應該還在。
    # 這一條與「開局」是同一件事——ROE 與機動覆寫掉了，活局的規則就跟磁碟上的想定不同。
    # 這一條用 `soft`：ROE 掉了不妨礙開局，但會讓活局的規則與磁碟上的想定不同。
    # 當場拋的話後面六環全部變成「前置失敗」，就再也看不出它們到底行不行。
    back = c.api.get(f"/api/v1/scenarios/{c.scenario_id}")
    lost = [k for k in ("roe", "overrides") if k in bundle and k not in back]
    soft(
        st,
        not lost,
        f"想定 roundtrip 掉了 {lost}：磁碟上的 armor-breakthrough 宣告了這些，"
        f"經 POST /scenarios 存回來只剩 {sorted(back)}。"
        f"後果＝走 HTTP 開的局沒有 ROE（MLRS 禁令失效）也沒有機動覆寫，"
        f"與磁碟上的想定不是同一場演習",
    )

    summary = c.api.post(
        "/api/v1/sessions",
        {"name": f"{RUN_TAG}-推演", "scenario_id": c.scenario_id, "mode": "WEGO"},
    )
    c.session_id = summary["id"]
    ex = c.api.post(f"/api/v1/exercises/{c.exercise_id}/sessions", {"session_id": c.session_id})
    expect(
        any(s["id"] == c.session_id for s in ex["sessions"]),
        f"掛載後演習底下應看得到本局，實得 {[s['id'][:8] for s in ex['sessions']]}",
    )
    st.facts.append(f"推演 {c.session_id[:8]} 建立並掛上演習（sessions={len(ex['sessions'])}）")

    units = c.units()
    expect(len(units) == 35, f"armor-breakthrough 應載入 35 個單位，實得 {len(units)}")
    blue = [u for u in units.values() if u["faction"] == "BLUE"]
    red = [u for u in units.values() if u["faction"] == "RED"]
    expect(
        len(blue) == 17 and len(red) == 18,
        f"編成應 BLUE 17 / RED 18，實得 BLUE {len(blue)} / RED {len(red)}",
    )
    noloc = [d for d, u in units.items() if u["lat"] is None or u["lng"] is None]
    expect(not noloc, f"這些單位沒有座標（COP 上不存在、也下不了令）：{noloc}")
    b_str = sum(float(u["strength"]) for u in blue)
    r_str = sum(float(u["strength"]) for u in red)
    st.facts.append(f"35 單位全部有座標；總戰力 BLUE {b_str:.0f} / RED {r_str:.0f}")

    # tick 前進——**這是「開局」與「開了一個不會動的局」的分界**。
    t0 = c.tick()
    t1 = c.wait_ticks(3, timeout=90.0)
    expect(t1 > t0, f"runner 沒有推進（tick 停在 {t0}）")
    st.facts.append(f"tick 前進 {t0} → {t1}")
    return f"35 單位就位（BLUE {b_str:.0f}/RED {r_str:.0f} 戰力），tick {t0} → {t1}"


# --------------------------------------------------------------------------- S3 四席位


def s3_seats(c: Cpx, st: Stage) -> str:
    """建四個席位帳號並指派；用 S4 下 MOVE 驗證席位**真的有約束力**。"""
    for alias, role, seat in SEATS:
        username = f"{RUN_TAG.lower()}-{alias}"
        u = c.api.post(
            "/api/v1/users",
            {"username": username, "password": "cpx-accept-2026", "role": role},
        )
        c.users[alias] = u["id"]
        view = c.api.call(
            "PUT",
            f"/api/v1/sessions/{c.session_id}/participants/{u['id']}",
            {"faction": "BLUE", "role": role, "seat_role": seat},
        )
        expect(
            view["seat_role"] == seat,
            f"{alias} 席位應為 {seat}，名冊實得 {view['seat_role']}",
        )
        c.tokens[alias] = c.api.login(username, "cpx-accept-2026")
    st.facts.append("四席位帳號建立並指派：" + "、".join(f"{a}={s}" for a, _, s in SEATS))

    roster = c.api.get(f"/api/v1/sessions/{c.session_id}/participants")
    seats_seen = {p["seat_role"] for p in roster["participants"] if p["seat_role"]}
    expect(
        seats_seen == {s for _, _, s in SEATS},
        f"名冊席位應為 {sorted(s for _, _, s in SEATS)}，實得 {sorted(seats_seen)}",
    )

    # 席位有沒有約束力：同一道 MOVE，S4（後勤官，只能下 RESUPPLY）必須被擋、
    # S3（作戰官）必須下得了。**對照組是重點**——只驗前者的話，
    # 任何原因造成的失敗都會被誤讀成「席位生效了」。
    target = c.unit("B-1-A")
    move_payload = {
        "to_h3": _h3_of(float(target["lat"]), float(target["lng"]) + 0.004),
        "mobility_profile": "TRACKED",
        "to_lat": float(target["lat"]),
        "to_lng": float(target["lng"]) + 0.004,
    }
    err = expect_rejected(
        lambda: c.order("s4", "B-1-A", "MOVE", move_payload),
        {"ORDER_SEAT_DENIED"},
        "後勤官（S4_LOG）下 MOVE 令",
    )
    st.facts.append(
        f"S4_LOG 下 MOVE 被擋：HTTP {err['_http_status']} ORDER_SEAT_DENIED "
        f"（details={err.get('details')}）"
    )
    ok = c.order("s3", "B-1-A", "MOVE", move_payload)
    expect(
        ok["status"] in {"VALIDATED", "PENDING"},
        f"作戰官下同一道 MOVE 應被收下，實得 status={ok['status']}",
    )
    st.facts.append(f"對照組：S3_OPS 下同一道 MOVE 被收下（order {ok['id'][:8]} {ok['status']}）")

    # FSO 不該下得了 MOVE、S3 不該下得了 FIRE_MISSION——兩張表若合而為一就會漏掉一邊。
    expect_rejected(
        lambda: c.order("fso", "B-1-A", "MOVE", move_payload),
        {"ORDER_SEAT_DENIED"},
        "火力支援協調官下 MOVE 令",
    )
    st.facts.append("FSO_FIRES 下 MOVE 亦被擋（席位表是雙向的，不是只擋後勤官）")
    return "四席位就位；S4/FSO 下 MOVE 被 ORDER_SEAT_DENIED，S3 下同一道令通過"


def _h3_of(lat: float, lng: float) -> str:
    import h3

    return cast(str, h3.latlng_to_cell(lat, lng, 8))


# --------------------------------------------------------------------------- S4 MSEL


def s4_msel(c: Cpx, st: Stage) -> str:
    """白軍扣板機注入一則 `manual` 狀況，並斷言**世界真的多了一支部隊**。

    選 `blue-at-reinforcement` 是因為它的動作是 SPAWN_UNITS：單位數 35→36。
    選 MESSAGE 型的話「有沒有生效」只能看信文匣，那比較容易與「本來就有的信」混淆。
    """
    pending = c.api.get(f"/api/v1/sessions/{c.session_id}/msel")["pending"]
    expect(pending, "待命注入清單是空的——runner 沒有發布 MSEL 狀態（該局的 MSEL 沒有在跑）")
    entry = "blue-at-reinforcement"
    expect(entry in pending, f"待命清單應含 {entry}，實得 {pending}")
    st.facts.append(f"待命注入 {len(pending)} 則：{pending}")

    before = c.units()
    expect(
        "B-AT-X" not in before,
        "增援單位 B-AT-X 在扣板機前就已存在——那 manual 觸發等於沒有意義",
    )

    # manual 只有扣板機才會成立：先確認**不扣**的時候它不會自己發生。
    c.wait_ticks(3)
    expect(
        "B-AT-X" not in c.units(),
        "沒扣板機、只是讓時間過去，manual 注入就自己成立了——那不是 manual",
    )

    resp = c.api.post(f"/api/v1/sessions/{c.session_id}/msel/{entry}/fire")
    expect(resp.get("status") == "queued", f"扣板機應回 queued，實得 {resp}")

    def spawned() -> Any:
        us = c.units()
        return us.get("B-AT-X")

    unit = c.wait_until(spawned, f"MSEL {entry} 的增援單位 B-AT-X 出現", timeout=90.0)
    after = c.units()
    expect(
        len(after) == len(before) + 1,
        f"增援後單位數應 {len(before)}→{len(before) + 1}，實得 {len(after)}",
    )
    expect(unit["faction"] == "BLUE", f"B-AT-X 應屬 BLUE，實得 {unit['faction']}")
    expect(
        unit["lat"] is not None and abs(float(unit["lat"]) - 23.730) < 0.01,
        f"B-AT-X 應生成於 23.730/120.372 附近，實得 {unit['lat']}/{unit['lng']}",
    )
    st.facts.append(
        f"扣板機後 B-AT-X 出現：單位數 {len(before)}→{len(after)}、"
        f"座標 {unit['lat']:.3f}/{unit['lng']:.3f}、strength {unit['strength']:.0f}"
    )

    now_pending = c.api.get(f"/api/v1/sessions/{c.session_id}/msel")["pending"]
    expect(
        entry not in now_pending,
        f"已觸發的 {entry} 不該還在待命清單（once=true），實得 {now_pending}",
    )
    st.facts.append(f"{entry} 已自待命清單移除（once 生效），剩 {len(now_pending)} 則")
    return f"白軍扣板機 {entry} → 單位數 {len(before)}→{len(after)}，B-AT-X 落地"


# --------------------------------------------------------------------------- S5 火協審批


def s5_fire_chain(c: Cpx, st: Stage) -> str:
    """FSO 提申請 → 指揮官核准 → 曲射才打得出去，且彈藥與目標戰力真的變。"""
    shooter, target = "B-FA-A", "R-1-1"
    tgt = c.unit(target)
    tlat, tlng = float(tgt["lat"]), float(tgt["lng"])
    gun = c.unit(shooter)
    dist = haversine_m(float(gun["lat"]), float(gun["lng"]), tlat, tlng)
    st.facts.append(f"{shooter} → {target} 距離 {dist / 1000:.1f} km（155 榴射程 2–30 km）")

    fm = {"target_lat": tlat, "target_lng": tlng, "rounds": 12}

    # (1) 未核准 → 擋。
    err = expect_rejected(
        lambda: c.order("fso", shooter, "FIRE_MISSION", fm),
        {"ORDER_FIRE_APPROVAL_REQUIRED"},
        "無核准單的 FIRE_MISSION",
    )
    st.facts.append(f"未核准的 FIRE_MISSION 被擋：HTTP {err['_http_status']} {err['code']}")

    # (1b) 臨機火力申請的觀測要件：對一個沒有任何友軍看得到的座標叫火力，必須被擋。
    err = expect_rejected(
        lambda: c.api.post(
            f"/api/v1/sessions/{c.session_id}/requests",
            {
                "kind": "CALL_FOR_FIRE",
                "params": {"target_lat": 23.775, "target_lng": 120.225},
                "note": "無觀測的臨機火力（反例）",
            },
            token=c.tokens["fso"],
        ),
        {"REQUEST_NO_OBSERVER"},
        "無觀測的 CALL_FOR_FIRE",
    )
    st.facts.append(f"無觀測的 CALL_FOR_FIRE 被擋：{err['code']}")

    # (2) FSO 提 FIRE_SUPPORT 申請。
    req = c.api.post(
        f"/api/v1/sessions/{c.session_id}/requests",
        {
            "kind": "FIRE_SUPPORT",
            "params": {"target_lat": tlat, "target_lng": tlng, "rounds": 12},
            "note": "請求對紅軍南軸線先頭實施阻絕射擊",
        },
        token=c.tokens["fso"],
    )
    expect(req["status"] == "PENDING", f"新申請單應 PENDING，實得 {req['status']}")
    expect(
        req["requested_seat"] == "FSO_FIRES",
        f"申請單應記下 FSO_FIRES 席位，實得 {req['requested_seat']}",
    )
    st.facts.append(f"FSO 送出 FIRE_SUPPORT 申請 {req['id'][:8]}（PENDING）")

    # 申請單一定伴隨一封送到 COMMANDER 席位的 REQUEST 信文——那才是 C2 工件的載體。
    inbox = [m for m in c.messages("cdr") if m["ref_id"] == req["id"]]
    expect(inbox, "指揮官收信匣裡找不到這張申請單的 REQUEST 信文")
    st.facts.append(f"指揮官收到 REQUEST 信文（kind={inbox[0]['kind']}）")

    # (2b) 反例：FSO 自己核准自己的申請 → 席位無權。
    expect_rejected(
        lambda: c.api.post(
            f"/api/v1/sessions/{c.session_id}/requests/{req['id']}/decide",
            {"approve": True, "note": "自己批自己（反例）"},
            token=c.tokens["fso"],
        ),
        {"REQUEST_APPROVAL_DENIED"},
        "FSO 核覆自己的火力支援申請",
    )
    st.facts.append("FSO 自核被擋：REQUEST_APPROVAL_DENIED（核覆權只在 COMMANDER 席位）")

    # (3) 指揮官核准。
    decided = c.api.post(
        f"/api/v1/sessions/{c.session_id}/requests/{req['id']}/decide",
        {"approve": True, "note": "核准；注意避開虎尾鎮立醫院"},
        token=c.tokens["cdr"],
    )
    expect(decided["status"] == "APPROVED", f"核准後應 APPROVED，實得 {decided['status']}")
    st.facts.append(
        f"指揮官核准（decided_by={decided['decided_by']} tick={decided['decided_at_tick']}）"
    )

    # (4) 帶著核准單再打一次 → 應被收下，且世界要真的變。
    ammo_before = c.ammo(shooter, "HOWITZER_155_SP")
    str_before = float(c.unit(target)["strength"])
    order = c.order("fso", shooter, "FIRE_MISSION", {**fm, "fire_request_id": req["id"]})
    expect(
        order["status"] in {"VALIDATED", "PENDING"},
        f"帶核准單的 FIRE_MISSION 應被收下，實得 {order['status']}",
    )
    st.facts.append(f"帶核准單的 FIRE_MISSION 被收下（order {order['id'][:8]}）")

    # 核准單是一次性的：收下令的當下就該兌現成 EXPENDED，否則一張單能打一百次。
    now = next(r for r in c.requests("fso") if r["id"] == req["id"])
    expect(
        now["status"] == "EXPENDED",
        f"核准單於令被收下時應兌現為 EXPENDED，實得 {now['status']}——一張單可重複使用",
    )
    st.facts.append("核准單狀態 APPROVED → EXPENDED（一單一用）")

    def fired() -> Any:
        left = c.ammo(shooter, "HOWITZER_155_SP")
        return left if left < ammo_before else None

    ammo_after = c.wait_until(fired, "砲彈數下降（火力任務真的執行）", timeout=120.0)
    st.facts.append(f"{shooter} 砲彈 {ammo_before} → {ammo_after}（-{ammo_before - ammo_after}）")

    def hurt() -> Any:
        s = float(c.unit(target)["strength"])
        return s if s < str_before else None

    str_after = c.wait_until(hurt, f"{target} 戰力下降", timeout=120.0)
    st.facts.append(f"{target} 戰力 {str_before:.1f} → {str_after:.1f}")
    return (
        f"未核准被擋 → 核准 → 執行：彈 {ammo_before}→{ammo_after}、"
        f"{target} 戰力 {str_before:.1f}→{str_after:.1f}"
    )


# --------------------------------------------------------------------------- S6 禁射區


def s6_no_strike(c: Cpx, st: Stage) -> str:
    """對 NO_STRIKE 區內目標下火力任務必須被拒，且拒絕要留得下追究的痕跡。

    **一定要帶已核准的火協單**：否則被擋的原因會是「沒有核准單」，
    完全驗不到禁射區這一層——安全機制被另一個閘門遮住，看起來有效其實沒測過。
    """
    req = c.api.post(
        f"/api/v1/sessions/{c.session_id}/requests",
        {"kind": "FIRE_SUPPORT", "params": {"note": "禁射區驗證用"}, "note": "禁射區驗證用"},
        token=c.tokens["fso"],
    )
    c.api.post(
        f"/api/v1/sessions/{c.session_id}/requests/{req['id']}/decide",
        {"approve": True, "note": "核准"},
        token=c.tokens["cdr"],
    )
    # 虎尾鎮立醫院圓心（scenario.yaml no_strike_zones，zone_class=NO_STRIKE 硬阻擋）。
    hospital = {"target_lat": 23.737, "target_lng": 120.330, "rounds": 6}
    err = expect_rejected(
        lambda: c.order(
            "fso", "B-FA-A", "FIRE_MISSION", {**hospital, "fire_request_id": req["id"]}
        ),
        {"ORDER_NO_STRIKE_ZONE"},
        "對 NO_STRIKE 區（虎尾鎮立醫院）的 FIRE_MISSION",
    )
    st.facts.append(f"醫院圓心 FIRE_MISSION 被擋：HTTP {err['_http_status']} {err['code']}")

    # 落帳：被拒的令要留下一筆可追究的紀錄。
    # **不能用 payload 過濾**——`OrderResponse` 根本不回 payload（見下方的 soft 斷言），
    # 所以改以「型別 + REJECTED + no_strike 預檢失敗」定位，那是 API 真的給得出來的資訊。
    rejected = [
        o
        for o in c.orders("fso")
        if o["order_type"] == "FIRE_MISSION"
        and o["status"] == "REJECTED"
        and any(
            ch["name"] == "no_strike" and not ch["passed"]
            for ch in ((o.get("precheck") or {}).get("checks") or [])
        )
    ]
    expect(
        rejected,
        "被禁射區擋下的令沒有留下 REJECTED 紀錄——安全機制攔了但事後完全查不到",
    )
    rec = rejected[0]
    failed = next(
        ch for ch in rec["precheck"]["checks"] if ch["name"] == "no_strike" and not ch["passed"]
    )
    st.facts.append(
        f"落帳：order {rec['id'][:8]} FIRE_MISSION/REJECTED tick={rec['issued_at_tick']}，"
        f"precheck.no_strike 失敗「{failed['detail']}」"
    )

    # 「可追究」到什麼程度？行動後檢討會問四件事：**誰、第幾 tick、想做什麼、被哪一條擋下**。
    # 四個都要答得出來才算「留痕」——只知道「有人想打某個禁射區」是追究不了責任的。
    #
    # ⚠ 這一段原本是兩條 soft 缺陷（追不到人、追不到落點）。`OrderResponse` 現在回
    # `issuer_id` 與 `payload`，`no_strike` 的說明也帶上了區名與落點，故改為硬斷言。
    expect(
        rec.get("issuer_id"),
        f"REJECTED 紀錄追不到「誰下的令」（欄位＝{sorted(rec)}）",
    )
    aim = rec.get("payload") or {}
    expect(
        aim.get("target_lat") is not None and aim.get("target_lng") is not None,
        f"REJECTED 紀錄追不到「打哪裡」：FIRE_MISSION 的目標是座標，payload＝{aim}",
    )
    zone_named = "「" in failed["detail"]
    soft(
        st,
        zone_named,
        f"no_strike 的說明沒指出是哪一個保護區（「{failed['detail']}」）"
        "——泛稱只告訴受訓者規則存在，區名才告訴他差點打到什麼",
    )
    st.facts.append(
        f"可追究：下令者 {rec['issuer_id']}、"
        f"落點 {aim.get('target_lat')}, {aim.get('target_lng')}、"
        f"擋下理由「{failed['detail']}」"
    )

    # 對照：同一發砲、同一張核准單流程，換一個不在保護區的座標就打得出去
    # ——證明擋下來的是禁射區，不是「這門砲根本打不了」。
    req2 = c.api.post(
        f"/api/v1/sessions/{c.session_id}/requests",
        {"kind": "FIRE_SUPPORT", "params": {"note": "禁射區對照組"}, "note": "對照組"},
        token=c.tokens["fso"],
    )
    c.api.post(
        f"/api/v1/sessions/{c.session_id}/requests/{req2['id']}/decide",
        {"approve": True, "note": "核准"},
        token=c.tokens["cdr"],
    )
    tgt = c.unit("R-1-2")
    ok = c.order(
        "fso",
        "B-FA-A",
        "FIRE_MISSION",
        {
            "target_lat": float(tgt["lat"]),
            "target_lng": float(tgt["lng"]),
            "rounds": 6,
            "fire_request_id": req2["id"],
        },
    )
    expect(
        ok["status"] in {"VALIDATED", "PENDING"},
        f"保護區外的同型火力任務應放行，實得 {ok['status']}——那擋下醫院的可能不是禁射區",
    )
    st.facts.append(f"對照組：保護區外座標的同型火力任務放行（order {ok['id'][:8]}）")
    return "NO_STRIKE 攔截成立且留痕；保護區外對照組放行"


# --------------------------------------------------------------------------- S7 事件鏈


def s7_event_chain(c: Cpx, st: Stage) -> str:
    """驗收條文的核心：前面每一步都要在帳本／可查介面上追得回來。

    分兩類講清楚，因為它們不在同一個資料來源：
    - **Ledger（`/aar/*`）**：模擬側事實——MSEL 注入、火力執行、戰損。
    - **C2 介面（`/orders`、`/requests`、`/messages`）**：指參側事實——誰下了什麼令、
      誰提了申請、誰核准的、哪一道令被護欄擋下。
    兩邊都算「可評量」，但**要說得出哪一件在哪裡**，否則檢討會上找不到證據。
    """
    replay = c.api.get(f"/api/v1/sessions/{c.session_id}/aar/replay")
    stats = c.api.get(f"/api/v1/sessions/{c.session_id}/aar/stats")
    report = c.api.get(f"/api/v1/sessions/{c.session_id}/aar/report")
    missions = c.api.get(f"/api/v1/sessions/{c.session_id}/aar/missions")

    counts: dict[str, int] = dict(stats["event_counts"])
    expect(counts, "AAR 事件統計是空的——整場演習在帳本上什麼都沒留下")
    st.facts.append(
        f"Ledger 事件 {stats['total_events']} 筆 / max_tick {replay['max_tick']}；"
        f"型別 {json.dumps(counts, ensure_ascii=False, sort_keys=True)}"
    )

    # (a) MSEL 注入：白軍扣的那則板機要在帳本上。
    msel_types = [t for t in counts if t.startswith("MSEL_") or t == "REINFORCEMENT"]
    if c.no_msel:
        # 診斷模式自己把 MSEL 拿掉了，這裡再斷言它會是自導自演。
        st.facts.append("MSEL 注入：診斷模式無 MSEL，本項不適用（正常模式下為硬性斷言）")
    else:
        expect(
            "MSEL_UNITS_SPAWNED" in counts or "REINFORCEMENT" in counts,
            f"帳本裡找不到 MSEL 增援注入（有的 MSEL 相關型別：{msel_types}）",
        )
        st.facts.append(f"MSEL 注入可追：{ {t: counts[t] for t in msel_types} }")

    # (b) 火力執行：曲射任務要在帳本上（面射擊或交戰裁決）。
    fire_types = [
        t for t in counts if t in {"AREA_FIRE_RESOLVED", "ENGAGEMENT_RESOLVED", "FIRE_MISSION"}
    ]
    expect(fire_types, f"帳本裡找不到任何火力執行事件（現有型別：{sorted(counts)}）")
    st.facts.append(f"火力執行可追：{ {t: counts[t] for t in fire_types} }（見 S5 的彈藥與戰損）")

    # (c) 席位下令：每一道令都要看得出是誰、坐哪一席下的。
    orders = c.orders()
    by_type: dict[str, int] = {}
    for o in orders:
        by_type[o["order_type"]] = by_type.get(o["order_type"], 0) + 1
    expect("MOVE" in by_type and "FIRE_MISSION" in by_type, f"下令紀錄不完整：{by_type}")
    st.facts.append(f"下令紀錄：{by_type}（可追到單位、型別、tick、預檢結果）")
    # **「席位下令可評量」在這裡不成立**：Order 表有 issuerId，但沒有任何讀取介面回它，
    # Ledger 也不落 ORDER_* 事件。於是「這道逆襲令是作戰官還是指揮官下的」查不出來
    # ——而那正是四席位 CPX 檢討會最想問的問題。
    soft(
        st,
        all("issuer_id" in o for o in orders),
        f"戰術令追不到下令者：{len(orders)} 道令的 OrderResponse 欄位為 "
        f"{sorted(orders[0]) if orders else []}，沒有 issuer/seat；"
        f"Ledger 也沒有 ORDER_* 事件（型別表：{sorted(counts)}）。"
        f"席位分工在 AAR 上因此不可評量——只有 C2 側的申請/核覆帶得出席位",
    )

    # (d) 護欄攔截：被擋的令留在 REJECTED 紀錄裡（**不在 Ledger**——見下方註記）。
    rejected = [o for o in orders if o["status"] == "REJECTED"]
    expect(rejected, "沒有任何 REJECTED 紀錄——護欄攔截在事後完全查不到")
    reasons = sorted(
        {
            ch["name"]
            for o in rejected
            for ch in ((o.get("precheck") or {}).get("checks") or [])
            if not ch["passed"]
        }
    )
    expect("no_strike" in reasons, f"REJECTED 紀錄裡找不到 no_strike 攔截，實得 {reasons}")
    st.facts.append(f"護欄攔截可追：{len(rejected)} 道 REJECTED，失敗預檢項 {reasons}")
    if "GUARDRAIL_INTERVENTION" not in counts:
        c.notes.append(
            "護欄攔截**不在 Ledger**：`GUARDRAIL_INTERVENTION` 只給 AI 護欄用，"
            "人工下令被預檢擋下只寫 Order 列（status=REJECTED + precheck）。"
            "`/aar/stats` 的 guardrail_blocks 因此恆為 0，追究要走 `GET /orders`。"
        )

    # (e) 火協審批：申請單與核覆的留痕（C2 側）。
    reqs = c.api.get(f"/api/v1/sessions/{c.session_id}/requests")["requests"]
    approved = [r for r in reqs if r["status"] in {"APPROVED", "EXPENDED"}]
    expect(approved, f"找不到任何已核准的申請單：{[(r['kind'], r['status']) for r in reqs]}")
    expect(
        all(r["decided_by"] and r["decided_at_tick"] is not None for r in approved),
        f"核准紀錄缺 decided_by/decided_at_tick：{approved}",
    )
    st.facts.append(
        f"火協審批可追：{len(reqs)} 張申請單，"
        f"{len(approved)} 張經 {approved[0]['decided_by']} 於 tick "
        f"{approved[0]['decided_at_tick']} 核准"
    )
    if not any(t.startswith("REQUEST") or t.startswith("APPROVAL") for t in counts):
        c.notes.append(
            "火協核准**不在 Ledger**：申請/核覆只寫 Request 與 Message 兩張表，"
            "`/aar/*` 讀不到。AAR 敘事因此講不出「這一發是誰批的」。"
        )

    # (f) 敘事與引用：報告的引用序號必須指得到真的事件。
    expect(report["summary"], "AAR 報告沒有摘要")
    expect(
        report["citations"]["valid"],
        f"AAR 報告的引用指向不存在的事件：{report['citations']['invalid_seqs']}",
    )
    st.facts.append(
        f"AAR 報告 {len(report['paragraphs'])} 段、{len(report['lessons'])} 條檢討；"
        f"引用有效={report['citations']['valid']}；任務時間軸 {len(missions)} 條"
    )
    return (
        f"事件鏈成立：Ledger {stats['total_events']} 筆（MSEL/火力）＋"
        f"C2 側 {len(orders)} 道令、{len(reqs)} 張申請單（席位/核准/攔截）"
    )


# --------------------------------------------------------------------------- 清理


def _protected(name: str) -> bool:
    return any(name.startswith(p) for p in _PROTECTED_PREFIXES)


def cleanup(c: Cpx) -> list[str]:
    """把自己建的東西收掉。每一步各自 try——一步失敗不該讓後面的殘留。

    **順序有意義**：簽證是全域閘門，先解它；session 先卸下再刪，
    免得演習底下留著一個指向已刪 session 的參照。
    """
    log: list[str] = []

    def step(label: str, fn: Callable[[], Any]) -> None:
        try:
            fn()
            log.append(f"✓ {label}")
        except Exception as exc:  # 清理失敗只回報，不遮蔽驗收結果
            log.append(f"✗ {label}：{exc}")

    if c.sealed and c.exercise_id:
        step(
            "解除參數簽證（全域閘門，非解不可）",
            lambda: c.api.call("DELETE", f"/api/v1/exercises/{c.exercise_id}/seal"),
        )
    if c.session_id and c.exercise_id:
        step(
            "自演習卸下推演",
            lambda: c.api.call(
                "DELETE", f"/api/v1/exercises/{c.exercise_id}/sessions/{c.session_id}"
            ),
        )
    if c.session_id:
        step("刪除推演", lambda: c.api.call("DELETE", f"/api/v1/sessions/{c.session_id}"))
    if c.scenario_id:
        step("刪除想定", lambda: c.api.call("DELETE", f"/api/v1/scenarios/{c.scenario_id}"))
    if c.exercise_id:
        step("刪除演習專案", lambda: c.api.call("DELETE", f"/api/v1/exercises/{c.exercise_id}"))
    for alias, uid in c.users.items():
        step(f"刪除席位帳號 {alias}", lambda u=uid: c.api.call("DELETE", f"/api/v1/users/{u}"))  # type: ignore[misc]

    # 殘留掃描：本次跑批的前綴若還看得到，就講出來（不自動再刪一次，避免誤刪）。
    for path, label in (("/api/v1/sessions", "推演"), ("/api/v1/exercises", "演習")):
        try:
            rows = c.api.get(path)
        except ApiError:
            continue
        left = [r["name"] for r in rows if str(r.get("name", "")).startswith(RUN_TAG)]
        if left:
            log.append(f"⚠ 仍有殘留{label}：{left}")
    return log


# --------------------------------------------------------------------------- 主程序

_PIPELINE: tuple[tuple[str, str, Callable[[Cpx, Stage], str]], ...] = (
    ("S1", "演習專案：建立 → 整備勾稽 → 參數簽證", s1_exercise),
    ("S2", "開局：armor-breakthrough 落地並掛上演習，tick 在前進", s2_session),
    ("S3", "四席位編組：席位真的有約束力（S4/FSO 被擋、S3 通過）", s3_seats),
    ("S4", "白軍 MSEL 誘導：扣板機後世界真的改變", s4_msel),
    ("S5", "火協審批鏈：申請 → 核准 → 曲射執行（彈藥/戰力都要動）", s5_fire_chain),
    ("S6", "禁射區護欄：NO_STRIKE 目標被拒且留痕", s6_no_strike),
    ("S7", "事件鏈可評量：每一環都追得回來", s7_event_chain),
)


def main() -> int:
    ap = argparse.ArgumentParser(description="迷你 CPX 驗收（V2.1 exit）")
    ap.add_argument("--json", help="把逐環結果寫成 JSON")
    ap.add_argument("--keep", action="store_true", help="跑完不清理（現場除錯用）")
    ap.add_argument("--only", help="只跑某幾環（逗號分隔，如 S1,S2）")
    ap.add_argument(
        "--no-msel",
        action="store_true",
        help=(
            "【診斷模式】把 MSEL 自 bundle 移除後再跑。"
            "用途只有一個：含 MSEL 的局目前每 tick 崩潰、tick 恆為 0（見 S2 失敗訊息），"
            "S3–S7 因而全部量不到。拿掉 MSEL 可以隔離出「其餘六環到底行不行」，"
            "同時證明崩潰的成因就是 MSEL 那條路徑。"
            "**這個模式永遠不算通過驗收**：S4（白軍誘導）在此模式下不存在。"
        ),
    )
    args = ap.parse_args()

    scenario_dir = SCENARIO_DIR
    substitute = False
    if not (scenario_dir / "scenario.yaml").exists():
        scenario_dir = FALLBACK_SCENARIO_DIR
        substitute = True
        print(f"⚠ armor-breakthrough 不存在，改用替代想定：{scenario_dir}")

    api = Api(__import__("live_system_check").BASE)
    api.token = api.login()
    c = Cpx(api, scenario_dir, no_msel=args.no_msel)

    wanted = set(args.only.split(",")) if args.only else {code for code, _, _ in _PIPELINE}
    blocked: str | None = None
    print(f"▸ {RUN_TAG}｜想定 {scenario_dir.name}{'（替代）' if substitute else ''}")
    if args.no_msel:
        print("▸ 【診斷模式 --no-msel】MSEL 已自 bundle 移除；本次結果不構成驗收通過。")
    print()

    try:
        for code, title, fn in _PIPELINE:
            st = Stage(code, title)
            STAGES.append(st)
            if code not in wanted:
                st.status, st.detail = "SKIP", "--only 未選"
                continue
            if blocked:
                st.status, st.detail = "SKIP", f"前置環節 {blocked} 失敗，本環無法執行"
                print(f"– {code} {title}\n    {st.detail}")
                continue
            if code == "S4" and c.no_msel:
                # 診斷模式把 MSEL 拿掉了 → 這一環在此模式下**不存在**，記 FAIL 但不封鎖後續：
                # 那正是這個模式要換來的東西（把 S5–S7 量出來）。
                st.status = "FAIL"
                st.detail = "診斷模式移除了 MSEL；白軍誘導在此模式下不可能成立（成因見 S2）"
                print(f"✗ {code} {title}\n    {st.detail}")
                continue
            started = time.time()
            try:
                st.detail = fn(c, st)
                # `soft` 記下的缺陷同樣算這一環沒過——只是它不中斷後面的環節。
                st.status = "FAIL" if st.defects else "PASS"
            except Exception as exc:  # 驗收工具要收下所有失敗並繼續下一環
                st.status, st.detail = "FAIL", f"{type(exc).__name__}: {exc}"
                blocked = code  # 硬失敗才封鎖後續（soft 缺陷不封鎖）
            st.seconds = time.time() - started
            mark = "✓" if st.status == "PASS" else "✗"
            print(f"{mark} {code} {title}\n    {st.detail}  [{st.seconds:.0f}s]")
            for f in st.facts:
                print(f"      · {f}")
            for d in st.defects:
                print(f"      ✗ 缺陷：{d}")
    finally:
        if args.keep:
            print(f"\n▸ --keep：保留 exercise={c.exercise_id} session={c.session_id}")
        else:
            print("\n▸ 清理")
            for line in cleanup(c):
                print(f"    {line}")

    passed = sum(1 for s in STAGES if s.status == "PASS")
    failed = [s.code for s in STAGES if s.status == "FAIL"]
    skipped = [s.code for s in STAGES if s.status == "SKIP"]
    print(f"\n▸ {passed}/{len(_PIPELINE)} 環通過")
    if failed:
        print(f"▸ 失敗：{failed}")
    if skipped:
        print(f"▸ 未執行：{skipped}")
    for note in c.notes:
        print(f"▸ 註記：{note}")
    # **只有七環全跑且全過、且用的是正牌想定，才算條文達成。**
    # 「跑了一環就宣告達成」是這類驗收工具最容易犯的自我恭維。
    if substitute:
        verdict = "未達成（用的是替代想定，不符條文指定的 armor-breakthrough）"
    elif args.no_msel:
        verdict = "未達成（--no-msel 診斷模式：想定被改過，不是條文要求的那一場）"
    elif failed or skipped:
        verdict = "未達成"
    else:
        verdict = "達成"
    print(f"▸ V2.1 exit 條文：{verdict}")

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {
                    "run_tag": RUN_TAG,
                    "scenario": scenario_dir.name,
                    "substitute": substitute,
                    "session_id": c.session_id,
                    "exercise_id": c.exercise_id,
                    "verdict": verdict,
                    "notes": c.notes,
                    "stages": [s.__dict__ for s in STAGES],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
