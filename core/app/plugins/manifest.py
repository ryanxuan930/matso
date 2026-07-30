"""插件契約版本協商（SPEC_FULL §16.3；contracts/proto/matso/plugin/v1/plugin_base.proto）。

補的洞（M11b）：`GetManifest` / `contract_version` / `capabilities` 在 core/app 是零命中——
Core 從來沒問過對面插件「你是誰、你講哪個版本的契約」。後果是換上 major 不相容的插件，
Core 照樣連上去照樣用，直到某個欄位讀出垃圾、或某支 RPC 回 UNIMPLEMENTED 才發現；那時
錯誤已經以「地形怪怪的」的形式進了推演結果與 AAR，沒人會回頭懷疑是插件版本。

本模組的紀律：**不相容就拋例外 + 記 error log，不做靜默降級**。呼叫端必須讓例外冒出去
（裝配失敗好過帶著錯的物理事實跑完一場推演）。

本模組是裝配期基礎設施（非模擬引擎）：純同步、無狀態，建好 channel 後立刻呼叫一次即可。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass

import grpc
from matso_sdk._generated import plugin_base_pb2, plugin_base_pb2_grpc

from app.errors import MatsoError

_LOG = logging.getLogger("app.plugins.manifest")

# 與健檢同級（2.0s）而非領域 RPC 的 0.2s：握手發生在插件剛起、可能還在載 DTED/圖資的時候，
# 用領域 deadline 會把「還在暖機」誤判成「不可達」而拒絕載入。
_DEFAULT_HANDSHAKE_DEADLINE_S = 2.0

CORE_CONTRACT_MAJOR = 0
"""Core 目前對得上的插件契約主版本（terrain / weather / comms 三個插件皆自報 0.1.0）。

⚠ 0.x 期間 semver 的破壞性變更其實落在 minor，但 plugin_base.proto 明文寫的是「major 不合
Orchestrator 拒絕載入」，這裡照契約實作。契約升到 1.0 之後這個判準才完全對得上。
"""

TERRAIN_REQUIRED_CAPABILITIES = ("GetElevation", "CheckLos", "GetPath", "GetCellBatch")
"""terrain 是物理預檢硬依賴，這四支缺一不可（缺了等於預檢無法裁決）。

刻意**不含** GetViewshed：視域目前還沒接進 Core 的呼叫端，為了一個還沒用到的能力就把整個
硬依賴插件擋在門外並不划算。要用視域的呼叫端請自己問 `manifest.supports("GetViewshed")`。
"""

# 嚴格 semver（允許 -pre / +build 後綴）。刻意不接受 "1.0" 這種兩段式：見 _parse_major。
_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")


class PluginHandshakeError(MatsoError):
    """插件握手失敗的基底：**Core 不得使用這個插件**。

    ⚠ contracts/core_api.yaml 的 Error.code 目前沒有 PLUGIN_* 代碼，故沿用 INTERNAL_ERROR。
    握手發生在裝配期而非請求期，正常情況不會走到 API 的錯誤轉換；若日後要把它上到 API，
    需要先在契約加一個 PLUGIN_INCOMPATIBLE 代碼（契約由主 agent 統一管）。
    """

    error_code = "INTERNAL_ERROR"
    http_status = 503


class PluginUnreachableError(PluginHandshakeError):
    """GetManifest 打不通（插件還沒起 / 網路不通）。可重試——與「版本不相容」語意不同。"""


class PluginContractMismatchError(PluginHandshakeError):
    """插件自報的身分 / 契約版本 / 能力與 Core 期望不符。重試無用，必須換插件或換 Core。"""


@dataclass(frozen=True, slots=True)
class PluginManifest:
    """插件自報的身分（plugin_base.proto 的 Manifest 於 Core 端的鏡像）。"""

    name: str
    kind: str
    contract_version: str
    capabilities: frozenset[str]

    def __post_init__(self) -> None:
        # 建構即驗證版本字串：讓「版本解析不出來」在握手當下就炸，而不是等到某個
        # 讀 major 的分支才炸（那時已經在用這個插件了）。
        _parse_major(self.contract_version)

    @property
    def major(self) -> int:
        return _parse_major(self.contract_version)

    def supports(self, capability: str) -> bool:
        """插件是否自報具備某能力（如 "GetViewshed"）。

        capabilities 是插件**自報**的清單、不是 proto 反射結果：只能當「沒報就別打」的提前
        攔截，不能當「有報就一定能用」的保證——RPC 端該有的 try/except 一樣不能省。
        """
        return capability in self.capabilities


def fetch_manifest(
    channel: grpc.Channel, *, deadline_s: float = _DEFAULT_HANDSHAKE_DEADLINE_S
) -> PluginManifest:
    """問插件要 manifest。打不通 / 回空 manifest / 版本字串不合法 → 拋例外（不回 None）。

    回 None 或回一個「預設值 manifest」都會讓呼叫端很容易寫成 `if m: check()`，於是插件
    有問題時反而跳過檢查——這正是本輪要修的 fail-open。
    """
    # protobuf 產生的 stub 工廠沒有型別（同 terrain_client 的處境；那邊是靠 pyproject 的
    # mypy overrides 放行）。本模組不在該清單內，改用逐行豁免——契約/設定檔這一輪不歸我改。
    stub = plugin_base_pb2_grpc.PluginBaseServiceStub(channel)  # type: ignore[no-untyped-call]
    try:
        resp = stub.GetManifest(plugin_base_pb2.GetManifestRequest(), timeout=deadline_s)
    except grpc.RpcError as exc:
        code = exc.code() if isinstance(exc, grpc.Call) else None
        raise PluginUnreachableError(f"插件 GetManifest 失敗（{code}）") from exc
    if not resp.HasField("manifest"):
        # proto3 讀未設定的 message 欄位會拿到全預設值的空 Manifest（name="" version=""），
        # 不特別攔就會變成「一個名叫空字串的插件」往下走。
        raise PluginContractMismatchError("插件 GetManifest 沒有回 manifest（欄位未設定）")
    m = resp.manifest
    return PluginManifest(
        name=str(m.name),
        kind=str(m.kind),
        contract_version=str(m.contract_version),
        capabilities=frozenset(str(c) for c in m.capabilities),
    )


def negotiate_contract(
    channel: grpc.Channel,
    *,
    expected_kind: str,
    expected_major: int = CORE_CONTRACT_MAJOR,
    required_capabilities: Iterable[str] = (),
    deadline_s: float = _DEFAULT_HANDSHAKE_DEADLINE_S,
) -> PluginManifest:
    """握手：GetManifest → 比對 kind / contract_version 的 major / 必要能力。

    比對 kind 而不是 name：name 是部署上的唯一識別（可能被換成 "terrain-gdal" 之類的替代
    實作），kind 才是「這是不是一個地形插件」的語意判準。

    不合就拋 PluginContractMismatchError 並記 error log。呼叫端**不要**吞掉這個例外去退回
    「先用著看看」——那正是這個模組要消滅的行為。
    """
    manifest = fetch_manifest(channel, deadline_s=deadline_s)

    if manifest.kind != expected_kind:
        _LOG.error(
            "插件種類不符：期望 %s，插件 %r 自報 %s → 拒絕載入",
            expected_kind,
            manifest.name,
            manifest.kind,
        )
        raise PluginContractMismatchError(
            f"插件種類不符：期望 {expected_kind}，實得 {manifest.kind}（name={manifest.name}）"
        )

    if manifest.major != expected_major:
        _LOG.error(
            "插件契約主版本不相容：Core 期望 major=%d，插件 %r 自報 %s → 拒絕載入",
            expected_major,
            manifest.name,
            manifest.contract_version,
        )
        raise PluginContractMismatchError(
            f"插件 {manifest.name} 契約版本 {manifest.contract_version} "
            f"與 Core 期望的 major={expected_major} 不相容"
        )

    missing = sorted(set(required_capabilities) - manifest.capabilities)
    if missing:
        _LOG.error(
            "插件 %r 缺少必要能力 %s（自報 %s）→ 拒絕載入",
            manifest.name,
            missing,
            sorted(manifest.capabilities),
        )
        raise PluginContractMismatchError(
            f"插件 {manifest.name} 缺少必要能力：{'、'.join(missing)}"
        )

    _LOG.info(
        "插件握手通過：name=%s kind=%s contract=%s capabilities=%s",
        manifest.name,
        manifest.kind,
        manifest.contract_version,
        sorted(manifest.capabilities),
    )
    return manifest


def _parse_major(contract_version: str) -> int:
    """取 semver 主版本；解析不出來就**拒絕**，不是當成 0。

    寬鬆解析（`contract_version.split(".")[0]`）碰到 "" 或 "unknown" 會炸或回垃圾，而若再
    寬鬆一點回退成 0，沒填版本的插件就正好通過目前 major=0 的相容性檢查——沒版本的插件
    反而最容易被載入。同理不接受 "1.0" 這種兩段式：契約寫明是 semver，格式鬆掉之後
    比對規則就沒有意義了。
    """
    matched = _SEMVER_RE.match(contract_version.strip())
    if matched is None:
        raise PluginContractMismatchError(
            f"插件 contract_version 不是合法 semver：{contract_version!r}"
        )
    return int(matched.group(1))
