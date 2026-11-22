from time import time

from backend import db, mail
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

    changed = api.patch(task + scope, headers=guest, json={"board_status": "doing"}).json()
    assert changed["board_status"] == "doing" and not changed["completed"]
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
