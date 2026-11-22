from conftest import register_and_login


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
