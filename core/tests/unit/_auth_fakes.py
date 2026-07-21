"""Auth/Lobby API 測試共用鷹架（O4.1）——SQLite + settings 覆寫的 TestClient + 種子使用者。"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_db, get_settings
from app.auth.hashing import hash_password
from app.config import Settings
from app.main import app
from app.models import User, UserRole

TEST_SETTINGS = Settings(jwt_secret="test-secret-o41-at-least-32-bytes-long!")


def seed_user(
    factory: sessionmaker[Session],
    username: str = "cmdr",
    password: str = "pw123",
    role: UserRole = UserRole.COMMANDER,
) -> str:
    db = factory()
    user = User(username=username, password_hash=hash_password(password), role=role)
    db.add(user)
    db.commit()
    uid = user.id
    db.close()
    return uid


def make_client(factory: sessionmaker[Session]) -> TestClient:
    def _db() -> Iterator[Session]:
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_settings] = lambda: TEST_SETTINGS
    return TestClient(app)


def login(client: TestClient, username: str = "cmdr", password: str = "pw123") -> dict[str, str]:
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    r.raise_for_status()
    return r.json()


def auth_header(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}
