"""密碼雜湊（O4.1，SPEC §12）——Argon2id。

離線自建帳號，不依賴外部 IdP。argon2-cffi 預設參數即 Argon2id。雜湊含鹽與參數，
verify 失敗以布林回報（不拋以區分帳號存在與否——列舉防護在 service 層）。
"""

from __future__ import annotations

import contextlib

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()

# 帳號不存在時仍跑一次等價的 Argon2 驗證，消除「存在才跑 hash」的計時側信道（CODE_REVIEW C4）。
# 於 import 時預算一個 dummy hash（一次性成本）。
_DUMMY_HASH = _hasher.hash("matso-constant-time-dummy-password")


def hash_password(password: str) -> str:
    """回傳 Argon2id 編碼字串（含演算法、參數、鹽、摘要）。"""
    return _hasher.hash(password)


def dummy_verify() -> None:
    """帳號不存在時呼叫：跑一次等價 Argon2 驗證吸收時間，使回應時間與帳號存在時相當。"""
    with contextlib.suppress(VerificationError, InvalidHashError):
        _hasher.verify(_DUMMY_HASH, "wrong-password")


def verify_password(password_hash: str, password: str) -> bool:
    """驗證明文密碼是否符合雜湊。不符 / 雜湊格式錯 → False（不拋）。"""
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):  # 不符 / 雜湊格式錯 → 不符
        return False


def needs_rehash(password_hash: str) -> bool:
    """雜湊參數是否落後於目前預設（登入成功時可順手升級）。"""
    return _hasher.check_needs_rehash(password_hash)
