from backend import accounts, db, rate_limits
from conftest import PASSWORD, register_and_login

EMAIL = "marta@example.com"


def test_registration_allows_immediate_login_and_preserves_existing_password(api):
    credentials = {"email": EMAIL, "password": PASSWORD}
    assert api.post("/api/auth/register", json={**credentials, "email": "sin-arroba"}).status_code == 422
    assert api.post("/api/auth/register", json={"email": EMAIL}).status_code == 422
    rejected = api.post("/api/auth/register", json={**credentials, "password": "corta"})
    assert rejected.status_code == 422
    assert "corta" not in rejected.text
    assert '"input"' not in rejected.text
    assert api.post("/api/auth/register", json={**credentials, "email": EMAIL.upper()}).status_code == 201
    duplicate = {**credentials, "password": "Otra contraseña distinta 9"}
    assert api.post("/api/auth/register", json=duplicate).status_code == 409
    assert api.post("/api/auth/login", json=duplicate).status_code == 401
    login = api.post("/api/auth/login", json=credentials)
    assert login.status_code == 200
    auth = {"Authorization": f"Bearer {login.json()['token']}"}
    assert api.get("/api/auth/me", headers=auth).json()["email"] == EMAIL
    assert api.post("/api/auth/logout", headers=auth).status_code == 204
    assert api.get("/api/tasks", headers=auth).status_code == 401
    with db.database() as connection:
        user = connection.execute("SELECT * FROM users").fetchone()
        assert user["password_hash"] != PASSWORD


def test_all_task_routes_require_login_and_enforce_ownership(api):
    task = {"title": "Tarea privada", "start_date": "2026-11-20", "end_date": "2026-11-20"}
    assert api.get("/api/tasks").status_code == 401
    assert api.post("/api/tasks", json=task).status_code == 401
    for method, data in (("GET", None), ("PATCH", {"completed": True}), ("DELETE", None)):
        assert api.request(method, "/api/tasks/1", json=data).status_code == 401

    owner = register_and_login(api, EMAIL)
    other = register_and_login(api, "otra@example.com")
    owner_headers = {"Authorization": f"Bearer {owner}"}
    other_headers = {"Authorization": f"Bearer {other}"}
    created = api.post("/api/tasks", json=task, headers=owner_headers).json()
    path = f"/api/tasks/{created['id']}"
    assert api.get("/api/tasks", headers=other_headers).json() == []
    for method, data in (("GET", None), ("PATCH", {"completed": True}), ("DELETE", None)):
        assert api.request(method, path, json=data, headers=other_headers).status_code == 404
    assert api.get(path, headers=owner_headers).json() == created

    with db.database() as connection:
        connection.execute("UPDATE sessions SET expires_at = 0 WHERE token_hash = ?", (accounts.token_hash(owner),))
    assert api.get(path, headers=owner_headers).status_code == 401


def test_login_limits_are_keyed_by_normalized_email_and_expire(api, monkeypatch):
    credentials = {"email": EMAIL, "password": PASSWORD}
    clock = [1200]
    monkeypatch.setattr(rate_limits, "time", lambda: clock[0])
    monkeypatch.setattr(accounts, "LOGIN_ACCOUNT", accounts.LOGIN_ACCOUNT._replace(attempts=2))
    assert api.post("/api/auth/register", json=credentials).status_code == 201

    for email in (EMAIL, EMAIL.upper()):
        wrong = {"email": email, "password": "Incorrecta"}
        assert api.post("/api/auth/login", json=wrong).status_code == 401
    blocked = api.post("/api/auth/login", json=credentials)
    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"] == "300"
    assert blocked.headers["Cache-Control"] == "no-store"
    assert "token" not in blocked.json()
    assert api.post("/api/auth/login", json={**credentials, "email": "otra@example.com"}).status_code == 401

    clock[0] += int(blocked.headers["Retry-After"])
    assert api.post("/api/auth/login", json=credentials).status_code == 200


def test_registration_ignores_untrusted_forwarding_headers(api, monkeypatch):
    monkeypatch.setattr(rate_limits, "time", lambda: 1200)
    monkeypatch.setattr(accounts, "REGISTER_IP", accounts.REGISTER_IP._replace(attempts=1))
    credentials = {"email": EMAIL, "password": PASSWORD}
    assert api.post("/api/auth/register", json=credentials).status_code == 201
    blocked = api.post(
        "/api/auth/register", json={**credentials, "email": "otra@example.com"},
        headers={"X-Forwarded-For": "192.0.2.3", "X-Vercel-Forwarded-For": "192.0.2.4"},
    )
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) > 0
