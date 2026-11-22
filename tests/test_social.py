from backend import db
from backend.attachment_policy import MAX_ATTACHMENT_BYTES
from conftest import TEST_IMAGE, register_and_login


def account(api, email):
    return {"Authorization": f"Bearer {register_and_login(api, email)}"}


def test_chat_requires_an_accepted_friendship_and_an_active_session(api):
    sender = account(api, "ana@example.com")
    recipient = account(api, "eva@example.com")
    outsider = account(api, "luz@example.com")
    friends_path = "/api/friends"

    assert api.get(friends_path).status_code == 401
    assert api.post(friends_path, headers=sender, json={"email": "nueva@example.com"}).status_code == 404
    assert api.post(friends_path, headers=sender, json={"email": "ana@example.com"}).status_code == 422
    sent = api.post(friends_path, headers=sender, json={"email": "EVA@example.com"})
    assert sent.status_code == 201
    friendship_id = sent.json()["friendship_id"]
    received = {"friendship_id": friendship_id, "email": "ana@example.com", "status": "received"}
    assert api.get(friends_path, headers=recipient).json() == [received]
    assert api.get(friends_path, headers=sender).json()[0]["status"] == "sent"
    assert api.get(friends_path, headers=outsider).json() == []
    assert api.post(friends_path, headers=recipient, json={"email": "ana@example.com"}).status_code == 409

    friendship = f"{friends_path}/{friendship_id}"
    chat = f"/api/chats/{friendship_id}"
    assert api.get(chat, headers=sender).status_code == 404
    assert api.post(chat, headers=recipient, json={"body": "Hola"}).status_code == 404
    assert api.post(friendship + "/accept", headers=sender).status_code == 404
    assert api.post(friendship + "/accept", headers=outsider).status_code == 404
    assert api.post(friendship + "/cancel", headers=recipient).status_code == 404
    assert api.post(friendship + "/accept", headers=recipient).status_code == 204
    assert api.get(friends_path, headers=recipient).json() == [{**received, "status": "friend"}]
    assert api.post(friendship + "/cancel", headers=sender).status_code == 404
    assert api.get(chat, headers=outsider).status_code == 404
    assert api.post(chat, headers=outsider, json={"body": "Hola"}).status_code == 404
    assert api.post(chat, headers=sender, json={"body": "   "}).status_code == 422

    first = api.post(chat, headers=sender, json={"body": " Hola Eva "}).json()
    second = api.post(chat, headers=recipient, json={"body": "¡Hola Ana!"}).json()
    assert first["author_email"] == "ana@example.com" and first["body"] == "Hola Eva"
    assert second["author_email"] == "eva@example.com"
    assert api.get(chat, headers=recipient).json() == {"messages": [first, second], "has_more": False}
    assert api.get(chat, headers=sender, params={"before_id": second["id"]}).json()["messages"] == [first]
    assert api.get(chat, headers=sender, params={"after_id": first["id"]}).json()["messages"] == [second]
    assert api.post("/api/auth/logout", headers=sender).status_code == 204
    assert api.get(chat, headers=sender).status_code == 401
    assert api.post(chat, headers=sender, json={"body": "Sesión cerrada"}).status_code == 401
    assert api.get(chat, headers=recipient).json()["messages"] == [first, second]


def test_friend_requests_can_be_rejected_or_cancelled(api):
    sender = account(api, "ana@example.com")
    recipient = account(api, "eva@example.com")
    previous_id = None
    for action, actor in (("reject", recipient), ("cancel", sender)):
        sent = api.post("/api/friends", headers=sender, json={"email": "eva@example.com"})
        assert sent.status_code == 201
        friendship_id = sent.json()["friendship_id"]
        assert friendship_id != previous_id
        previous_id = friendship_id
        friendship = f"/api/friends/{friendship_id}"
        assert api.post(friendship + "/" + action, headers=actor).status_code == 204
        assert api.get("/api/friends", headers=recipient).json() == []
        assert api.get("/api/friends", headers=sender).json() == []


def test_unread_messages_are_private_and_read_positions_only_advance(api):
    sender = account(api, "ana@example.com")
    recipient = account(api, "eva@example.com")
    outsider = account(api, "luz@example.com")
    unread = "/api/chats/unread"
    clear = {"total": 0, "chats": []}

    def start_chat(target, email):
        invitation = api.post("/api/friends", headers=sender, json={"email": email})
        friendship_id = invitation.json()["friendship_id"]
        assert api.post(f"/api/friends/{friendship_id}/accept", headers=target).status_code == 204
        return friendship_id, f"/api/chats/{friendship_id}"

    friendship_id, chat = start_chat(recipient, "eva@example.com")
    _, another_chat = start_chat(outsider, "luz@example.com")
    other_message = api.post(another_chat, headers=sender, json={"body": "Otra conversación"}).json()["id"]
    first = api.post(chat, headers=sender, json={"body": "Primero"}).json()["id"]
    second = api.post(chat, headers=sender, json={"body": "Segundo"}).json()["id"]

    def count_for(user, count):
        return api.get(unread, headers=user).json() == {
            "total": count, "chats": [{"friendship_id": friendship_id, "unread_count": count}] if count else [],
        }

    assert api.get(unread).status_code == 401
    assert api.get(unread, headers=sender).json() == clear
    assert count_for(recipient, 2)
    assert api.get(chat, headers=recipient).status_code == 200
    assert count_for(recipient, 2)
    assert api.post(chat + "/read", headers=outsider, json={"message_id": first}).status_code == 404
    assert api.post(chat + "/read", headers=recipient, json={"message_id": other_message}).status_code == 404
    assert api.post(chat + "/read", headers=recipient, json={"message_id": first}).status_code == 204
    assert count_for(recipient, 1)

    api.post(chat, headers=sender, json={"body": "Tercero"})
    assert api.post(chat + "/read", headers=recipient, json={"message_id": second}).status_code == 204
    assert api.post(chat + "/read", headers=recipient, json={"message_id": first}).status_code == 204
    assert count_for(recipient, 1)
    assert api.get(unread, headers=outsider).json()["total"] == 1

    with db.database(write=True) as connection:
        connection.execute("DROP TABLE direct_message_reads")
    db.init_db()
    assert api.get(unread, headers=recipient).json() == clear
    assert api.get(unread, headers=outsider).json() == clear
    api.post(chat, headers=sender, json={"body": "Nuevo tras la migración"})
    db.init_db()
    assert count_for(recipient, 1)


def test_chat_attachments_count_as_messages_and_require_chat_membership(api):
    sender = account(api, "ana@example.com")
    recipient = account(api, "eva@example.com")
    outsider = account(api, "luz@example.com")
    pending = api.post("/api/friends", headers=sender, json={"email": "eva@example.com"})
    friendship_id = pending.json()["friendship_id"]
    chat = f"/api/chats/{friendship_id}"
    upload = chat + "/attachments"
    headers = {**sender, "Content-Type": "application/octet-stream"}
    file_name = {"file_name": "Vacaciones.png"}

    assert api.post(upload, headers=headers, params=file_name, content=TEST_IMAGE).status_code == 404
    assert api.post(f"/api/friends/{friendship_id}/accept", headers=recipient).status_code == 204
    assert api.post(upload, headers=headers, params={"file_name": "../imagen.png"}, content=TEST_IMAGE).status_code == 422
    too_large = b"x" * (MAX_ATTACHMENT_BYTES + 1)
    assert api.post(upload, headers=headers, params=file_name, content=too_large).status_code == 413
    assert api.get(chat, headers=recipient).json()["messages"] == []

    stored = api.post(upload, headers=headers, params=file_name, content=TEST_IMAGE)
    assert stored.status_code == 201
    image = stored.json()
    assert image["attachment"] == {"file_name": "Vacaciones.png", "size_bytes": len(TEST_IMAGE), "preview": True}
    assert api.get(chat, headers=recipient).json()["messages"] == [image]
    assert api.get("/api/chats/unread", headers=recipient).json()["total"] == 1

    file_path = f"{upload}/{image['id']}"
    preview_path = file_path + "/preview"
    for path in (file_path, preview_path):
        assert api.get(path).status_code == 401
        assert api.get(path, headers=outsider).status_code == 404
    preview = api.get(preview_path, headers=recipient)
    assert preview.content == TEST_IMAGE
    assert preview.headers["Content-Type"] == "image/png"
    assert preview.headers["Content-Disposition"].startswith("inline;")
    assert preview.headers["X-Content-Type-Options"] == "nosniff"
    assert preview.headers["Cache-Control"] == "no-store"
    download = api.get(file_path, headers=recipient)
    assert download.content == TEST_IMAGE
    assert download.headers["Content-Type"] == "application/octet-stream"
    assert download.headers["Content-Disposition"].startswith("attachment;")

    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="5" height="5"/></svg>'
    file = api.post(upload, headers=headers, params={"file_name": "dibujo.png"}, content=svg).json()
    assert file["attachment"] == {"file_name": "dibujo.png", "size_bytes": len(svg), "preview": False}
    file_path = f"{upload}/{file['id']}"
    assert api.get(file_path + "/preview", headers=recipient).status_code == 404
    assert api.get(file_path, headers=recipient).content == svg
