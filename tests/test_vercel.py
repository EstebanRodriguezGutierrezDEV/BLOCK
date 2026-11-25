from importlib import import_module
import os
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
import pytest

from backend import accounts, rate_limits
from backend.attachment_policy import MAX_ATTACHMENT_BYTES
from conftest import PASSWORD, TEST_IMAGE, registration_data
from frontend.app import app as web
from frontend import embedded_api
from frontend.deployment import configure_deployment


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
    monkeypatch.setitem(web.config, "TRUSTED_HOSTS", None)
    monkeypatch.setattr(embedded_api, "_initialized", False)
    monkeypatch.delenv("TODO_PUBLIC_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    app = import_module("app").app
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("VERCEL_PROJECT_PRODUCTION_URL", "tareas.example.com")
    configure_deployment(app)
    base_url = "http://tareas.example.com"
    headers = {"X-Forwarded-Proto": "https", "Origin": "https://tareas.example.com"}
    monkeypatch.setattr(rate_limits, "time", lambda: 1200)
    owner = app.test_client()
    guest = app.test_client()
    credentials = registration_data("propietario@example.com")
    task = {"title": "Plan de producción", "start_date": "2026-11-20", "end_date": "2026-11-25"}

    assert owner.get("/", base_url=base_url).status_code == 200
    assert owner.get("/", base_url=base_url, headers=headers).headers["Strict-Transport-Security"] == "max-age=31536000"
    assert owner.get("/favicon.ico", base_url=base_url).status_code == 200
    assert owner.post("/registro", base_url=base_url, headers=headers, data=credentials).status_code == 503
    test_database = make_conninfo(test_url, options=f"-c search_path={namespace}")
    monkeypatch.setenv("DATABASE_URL", test_database)

    for client, email in ((owner, credentials["email"]), (guest, "invitado@example.com")):
        account = {**credentials, "email": email}
        assert client.post("/registro", base_url=base_url, headers=headers, data=account).status_code == 303
        login = client.post("/iniciar-sesion", base_url=base_url, headers=headers, data=account)
        assert login.location == "/tareas"
        assert "Secure" in login.headers["Set-Cookie"]
        assert "HttpOnly" in login.headers["Set-Cookie"]

    space = owner.post("/api/workspaces", base_url=base_url, headers=headers, json={"name": "Equipo"})
    assert space.status_code == 201
    space_path = f"/api/workspaces/{space.json['id']}"
    scope = f"?workspace_id={space.json['id']}"
    card = owner.post("/api/tasks" + scope, base_url=base_url, headers=headers, json=task)
    assert card.status_code == 201
    with psycopg.connect(test_database) as legacy_schema:
        legacy_schema.execute("ALTER TABLE tasks DROP COLUMN board_position")
        legacy_schema.execute("ALTER TABLE workspace_members DROP COLUMN can_change_status")
        legacy_schema.execute("DROP TABLE task_attachments")
    monkeypatch.setattr(embedded_api, "_initialized", False)
    upgraded = owner.get("/api/tasks" + scope, base_url=base_url, headers=headers)
    assert upgraded.json == [{**card.json, "board_position": 0}]
    calendar = "/api/calendar/tasks"
    assert guest.get(calendar, base_url=base_url, headers=headers).json["tasks"] == []
    comments = f"/api/tasks/{card.json['id']}/comments" + scope
    assert guest.get(comments, base_url=base_url, headers=headers).status_code == 404
    invite = owner.post(
        space_path + "/invitations", base_url=base_url, headers=headers, json={"email": "invitado@example.com"},
    )
    assert invite.status_code == 201
    inbox = guest.get("/api/messages", base_url=base_url, headers=headers)
    message_id = inbox.json[0]["id"]
    accepted = guest.post(f"/api/messages/{message_id}/accept", base_url=base_url, headers=headers, json={})
    assert accepted.status_code == 200
    guest_calendar = guest.get(calendar, base_url=base_url, headers=headers)
    assert guest_calendar.json == {
        "tasks": [{**upgraded.json[0], "workspace_id": space.json["id"], "workspace_name": "Equipo"}],
        "next_before_id": None,
    }
    assert guest.post(comments, base_url=base_url, headers=headers, json={"body": "Revisado"}).status_code == 201
    assert owner.get(comments, base_url=base_url, headers=headers).json[0]["body"] == "Revisado"

    attachments = f"/api/tasks/{card.json['id']}/attachments"
    upload = attachments + scope + "&file_name=Notas%20de%20revisi%C3%B3n.xyz"
    binary_headers = {**headers, "Content-Type": "application/octet-stream"}
    payload = bytes(range(256)) * (MAX_ATTACHMENT_BYTES // 256)
    assert guest.post(upload, base_url=base_url, headers=binary_headers, data=payload + b"X").status_code == 413
    stored = guest.post(upload, base_url=base_url, headers=binary_headers, data=payload)
    assert stored.status_code == 201
    assert owner.get(attachments + scope, base_url=base_url, headers=headers).json == [stored.json]
    download_path = f"{attachments}/{stored.json['id']}" + scope
    download = owner.get(download_path, base_url=base_url, headers=headers)
    assert download.data == payload
    assert download.headers["Content-Type"] == "application/octet-stream"
    assert download.headers["Content-Disposition"].endswith("Notas%20de%20revisi%C3%B3n.xyz")
    assert download.headers["X-Content-Type-Options"] == "nosniff"
    assert download.headers["Cache-Control"] == "no-store"
    assert app.test_client().get(download_path, base_url=base_url, headers=headers).status_code == 401

    position = f"/api/tasks/{card.json['id']}/position" + scope
    move = {"board_status": "done"}
    assert guest.patch(position, base_url=base_url, headers=headers, json=move).status_code == 403
    members = owner.get(space_path, base_url=base_url, headers=headers).json["members"]
    member_id = next(member["id"] for member in members if member["email"] == "invitado@example.com")
    permission_path = f"/api/tasks/{card.json['id']}/permissions/{member_id}" + scope
    permission = {"can_change_status": True}
    assert owner.patch(permission_path, base_url=base_url, headers=headers, json=permission).json == permission
    assert guest.patch(position, base_url=base_url, headers=headers, json=move).json[0]["completed"] is True
    assert owner.get("/api/tasks" + scope, base_url=base_url, headers=headers).json[0]["board_status"] == "done"

    friend_request = owner.post("/api/friends", base_url=base_url, headers=headers, json={"email": "invitado@example.com"})
    assert friend_request.status_code == 201
    friendship = friend_request.json["friendship_id"]
    chat = f"/api/chats/{friendship}"
    assert guest.get(chat, base_url=base_url, headers=headers).status_code == 404
    assert guest.post(f"/api/friends/{friendship}/accept", base_url=base_url, headers=headers, json={}).status_code == 204
    sent = guest.post(chat, base_url=base_url, headers=headers, json={"body": "Hola desde el tablero"})
    assert sent.status_code == 201
    assert owner.get(chat + "?after_id=0", base_url=base_url, headers=headers).json["messages"] == [sent.json]
    unread = "/api/chats/unread"
    assert owner.get(unread, base_url=base_url, headers=headers).json == {
        "total": 1, "chats": [{"friendship_id": friendship, "unread_count": 1}],
    }
    assert owner.post(
        chat + "/read", base_url=base_url, headers=headers, json={"message_id": sent.json["id"]},
    ).status_code == 204
    assert owner.get(unread, base_url=base_url, headers=headers).json == {"total": 0, "chats": []}

    shared_file = guest.post(
        chat + "/attachments?file_name=Imagen.png", base_url=base_url, headers=binary_headers, data=TEST_IMAGE,
    )
    assert shared_file.status_code == 201
    file_path = f"{chat}/attachments/{shared_file.json['id']}"
    image_preview = owner.get(file_path + "/preview", base_url=base_url, headers=headers)
    assert image_preview.data == TEST_IMAGE and image_preview.headers["Content-Type"] == "image/png"
    assert image_preview.headers["Cross-Origin-Resource-Policy"] == "same-origin"
    assert image_preview.headers["Cache-Control"] == "no-store"
    downloaded_file = owner.get(file_path, base_url=base_url, headers=headers)
    assert downloaded_file.data == TEST_IMAGE and downloaded_file.headers["Content-Type"] == "application/octet-stream"

    assert owner.post("/api/tasks", base_url=base_url, headers={}, json=task).status_code == 403
    created = owner.post("/api/tasks", base_url=base_url, headers=headers, json=task)
    assert created.status_code == 201
    path = f"/api/tasks/{created.json['id']}"
    assert guest.get(path, base_url=base_url, headers=headers).status_code == 404
    assert owner.patch(path, base_url=base_url, headers=headers, json={"end_date": "2026-11-19"}).status_code == 422
    update = owner.patch(path, base_url=base_url, headers=headers, json={"completed": True})
    assert update.status_code == 200 and update.json["completed"] is True

    monkeypatch.setattr(embedded_api, "_initialized", False)
    assert owner.get("/api/tasks", base_url=base_url, headers=headers).json == [update.json]
    assert guest.delete(path, base_url=base_url, headers=headers).status_code == 404
    assert owner.delete(path, base_url=base_url, headers=headers).status_code == 204
    guest_view = guest.get_cookie("todo_session_view", domain="tareas.example.com").value
    account_password = "Contraseña de integración nueva 8"
    changed_password = guest.post("/cuenta/contrasena", base_url=base_url, headers=headers, data={
        "session_view": guest_view, "current_password": PASSWORD,
        "new_password": account_password, "confirm_password": account_password,
    })
    assert changed_password.location == "/cuenta?contrasena=guardada"
    removed = guest.post("/cuenta/eliminar", base_url=base_url, headers=headers, data={
        "session_view": guest_view, "current_password": account_password,
    })
    assert removed.location == "/iniciar-sesion?cuenta_eliminada=1"
    assert guest.get("/cuenta", base_url=base_url, headers=headers).location == "/iniciar-sesion?volver=cuenta"
    assert owner.get(chat, base_url=base_url, headers=headers).status_code == 404
    assert owner.get("/api/tasks" + scope, base_url=base_url, headers=headers).status_code == 200
    assert owner.post("/cerrar-sesion", base_url=base_url, headers=headers).status_code == 303
    assert owner.get("/api/tasks", base_url=base_url, headers=headers).status_code == 401

    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setattr(accounts, "LOGIN_IP", accounts.LOGIN_IP._replace(attempts=1))
    wrong_password = {**credentials, "password": "Incorrecta"}
    trusted = {**headers, "X-Vercel-Forwarded-For": "192.0.2.10"}
    assert owner.post("/iniciar-sesion", base_url=base_url, headers=trusted, data=wrong_password).status_code == 401
    changed_forwarding = {**trusted, "X-Forwarded-For": "192.0.2.20"}
    blocked = owner.post("/iniciar-sesion", base_url=base_url, headers=changed_forwarding, data=wrong_password)
    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"] == "300"
    another_ip = {**trusted, "X-Vercel-Forwarded-For": "192.0.2.30"}
    assert owner.post("/iniciar-sesion", base_url=base_url, headers=another_ip, data=wrong_password).status_code == 401
