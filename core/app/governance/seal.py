"""參數簽證（WP-B4）——快照什麼、怎麼算雜湊、以及誰被鎖住。

## 為什麼「鎖住的是全域」而不是「該演習的 session」

規格寫「凍結後：裝備模板/SimParams 的寫入 API 對**該演習關聯 session** MUST 拒絕」。
但那兩樣東西**都是全域單例**：`EquipmentTemplate` 是一張全域武器庫（無 sessionId），
`SimParams` 存在 `SystemConfiguration.integrationConfig["sim"]`（DB 單一列）。
`POST /equipment-templates` 與 `PUT /system/config` **根本不帶 session_id**——沒有東西可以 scope。

所以實際的規則只能是：**只要有任何一場演習的簽證生效中，這些全域寫入一律拒絕**。
代價是同時進行的散局管理員也改不了武器庫。這個取捨是真實的，寫在這裡而不是藏起來：
在一個 CPX 場地裡，「演習進行中不准動參數」本來就該是全場的規矩。
（PROGRESS.md 早就記過同源的限制：「EquipmentTemplate 是全域表，per-session 覆寫會污染
同時進行的其他局」。）

驗收條文的「未掛演習的散局不受影響」講的是**開局不被拒**，不是寫入保持開放——
散局照跑，只是這段期間全域參數是唯讀的。

## 雜湊的三個細節

1. **雜湊正規化後的 SimParams**（`to_config(parse_sim_params(raw))`），不是庫裡的原始 JSON。
   `parse_sim_params` 會逐欄把壞值/缺值退成預設，於是外觀不同的 JSON 可能產生
   **完全相同的物理**——雜湊原始 JSON 會製造假的「被篡改」。
2. **機動矩陣雜湊檔案內容，不用它的 `version` 欄**。那個欄位是手寫的，沒有任何東西會自動 bump，
   拿它當版本雜湊等於零篡改偵測。
3. **不重用 `compute_state_hash`**（`state/checkpoint.py`）。它的契約釘在熱狀態的 units 子樹上，
   而它的值正是 golden replay 在斷言的東西；讓參數治理去共用它，等於把兩件無關的事綁在一起。
   只重用 `canonical_json`。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import zstandard
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import EquipmentTemplate, Exercise, ExercisePhase, ParameterSeal
from app.movement.mobility_matrix import _MATRIX_PATH
from app.sim_params import load_sim_params, to_config
from app.state.ledger import canonical_json

# 簽證生效中的階段。REVIEW 之後解鎖——演習已經結束，檢討期間再鎖著沒有意義。
SEALED_PHASES = frozenset({ExercisePhase.REHEARSAL, ExercisePhase.EXECUTION})


def build_seal_payload(db: Session) -> dict[str, Any]:
    """簽證要鎖的全域參數快照。

    只涵蓋**目前真的可調**的那個子集：`SimParams` 暴露的欄位、全域武器庫、機動矩陣。
    `docs/PARAMS.md` 的 P 層還有 25+ 個硬編模組常數沒有進 `SimParams`——
    它們改不了也就鎖不了，**明說這個界線**比宣稱「R+P 全鎖」誠實。
    """
    templates = db.execute(select(EquipmentTemplate).order_by(EquipmentTemplate.id)).scalars().all()
    try:
        mobility = _MATRIX_PATH.read_text(encoding="utf-8")
    except OSError as exc:  # 檔案不在 → 記下來，不讓簽證整個失敗
        mobility = f"<unreadable: {exc}>"
    return {
        "seal_version": "1.0",
        # 正規化後的值——見模組說明第 1 點。
        "sim_params": to_config(load_sim_params(db)),
        "equipment_templates": [
            {"id": t.id, "name": t.name, "category": t.category, "base_stats": t.base_stats or {}}
            for t in templates
        ],
        # 檔案內容而非 version 欄——見模組說明第 2 點。
        "mobility_matrix_sha256": hashlib.sha256(mobility.encode("utf-8")).hexdigest(),
    }


def compute_seal_hash(payload: dict[str, Any]) -> str:
    """快照 → 雜湊。`canonical_json` 讓鍵序與空白不影響結果。"""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def compress(payload: dict[str, Any]) -> bytes:
    return zstandard.ZstdCompressor().compress(canonical_json(payload).encode("utf-8"))


def decompress(blob: bytes) -> dict[str, Any]:
    return dict(json.loads(zstandard.ZstdDecompressor().decompress(bytes(blob)).decode("utf-8")))


def active_seal(db: Session) -> tuple[ParameterSeal, Exercise] | None:
    """目前生效中的簽證（若有）。回 (seal, exercise) 供錯誤訊息指名是哪一場演習。

    多場演習同時簽證時回**最早**的那一份：訊息裡指名誰都對，但要穩定
    （每次回不同的一場，操作員會以為狀況一直在變）。
    """
    rows = (
        db.execute(
            select(ParameterSeal, Exercise)
            .join(Exercise, Exercise.id == ParameterSeal.exercise_id)
            .where(Exercise.phase.in_(SEALED_PHASES))
            .order_by(ParameterSeal.sealed_at, ParameterSeal.id)
        )
        .tuples()
        .all()
    )
    return (rows[0][0], rows[0][1]) if rows else None


def seal_for(db: Session, exercise_id: str) -> ParameterSeal | None:
    return (
        db.execute(select(ParameterSeal).where(ParameterSeal.exercise_id == exercise_id))
        .scalars()
        .first()
    )


__all__ = [
    "SEALED_PHASES",
    "active_seal",
    "build_seal_payload",
    "compress",
    "compute_seal_hash",
    "decompress",
    "seal_for",
]
