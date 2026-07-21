"""密碼雜湊（O4.1）——Argon2id roundtrip / 錯誤密碼 / 格式錯不拋。"""

from __future__ import annotations

from app.auth.hashing import hash_password, needs_rehash, verify_password


def test_hash_verify_roundtrip() -> None:
    h = hash_password("s3cret-pw")
    assert verify_password(h, "s3cret-pw") is True


def test_wrong_password_rejected() -> None:
    h = hash_password("s3cret-pw")
    assert verify_password(h, "wrong") is False


def test_hash_is_salted_unique() -> None:
    # 同密碼兩次雜湊不同（含隨機鹽），但都可驗證
    a, b = hash_password("pw"), hash_password("pw")
    assert a != b
    assert verify_password(a, "pw") and verify_password(b, "pw")


def test_is_argon2id() -> None:
    assert hash_password("pw").startswith("$argon2id$")


def test_malformed_hash_returns_false_not_raises() -> None:
    assert verify_password("not-a-hash", "pw") is False


def test_needs_rehash_false_for_current_params() -> None:
    assert needs_rehash(hash_password("pw")) is False
