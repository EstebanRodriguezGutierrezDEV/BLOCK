import pytest
from fastapi.testclient import TestClient

from backend import db, main

PASSWORD = "Una contraseña de prueba 8"


@pytest.fixture(autouse=True)
def isolate_external_services(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("TODO_SMTP_HOST", raising=False)


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.delenv("TODO_LEGACY_OWNER_EMAIL", raising=False)
    with TestClient(main.app) as client:
        yield client


def register_and_login(api, email):
    credentials = {"email": email, "password": PASSWORD}
    assert api.post("/api/auth/register", json=credentials).status_code == 201
    login = api.post("/api/auth/login", json=credentials)
    assert login.status_code == 200
    return login.json()["token"]
