"""Verifica el entrypoint de producción con PostgreSQL en un esquema temporal."""

from importlib import import_module
import os
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
import pytest

from conftest import PASSWORD
from frontend.app import app as web
from frontend import embedded_api


def test_vercel_registration_sessions_and_task_storage(monkeypatch):
    test_url = os.environ.get("TODO_TEST_POSTGRES_URL")
    if not test_url:
        pytest.skip("Configura TODO_TEST_POSTGRES_URL para ejecutar la prueba de producción")

    namespace = f"todo_test_{uuid4().hex}"
    identifier = sql.Identifier(namespace)
    with psycopg.connect(test_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(identifier))
        try:
            exercise_deployment(monkeypatch, test_url, namespace)
        finally:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(identifier))


def exercise_deployment(monkeypatch, test_url, namespace):
    monkeypatch.setattr(web, "wsgi_app", web.wsgi_app)
    monkeypatch.setitem(web.config, "TODO_EMBEDDED_API", False)
    monkeypatch.setattr(embedded_api, "_initialized", False)
    monkeypatch.delenv("TODO_PUBLIC_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    app = import_module("app").app
    base_url = "http://tareas.example.com"
    headers = {"X-Forwarded-Proto": "https", "Origin": "https://tareas.example.com"}
    owner = app.test_client()
    guest = app.test_client()
    credentials = {"email": "propietario@example.com", "password": PASSWORD}
    task = {"title": "Plan de producción", "start_date": "2026-11-20", "end_date": "2026-11-25"}

    assert owner.get("/", base_url=base_url).status_code == 200
    assert owner.get("/favicon.ico", base_url=base_url).status_code == 200
    assert owner.post("/registro", base_url=base_url, headers=headers, data=credentials).status_code == 503
    monkeypatch.setenv("DATABASE_URL", make_conninfo(test_url, options=f"-c search_path={namespace}"))

    for client, email in ((owner, credentials["email"]), (guest, "invitado@example.com")):
        account = {**credentials, "email": email}
        assert client.post("/registro", base_url=base_url, headers=headers, data=account).status_code == 303
        login = client.post("/iniciar-sesion", base_url=base_url, headers=headers, data=account)
        assert login.location == "/tareas"
        assert "Secure" in login.headers["Set-Cookie"]
        assert "HttpOnly" in login.headers["Set-Cookie"]

    assert owner.post("/api/tasks", base_url=base_url, headers={}, json=task).status_code == 403
    created = owner.post("/api/tasks", base_url=base_url, headers=headers, json=task)
    assert created.status_code == 201
    path = f"/api/tasks/{created.json['id']}"
    assert guest.get(path, base_url=base_url, headers=headers).status_code == 404
    assert owner.patch(path, base_url=base_url, headers=headers, json={"end_date": "2026-11-19"}).status_code == 422
    update = owner.patch(path, base_url=base_url, headers=headers, json={"completed": True})
    assert update.status_code == 200 and update.json["completed"] is True

    # Simula un arranque nuevo que vuelve a abrir la misma base.
    monkeypatch.setattr(embedded_api, "_initialized", False)
    assert owner.get("/api/tasks", base_url=base_url, headers=headers).json == [update.json]
    assert guest.delete(path, base_url=base_url, headers=headers).status_code == 404
    assert owner.delete(path, base_url=base_url, headers=headers).status_code == 204
    assert owner.post("/cerrar-sesion", base_url=base_url, headers=headers).status_code == 303
    assert owner.get("/api/tasks", base_url=base_url, headers=headers).status_code == 401
