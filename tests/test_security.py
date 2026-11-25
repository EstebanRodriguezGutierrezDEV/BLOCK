import asyncio
from contextlib import contextmanager
import json

from flask import Flask
import psycopg
import pytest

from backend import accounts, db, main, rate_limits, social, workspaces
from backend.request_security import RequestSecurity
from conftest import register_and_login
from frontend.app import app as web
from frontend.deployment import configure_deployment

DATES = {"start_date": "2026-11-20", "end_date": "2026-11-25"}


@pytest.fixture
def production_web(monkeypatch):
    monkeypatch.setattr(web, "wsgi_app", web.wsgi_app)
    monkeypatch.setitem(web.config, "TRUSTED_HOSTS", None)
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.setenv("TODO_PUBLIC_URL", "https://tareas.example.com")
    monkeypatch.setenv("VERCEL_URL", "build-123.vercel.app")
    monkeypatch.setenv("VERCEL_PROJECT_PRODUCTION_URL", "block.vercel.app")
    monkeypatch.delenv("VERCEL_BRANCH_URL", raising=False)
    configure_deployment(web)
    return web.test_client()


def test_vercel_rejects_unknown_hosts_and_binds_writes_to_the_actual_origin(production_web):
    client = production_web
    forwarded = {"X-Forwarded-Proto": "https"}
    for host in ("tareas.example.com", "build-123.vercel.app", "block.vercel.app"):
        base = f"http://{host}"
        page = client.get("/", base_url=base, headers=forwarded)
        assert page.status_code == 200
        assert page.headers["Strict-Transport-Security"] == "max-age=31536000"
        assert page.headers["Vercel-CDN-Cache-Control"] == "no-store"
        # A valid same-origin write reaches authentication, including previews.
        assert client.post("/api/tasks", base_url=base, json={}, headers={
            **forwarded, "Origin": f"https://{host}",
        }).status_code == 401
        for origin in (None, "null", "https://attacker.example", "https://tareas.example.com.attacker.example"):
            headers = {**forwarded, **({"Origin": origin} if origin else {})}
            assert client.post("/api/tasks", base_url=base, json={}, headers=headers).status_code == 403
    for host in ("attacker.example", "unrelated.vercel.app", "tareas.example.com.attacker.example"):
        assert client.get("/", base_url=f"https://{host}", headers={
            "X-Forwarded-Host": "tareas.example.com",
        }).status_code == 400
    assert client.put("/api/tasks", base_url="https://tareas.example.com", json={}).status_code == 403


def test_proxy_configuration_does_not_trust_client_supplied_forwarded_ip(production_web):
    from frontend.app import client_address

    @contextmanager
    def request_context(headers):
        with web.test_request_context("/", base_url="https://tareas.example.com", headers=headers,
                                      environ_base={"REMOTE_ADDR": "192.0.2.10"}):
            yield

    # ProxyFix must not rewrite remote_addr from the untrusted generic header.
    assert web.wsgi_app.x_for == 0
    with request_context({"X-Forwarded-For": "192.0.2.99"}):
        assert client_address() == "192.0.2.10"


@pytest.mark.parametrize("url", ["http://tareas.example.com", "https://user:password@tareas.example.com",
                                     "https://tareas.example.com/path", "https://tareas.example.com:8443"])
def test_vercel_rejects_unsafe_public_url(monkeypatch, url):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("TODO_PUBLIC_URL", url)
    with pytest.raises(ValueError):
        configure_deployment(Flask(__name__))


def test_development_does_not_trust_forwarded_headers(monkeypatch):
    local = Flask(__name__)
    original = local.wsgi_app
    configure_deployment(local)
    assert local.wsgi_app == original


@pytest.mark.parametrize("environment", ["production", "preview"])
def test_vercel_postgres_requires_certificate_and_hostname_verification(monkeypatch, environment):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("VERCEL_ENV", environment)
    monkeypatch.setenv("DATABASE_URL", "postgresql://db.example.com/todo?sslmode=disable")
    calls = []

    @contextmanager
    def connect(*args, **kwargs):
        calls.append(kwargs)
        yield object()

    monkeypatch.setattr(db.psycopg, "connect", connect)
    with db.database():
        pass
    assert calls[0]["sslmode"] == "verify-full"
    assert calls[0]["sslrootcert"] == "system"
    monkeypatch.setenv("DATABASE_URL", "postgresql://db.example.com/todo?sslrootcert=/trusted/ca.pem")
    with db.database():
        pass
    assert calls[1]["sslrootcert"] == "/trusted/ca.pem"

    def failed_connect(*args, **kwargs):
        raise psycopg.OperationalError("certificate verify failed")

    monkeypatch.setattr(db.psycopg, "connect", failed_connect)
    with pytest.raises(psycopg.OperationalError):
        with db.database():
            pass


def test_vercel_never_falls_back_to_sqlite_without_database_url(monkeypatch, tmp_path):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "must-not-exist.db")
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        with db.database():
            pass
    assert not db.DB_PATH.exists()


def test_authentication_and_permission_payloads_reject_unknown_and_coerced_fields(api):
    token = register_and_login(api, "owner@example.com")
    auth = {"Authorization": f"Bearer {token}"}
    task = api.post("/api/tasks", json={"title": "Privada", **DATES}, headers=auth).json()
    rejected = api.patch(f"/api/tasks/{task['id']}", json={"completed": "true"}, headers=auth)
    assert rejected.status_code == 422
    assert api.patch(f"/api/tasks/{task['id']}", json={"user_id": 999}, headers=auth).status_code == 422
    assert api.get(f"/api/tasks/{task['id']}", headers=auth).json() == task
    rejected_login = api.post("/api/auth/login", json={
        "email": "owner@example.com", "password": "Secret that must not be echoed", "admin": True,
    })
    assert rejected_login.status_code == 422
    assert "Secret that must not be echoed" not in rejected_login.text
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert api.get(path).status_code == 404


def test_deep_json_is_rejected_without_crashing_or_echoing_content(api):
    token = register_and_login(api, "owner@example.com")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    malicious = b'[' * 1500 + b'"private-value"' + b']' * 1500
    rejected = api.post("/api/tasks", headers=headers, content=malicious)
    assert rejected.status_code == 400
    assert "private-value" not in rejected.text
    assert rejected.headers["Cache-Control"] == "no-store"
    for encoding in ("utf-16", "utf-32"):
        wide_json = (r'{"title":"quote\"","nested":' + '[' * 1500 + '0' + ']' * 1500 + '}').encode(encoding)
        assert api.post("/api/tasks", headers=headers, content=wide_json).status_code == 400
    # Quotes and escaped quotes inside ordinary text do not increase depth.
    title = '[{"' * 40
    created = api.post("/api/tasks", headers=headers, json={"title": title, **DATES})
    assert created.status_code == 201 and created.json()["title"] == title
    # An escaped quote can cross a chunk boundary without becoming structure.
    chunks = [b'{"title":"before\\', b'"' + b'[' * 80 + b'","start_date":"2026-11-20","end_date":"2026-11-25"}']
    messages = []

    async def app(scope, receive, send):
        body = b""
        while True:
            message = await receive()
            body += message["body"]
            if not message.get("more_body"):
                break
        messages.append(json.loads(body))

    async def receive():
        body = chunks.pop(0)
        return {"type": "http.request", "body": body, "more_body": bool(chunks)}

    asyncio.run(RequestSecurity(app)({
        "type": "http", "method": "POST", "path": "/api/tasks",
        "headers": [(b"content-type", b"application/json")],
    }, receive, None))
    assert messages[0]["title"] == 'before"' + '[' * 80


def test_write_limits_are_per_account_and_do_not_limit_reads(api, monkeypatch):
    monkeypatch.setattr(rate_limits, "time", lambda: 1200)
    monkeypatch.setattr(accounts, "AUTHENTICATED_WRITES", rate_limits.RateLimit("authenticated_write", 1, 60))
    owner = {"Authorization": f"Bearer {register_and_login(api, 'owner@example.com')}"}
    other = {"Authorization": f"Bearer {register_and_login(api, 'other@example.com')}"}
    data = {"title": "Primera", **DATES}
    assert api.post("/api/tasks", headers=owner, json=data).status_code == 201
    blocked = api.post("/api/tasks", headers=owner, json=data)
    assert blocked.status_code == 429 and blocked.headers["Retry-After"] == "60"
    assert blocked.headers["Cache-Control"] == "no-store"
    assert len(api.get("/api/tasks", headers=owner).json()) == 1
    assert api.post("/api/tasks", headers=other, json=data).status_code == 201
    monkeypatch.setattr(rate_limits, "time", lambda: 1260)
    assert api.post("/api/tasks", headers=owner, json=data).status_code == 201


@pytest.mark.parametrize("module,policy,path,data", [
    (main, "TASK_CREATIONS", "/api/tasks", {"title": "Limitada", **DATES}),
    (workspaces, "WORKSPACE_CREATIONS", "/api/workspaces", {"name": "Limitado"}),
])
def test_daily_creation_limits(api, monkeypatch, module, policy, path, data):
    original = getattr(module, policy)
    monkeypatch.setattr(module, policy, original._replace(attempts=1))
    auth = {"Authorization": f"Bearer {register_and_login(api, 'owner@example.com')}"}
    assert api.post(path, headers=auth, json=data).status_code == 201
    assert api.post(path, headers=auth, json=data).status_code == 429


def test_upload_byte_budget_is_shared_by_tasks_and_chats(api, monkeypatch):
    monkeypatch.setattr(rate_limits, "time", lambda: 1200)
    budget = rate_limits.ATTACHMENT_BYTES._replace(attempts=5)
    monkeypatch.setattr(main, "ATTACHMENT_BYTES", budget)
    monkeypatch.setattr(social, "ATTACHMENT_BYTES", budget)
    owner = {"Authorization": f"Bearer {register_and_login(api, 'owner@example.com')}"}
    guest = {"Authorization": f"Bearer {register_and_login(api, 'guest@example.com')}"}
    workspace = api.post("/api/workspaces", headers=owner, json={"name": "Adjuntos"}).json()
    scope = f"?workspace_id={workspace['id']}"
    task = api.post("/api/tasks" + scope, headers=owner, json={"title": "Tarjeta", **DATES}).json()
    task_files = f"/api/tasks/{task['id']}/attachments" + scope
    friendship = api.post("/api/friends", headers=owner, json={"email": "guest@example.com"}).json()["friendship_id"]
    assert api.post(f"/api/friends/{friendship}/accept", headers=guest, json={}).status_code == 204
    chat_files = f"/api/chats/{friendship}/attachments?file_name=test.bin"
    binary = {**owner, "Content-Type": "application/octet-stream"}
    assert api.post(task_files + "&file_name=test.bin", headers=binary, content=b"1234").status_code == 201
    assert api.post(chat_files, headers=binary, content=b"5").status_code == 201
    for path in (task_files + "&file_name=blocked.bin", chat_files):
        blocked = api.post(path, headers=binary, content=b"6")
        assert blocked.status_code == 429 and int(blocked.headers["Retry-After"]) > 0
    assert len(api.get(task_files, headers=owner).json()) == 1
    assert len(api.get(f"/api/chats/{friendship}", headers=owner).json()["messages"]) == 1
    # An initial upload larger than the entire budget must also be rejected.
    assert api.post(chat_files, headers={**guest, "Content-Type": "application/octet-stream"}, content=b"123456").status_code == 429
    assert api.post(chat_files, headers={**guest, "Content-Type": "application/octet-stream"}, content=b"12345").status_code == 201
