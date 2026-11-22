from time import time

from backend import db
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

    invite = api.post(space_path + "/invitations", headers=owner, json={"email": "INVITADA@example.com"})
    assert invite.status_code == 201
    token = {"token": invite.json()["token"]}
    assert api.post("/api/invitations/accept", headers=outsider, json=token).status_code == 404
    guest = headers(register_and_login(api, "invitada@example.com"))
    assert api.get("/api/tasks" + scope, headers=guest).status_code == 404
    assert api.post("/api/invitations/accept", headers=guest, json=token).json() == {"workspace_id": workspace["id"]}
    assert api.post("/api/invitations/accept", headers=guest, json=token).status_code == 404
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


def test_replacing_an_invitation_invalidates_the_old_link_and_expired_links_cannot_join(api):
    owner = headers(register_and_login(api, "owner@example.com"))
    guest = headers(register_and_login(api, "guest@example.com"))
    space = api.post("/api/workspaces", headers=owner, json={"name": "Prueba"}).json()
    path = f"/api/workspaces/{space['id']}/invitations"
    target = {"email": "guest@example.com"}
    old = {"token": api.post(path, headers=owner, json=target).json()["token"]}
    new = {"token": api.post(path, headers=owner, json=target).json()["token"]}
    assert old != new
    assert api.post("/api/invitations/accept", headers=guest, json=old).status_code == 404
    with db.database() as connection:
        connection.execute("UPDATE workspace_invitations SET expires_at = ?", (int(time()) - 1,))
    assert api.post("/api/invitations/accept", headers=guest, json=new).status_code == 404
    assert api.get("/api/workspaces", headers=guest).json() == []
