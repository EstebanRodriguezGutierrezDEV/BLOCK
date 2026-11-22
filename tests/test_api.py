"""Pruebas de las operaciones CRUD y validaciones principales."""

import pytest
import sqlite3
from fastapi.testclient import TestClient
from werkzeug.security import generate_password_hash
from backend import db, main
from backend.request_security import MAX_JSON_BYTES, MAX_RECORD_ID
from conftest import PASSWORD, register_and_login


DATES = {"start_date": "2026-11-20", "end_date": "2026-11-25"}


@pytest.fixture
def client(api):
    token = register_and_login(api, "marta@example.com")
    api.headers["Authorization"] = f"Bearer {token}"
    return api


def test_api_bounds_requests_and_does_not_echo_rejected_values(client):
    oversized = b"x" * (MAX_JSON_BYTES + 1)
    json_headers = {"Content-Type": "application/json"}
    assert client.post("/api/tasks", content=oversized, headers=json_headers).status_code == 413
    chunks = iter([b"x" * MAX_JSON_BYTES, b"x"])
    streamed = client.post("/api/tasks", content=chunks, headers=json_headers)
    assert streamed.status_code == 413
    assert streamed.headers["X-Content-Type-Options"] == "nosniff"
    assert client.post("/api/tasks", content=b"{}", headers={"Content-Type": "text/plain"}).status_code == 415

    title = "contenido privado\x00"
    response = client.post("/api/tasks", json={"title": title, **DATES})
    assert response.status_code == 422
    assert "contenido privado" not in response.text
    assert '"input"' not in response.text
    assert client.get(f"/api/tasks/{MAX_RECORD_ID + 1}").status_code == 422
    assert client.get("/api/tasks", params={"workspace_id": MAX_RECORD_ID + 1}).status_code == 422
    tasks = client.get("/api/tasks")
    assert tasks.json() == []
    assert tasks.headers["Cache-Control"] == "no-store"


def test_crud(client):
    assert client.get("/api/tasks").json() == []

    created = client.post(
        "/api/tasks", json={"title": "  Estudiar Python  ", "description": "FastAPI y Flask", **DATES}
    )
    assert created.status_code == 201
    task = created.json()
    assert task["title"] == "Estudiar Python"
    assert task["completed"] is False
    assert task["start_date"] == DATES["start_date"]
    assert task["end_date"] == DATES["end_date"]

    task_id = task["id"]
    assert len(client.get("/api/tasks").json()) == 1
    assert client.get(f"/api/tasks/{task_id}").status_code == 200

    changed = client.patch(
        f"/api/tasks/{task_id}", json={"title": "Proyecto to-do", "completed": True}
    )
    assert changed.status_code == 200
    assert changed.json()["title"] == "Proyecto to-do"
    assert changed.json()["completed"] is True

    assert client.delete(f"/api/tasks/{task_id}").status_code == 204
    assert client.get("/api/tasks").json() == []
    assert client.get(f"/api/tasks/{task_id}").status_code == 404


def test_validation_and_missing_task(client):
    assert client.post("/api/tasks", json={"title": "   ", **DATES}).status_code == 422
    assert client.post("/api/tasks", json={"title": "A" * 201, **DATES}).status_code == 422
    assert client.patch("/api/tasks/123", json={}).status_code == 400
    assert client.patch("/api/tasks/123", json={"completed": True}).status_code == 404
    assert client.delete("/api/tasks/123").status_code == 404


def test_sql_injection_is_plain_text(client):
    special = "x'); DROP TABLE tasks; --"
    response = client.post("/api/tasks", json={"title": special, **DATES})
    assert response.status_code == 201
    assert client.get("/api/tasks").json()[0]["title"] == special


@pytest.mark.parametrize("dates", [
    {},
    {"start_date": "2026-11-20"},
    {"end_date": "2026-11-25"},
    {"start_date": None, "end_date": "2026-11-25"},
    {"start_date": "2026-11-25", "end_date": "2026-11-20"},
    {"start_date": "2026-02-29", "end_date": "2026-03-01"},
    {"start_date": "2026-11-20", "end_date": "invalid"},
])
def test_reject_invalid_dates(client, dates):
    response = client.post("/api/tasks", json={"title": "Plan", **dates})
    assert response.status_code == 422
    assert client.get("/api/tasks").json() == []


@pytest.mark.parametrize("start,end", [
    ("2026-11-20", "2026-11-20"),
    ("2028-02-29", "2028-03-01"),
    ("2026-12-29", "2027-01-06"),
])
def test_date_ranges_persist(client, start, end):
    created = client.post("/api/tasks", json={"title": "Plan", "start_date": start, "end_date": end})
    assert created.status_code == 201
    saved = client.get(f"/api/tasks/{created.json()['id']}").json()
    assert saved["start_date"] == start
    assert saved["end_date"] == end


def test_partial_date_updates_are_validated_against_saved_dates(client):
    task = client.post("/api/tasks", json={"title": "Plan", **DATES}).json()
    url = f"/api/tasks/{task['id']}"
    for changes in (
        {"start_date": "2026-11-26"},
        {"end_date": "2026-11-19"},
        {"title": "No guardar", "start_date": "2027-01-01"},
        {"start_date": None},
        {"end_date": None},
    ):
        assert client.patch(url, json=changes).status_code == 422
        assert client.get(url).json() == task

    changed = client.patch(url, json={"start_date": "2027-01-01", "end_date": "2027-01-10"})
    assert changed.status_code == 200
    assert changed.json()["start_date"] == "2027-01-01"
    assert changed.json()["end_date"] == "2027-01-10"
    assert client.patch(url, json={"completed": True}).json()["end_date"] == "2027-01-10"


def test_migration_preserves_existing_tasks_and_is_repeatable(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setenv("TODO_LEGACY_OWNER_EMAIL", "marta@example.com")
    with sqlite3.connect(path) as connection:
        connection.execute("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT,
                activation_hash TEXT UNIQUE,
                activation_sent_at INTEGER
            )
        """)
        connection.execute(
            "INSERT INTO users (email, password_hash) VALUES (?, ?)",
            ("marta@example.com", generate_password_hash(PASSWORD)),
        )
        connection.execute(
            "INSERT INTO users (id, email, activation_hash, activation_sent_at) VALUES (?, ?, ?, ?)",
            (42, "pendiente@example.com", "obsolete", 100),
        )
        connection.execute("""
            CREATE TABLE tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                completed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        connection.execute(
            "INSERT INTO tasks (title, description, completed, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            ("Mi tarea anterior", "Conservar detalles", 1, "2025-06-10T12:00:00Z", "2025-06-11T12:00:00Z"),
        )
    main.init_db()
    main.init_db()
    with TestClient(main.app) as client:
        other = register_and_login(client, "pendiente@example.com")
        other_profile = client.get("/api/auth/me", headers={"Authorization": f"Bearer {other}"}).json()
        assert other_profile["id"] == 42
        assert client.get("/api/tasks", headers={"Authorization": f"Bearer {other}"}).json() == []
        with db.database() as connection:
            pending = connection.execute(
                "SELECT activation_hash, email_verified FROM users WHERE id = 42"
            ).fetchone()
            assert tuple(pending) == (None, 0)
        login = client.post("/api/auth/login", json={"email": "marta@example.com", "password": PASSWORD})
        assert login.status_code == 200
        client.headers["Authorization"] = f"Bearer {login.json()['token']}"
        tasks = client.get("/api/tasks").json()
        assert len(tasks) == 1
        task = tasks[0]
        assert task["id"] == 1
        assert task["title"] == "Mi tarea anterior"
        assert task["description"] == "Conservar detalles"
        assert task["completed"] is True
        assert task["start_date"] == task["end_date"] == "2025-06-10"
        assert task["created_at"] == "2025-06-10T12:00:00Z"
        assert task["updated_at"] == "2025-06-11T12:00:00Z"
        assert client.patch("/api/tasks/1", json={"end_date": "2025-06-20"}).status_code == 200

        monkeypatch.setenv("TODO_LEGACY_OWNER_EMAIL", "pendiente@example.com")
        with db.database() as connection:
            connection.execute(
                "INSERT INTO tasks (title, start_date, end_date, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                ("Sin propietario", "2025-06-10", "2025-06-10", "2025-06-10", "2025-06-10"),
            )
        main.init_db()
        assert client.get("/api/tasks", headers={"Authorization": f"Bearer {other}"}).json() == []
