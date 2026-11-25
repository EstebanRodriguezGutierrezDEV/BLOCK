from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import db, main
from backend.privacy import current_privacy_notice

PASSWORD = "Una contraseña de prueba 8"
TEST_IMAGE = (Path(__file__).resolve().parents[1] / "public/static/icons/block-192.png").read_bytes()


@pytest.fixture(autouse=True)
def isolate_external_services(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("VERCEL_ENV", raising=False)


@pytest.fixture(autouse=True)
def configure_privacy_notice(monkeypatch):
    monkeypatch.setenv("TODO_PRIVACY_CONTROLLER", "Responsable de prueba")
    monkeypatch.setenv("TODO_PRIVACY_CONTACT_EMAIL", "privacidad@example.com")
    monkeypatch.setenv("TODO_PRIVACY_INFRASTRUCTURE", "Alojamiento y base de datos de pruebas.")


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.delenv("TODO_LEGACY_OWNER_EMAIL", raising=False)
    with TestClient(main.app) as client:
        yield client


def registration_data(email):
    return {"email": email, "password": PASSWORD, "privacy_acknowledgement": current_privacy_notice().version}


def register_and_login(api, email):
    assert api.post("/api/auth/register", json=registration_data(email)).status_code == 201
    login = api.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    return login.json()["token"]
