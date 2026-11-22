from time import time

from backend import db, mail, main
from backend.attachment_policy import MAX_ATTACHMENT_BYTES
from conftest import register_and_login


def headers(token):
    return {"Authorization": f"Bearer {token}"}


def task_data(title):
    return {"title": title, "start_date": "2026-11-22", "end_date": "2026-11-24"}


def test_shared_tasks_and_comments_are_visible_only_to_members(api):
    owner = headers(register_and_login(api, "owner@example.com"))
    outsider = headers(register_and_login(api, "outsider@example.com"))
    assert api.get("/api/workspaces").status_code == 401
    created = api.post("/api/workspaces", headers=owner, json={"name": " Proyecto "})
    assert created.status_code == 201
    workspace = created.json()
    assert workspace["name"] == "Proyecto"
    space_path = "/api/workspaces/" + str(workspace["id"])
    scope = f"?workspace_id={workspace['id']}"

    assert api.post("/api/tasks", headers=owner, json=task_data("Personal")).status_code == 201
    shared = api.post("/api/tasks" + scope, headers=owner, json=task_data("Compartida")).json()
    task = f"/api/tasks/{shared['id']}"
    assert api.get(task, headers=owner).status_code == 404
    assert [row["title"] for row in api.get("/api/tasks", headers=owner).json()] == ["Personal"]
    assert api.get(space_path, headers=outsider).status_code == 404
    for method, path, body in [
        ("GET", task + scope, None), ("PATCH", task + scope, {"board_status": "done"}),
        ("DELETE", task + scope, None), ("GET", task + "/comments" + scope, None),
        ("POST", task + "/comments" + scope, {"body": "Sin permiso"}),
        ("POST", "/api/tasks" + scope, task_data("Sin permiso")),
    ]:
        assert api.request(method, path, headers=outsider, json=body).status_code == 404

    invitation_email = {"email": "INVITADA@example.com"}
    assert api.post(space_path + "/invitations", headers=owner, json=invitation_email).status_code == 404
    guest = headers(register_and_login(api, "invitada@example.com"))
    assert api.get("/api/messages", headers=guest).json() == []
    invite = api.post(space_path + "/invitations", headers=owner, json=invitation_email)
    assert invite.status_code == 201
    assert invite.json() == {"email_delivery": "not_configured"}
    messages = api.get("/api/messages", headers=guest).json()
    assert len(messages) == 1
    assert messages[0]["workspace_name"] == "Proyecto"
    assert messages[0]["sender_email"] == "owner@example.com"
    action = f"/api/messages/{messages[0]['id']}/accept"
    assert api.get("/api/messages").status_code == 401
    assert api.get("/api/messages", headers=outsider).json() == []
    assert api.post(action, headers=outsider).status_code == 404
    assert api.get("/api/tasks" + scope, headers=guest).status_code == 404
    assert api.post(action, headers=guest).json() == {"workspace_id": workspace["id"]}
    assert api.post(action, headers=guest).status_code == 404
    assert api.get("/api/workspaces", headers=guest).json() == [workspace]
    assert api.post(space_path + "/invitations", headers=guest, json={"email": "otra@example.com"}).status_code == 403

    guest_id = api.get("/api/auth/me", headers=guest).json()["id"]
    grant = f"{space_path}/members/{guest_id}"
    status_permission = {"can_change_status": True}
    assert api.get(space_path, headers=guest).json()["can_change_status"] is False
    assert api.patch(grant, headers=outsider, json=status_permission).status_code == 404
    assert api.patch(grant, headers=guest, json=status_permission).status_code == 403
    for data in ({"board_status": "doing"}, {"completed": True}):
        assert api.patch(task + scope, headers=guest, json=data).status_code == 403
    assert api.patch(
        task + "/position" + scope, headers=guest, json={"board_status": "done"},
    ).status_code == 403
    assert api.post(
        "/api/tasks" + scope, headers=guest, json={**task_data("Bloqueada"), "board_status": "doing"},
    ).status_code == 403
    assert api.patch(task + scope, headers=guest, json={"description": "Revisar"}).status_code == 200

    second = api.post("/api/tasks" + scope, headers=guest, json=task_data("Segunda")).json()
    second_position = f"/api/tasks/{second['id']}/position" + scope
    reordered = api.patch(
        second_position, headers=guest, json={"board_status": "todo", "before_task_id": shared["id"]},
    )
    assert [row["id"] for row in reordered.json()] == [second["id"], shared["id"]]
    assert api.get("/api/tasks" + scope, headers=owner).json() == reordered.json()
    assert api.patch(second_position, headers=outsider, json={"board_status": "todo"}).status_code == 404
    assert api.patch(grant, headers=owner, json=status_permission).json() == status_permission
    assert api.get(space_path, headers=guest).json()["can_change_status"] is True
    assert api.patch(
        second_position, headers=guest, json={"board_status": "doing", "before_task_id": shared["id"]},
    ).status_code == 409

    changed = api.patch(task + scope, headers=guest, json={"board_status": "doing"}).json()
    assert changed["board_status"] == "doing" and not changed["completed"]
    cross_column = api.patch(
        second_position, headers=guest, json={"board_status": "doing", "before_task_id": shared["id"]},
    )
    assert [(row["id"], row["board_status"]) for row in cross_column.json()] == [
        (second["id"], "doing"), (shared["id"], "doing"),
    ]
    assert api.patch(grant, headers=owner, json={"can_change_status": False}).status_code == 200
    assert api.patch(second_position, headers=guest, json={"board_status": "done"}).status_code == 403
    assert api.patch(task + scope, headers=owner, json={"board_status": "done"}).json()["completed"]
    comment_path = task + "/comments" + scope
    assert api.post(comment_path, headers=guest, json={"body": "  "}).status_code == 422
    comment = api.post(comment_path, headers=guest, json={"body": " Lista para revisar "})
    assert comment.status_code == 201
    assert comment.json()["author_email"] == "invitada@example.com"
    assert comment.json()["body"] == "Lista para revisar"
    assert api.get(comment_path, headers=owner).json() == [comment.json()]
    assert api.get(task + "/comments", headers=owner).status_code == 422
    assert api.delete(task + scope, headers=guest).status_code == 204
    assert api.get(comment_path, headers=owner).status_code == 404


def test_invitation_resends_rejection_and_expiry(api):
    owner = headers(register_and_login(api, "owner@example.com"))
    guest = headers(register_and_login(api, "guest@example.com"))
    space = api.post("/api/workspaces", headers=owner, json={"name": "Prueba"}).json()
    path = f"/api/workspaces/{space['id']}/invitations"
    target = {"email": "guest@example.com"}
    assert api.post(path, headers=owner, json=target).status_code == 201
    old = api.get("/api/messages", headers=guest).json()[0]["id"]
    assert api.post(path, headers=owner, json=target).status_code == 409
    with db.database() as connection:
        connection.execute("UPDATE workspace_invitations SET expires_at = expires_at - 61")
    assert api.post(path, headers=owner, json=target).status_code == 201
    new = api.get("/api/messages", headers=guest).json()[0]["id"]
    assert old != new
    assert api.post(f"/api/messages/{old}/accept", headers=guest).status_code == 404
    assert api.post(f"/api/messages/{new}/reject", headers=guest).json() == {"rejected": True}
    assert api.get("/api/messages", headers=guest).json() == []
    assert api.post(path, headers=owner, json=target).status_code == 201
    expiring = api.get("/api/messages", headers=guest).json()[0]["id"]
    with db.database() as connection:
        connection.execute("UPDATE workspace_invitations SET expires_at = ?", (int(time()) - 1,))
    assert api.post(f"/api/messages/{expiring}/accept", headers=guest).status_code == 404
    assert api.get("/api/workspaces", headers=guest).json() == []


def test_calendar_only_includes_personal_tasks_and_accepted_workspaces(api, monkeypatch):
    monkeypatch.setattr(main, "CALENDAR_PAGE_SIZE", 2)
    owner = headers(register_and_login(api, "owner@example.com"))
    guest = headers(register_and_login(api, "guest@example.com"))
    calendar = "/api/calendar/tasks"
    assert api.get(calendar).status_code == 401

    personal = api.post("/api/tasks", headers=guest, json=task_data("Personal invitado")).json()
    api.post("/api/tasks", headers=owner, json=task_data("Personal propietario"))
    allowed = []
    for name in ("Equipo A", "Equipo B", "Espacio privado"):
        space = api.post("/api/workspaces", headers=owner, json={"name": name}).json()
        card = api.post(
            "/api/tasks", headers=owner, params={"workspace_id": space["id"]}, json=task_data(name),
        ).json()
        if name != "Espacio privado":
            allowed.append({**card, "workspace_id": space["id"], "workspace_name": name})
            invite = api.post(
                f"/api/workspaces/{space['id']}/invitations", headers=owner, json={"email": "guest@example.com"},
            )
            assert invite.status_code == 201

    personal_entry = {**personal, "workspace_id": None, "workspace_name": None}
    assert api.get(calendar, headers=guest).json() == {"tasks": [personal_entry], "next_before_id": None}
    for invite in api.get("/api/messages", headers=guest).json():
        assert api.post(f"/api/messages/{invite['id']}/accept", headers=guest).status_code == 200
    first_page = api.get(calendar, headers=guest).json()
    assert first_page == {"tasks": allowed[::-1], "next_before_id": str(allowed[0]["id"])}
    second_page = api.get(calendar, headers=guest, params={"before_id": first_page["next_before_id"]}).json()
    assert second_page == {"tasks": [personal_entry], "next_before_id": None}
    assert api.get("/api/tasks", headers=guest).json() == [personal]


def test_shared_attachments_are_binary_and_scoped_to_members_and_tasks(api):
    owner = headers(register_and_login(api, "owner@example.com"))
    guest = headers(register_and_login(api, "guest@example.com"))
    outsider = headers(register_and_login(api, "outsider@example.com"))
    space = api.post("/api/workspaces", headers=owner, json={"name": "Proyecto"}).json()["id"]
    scope = {"workspace_id": space}
    task = api.post("/api/tasks", params=scope, headers=owner, json=task_data("Compartida")).json()["id"]
    second = api.post("/api/tasks", params=scope, headers=owner, json=task_data("Segunda")).json()["id"]
    personal = api.post("/api/tasks", headers=owner, json=task_data("Personal")).json()["id"]
    attachments = f"/api/tasks/{task}/attachments"
    sender = {**guest, "Content-Type": "application/octet-stream"}
    file_name = "Guía + otoño.xyz"
    payload = b"<h1>Archivo de prueba</h1>\x00\xff"

    invitation = api.post(
        f"/api/workspaces/{space}/invitations", headers=owner, json={"email": "guest@example.com"},
    )
    assert invitation.status_code == 201
    assert api.get(attachments, params=scope, headers=guest).status_code == 404
    invite_id = api.get("/api/messages", headers=guest).json()[0]["id"]
    assert api.post(f"/api/messages/{invite_id}/accept", headers=guest).status_code == 200

    params = {**scope, "file_name": file_name}
    assert api.post(attachments, headers=sender, params=params, content=b"x" * (MAX_ATTACHMENT_BYTES + 1)).status_code == 413
    assert api.post(
        attachments, headers=sender, params={**scope, "file_name": "../archivo"}, content=payload,
    ).status_code == 422
    assert api.post(attachments, headers=outsider | {"Content-Type": "application/octet-stream"},
                    params=params, content=payload).status_code == 404
    assert api.get(f"/api/tasks/{personal}/attachments", params=scope, headers=owner).status_code == 404
    assert api.get(attachments, params=scope, headers=owner).json() == []

    uploaded = api.post(attachments, headers=sender, params=params, content=payload)
    assert uploaded.status_code == 201
    attachment = uploaded.json()
    assert attachment["file_name"] == file_name
    assert attachment["size_bytes"] == len(payload)
    assert attachment["uploader_email"] == "guest@example.com"
    assert "content" not in attachment
    assert api.get(attachments, params=scope, headers=owner).json() == [attachment]
    assert api.get(attachments, params=scope, headers=outsider).status_code == 404
    download_path = f"{attachments}/{attachment['id']}"
    assert api.get(download_path, params=scope).status_code == 401
    assert api.get(download_path, params=scope, headers=outsider).status_code == 404
    assert api.get(f"/api/tasks/{second}/attachments/{attachment['id']}", params=scope, headers=owner).status_code == 404

    download = api.get(download_path, params=scope, headers=owner)
    assert download.content == payload
    assert download.headers["Content-Type"] == "application/octet-stream"
    assert download.headers["Content-Disposition"] == "attachment; filename*=UTF-8''Gu%C3%ADa%20%2B%20oto%C3%B1o.xyz"
    assert download.headers["X-Content-Type-Options"] == "nosniff"
    assert download.headers["Cache-Control"] == "no-store"

    assert api.post("/api/auth/logout", headers=guest).status_code == 204
    assert api.get(download_path, params=scope, headers=guest).status_code == 401
    assert api.delete(f"/api/tasks/{task}", params=scope, headers=owner).status_code == 204
    with db.database() as connection:
        assert connection.execute("SELECT id FROM task_attachments WHERE id = ?", (attachment["id"],)).fetchone() is None


def test_mail_notice_uses_the_registered_recipient_and_keeps_the_invitation_on_failure(api, monkeypatch):
    owner = headers(register_and_login(api, "owner@example.com"))
    guest = headers(register_and_login(api, "guest@example.com"))
    monkeypatch.setenv("TODO_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("TODO_SMTP_FROM", "block@example.com")
    monkeypatch.setenv("TODO_SMTP_SECURITY", "starttls")
    monkeypatch.setenv("TODO_SMTP_PORT", "587")
    monkeypatch.setenv("TODO_SMTP_USER", "block@example.com")
    monkeypatch.setenv("TODO_SMTP_PASSWORD", "test-smtp-password")
    monkeypatch.setenv("TODO_PUBLIC_URL", "https://block.example.com")
    sent = []

    class SMTP:
        def __init__(self, host, port, timeout):
            assert (host, port, timeout) == ("smtp.example.com", 587, 5)
            self.secure = False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def starttls(self, *, context):
            self.secure = True

        def login(self, user, password):
            assert self.secure
            assert user == "block@example.com"
            assert password == "test-smtp-password"

        def send_message(self, message):
            sent.append(message)

    monkeypatch.setattr(mail.smtplib, "SMTP", SMTP)
    space = api.post("/api/workspaces", headers=owner, json={"name": "Proyecto"}).json()
    route = f"/api/workspaces/{space['id']}/invitations"
    assert api.post(route, headers=owner, json={"email": "sin-cuenta@example.com"}).status_code == 404
    assert not sent
    result = api.post(route, headers=owner, json={"email": "guest@example.com"})
    assert result.status_code == 201 and result.json()["email_delivery"] == "sent"
    message = sent[0]
    assert message["To"] == "guest@example.com"
    assert "https://block.example.com/mensajes" in message.get_content()
    assert "owner@example.com" in message.get_content()
    invitation = api.get("/api/messages", headers=guest).json()[0]
    assert invitation["id"] not in message.get_content()
    assert api.post(f"/api/messages/{invitation['id']}/reject", headers=guest).status_code == 200

    def smtp_unavailable(*args, **kwargs):
        raise TimeoutError()

    monkeypatch.setattr(mail.smtplib, "SMTP", smtp_unavailable)
    result = api.post(route, headers=owner, json={"email": "guest@example.com"})
    assert result.json() == {"email_delivery": "failed"}
    assert len(api.get("/api/messages", headers=guest).json()) == 1
