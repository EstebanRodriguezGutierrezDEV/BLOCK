from frontend import app as frontend
from conftest import PASSWORD


def test_welcome_and_task_screens(api, monkeypatch):
    def forward(path, method, data, token, *, content_type="application/json"):
        headers = {"Content-Type": content_type}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        response = api.request(method, path, content=data, headers=headers)
        return response.status_code, response.content, response.headers

    monkeypatch.setattr(frontend, "api_request", forward)
    monkeypatch.delenv("TODO_PUBLIC_URL", raising=False)
    origin = {"Origin": "http://localhost"}
    with frontend.app.test_client() as client:
        welcome = client.get("/")
        assert welcome.status_code == 200
        assert "frame-ancestors 'none'" in welcome.headers["Content-Security-Policy"]
        assert "script-src 'self'" in welcome.headers["Content-Security-Policy"]
        assert welcome.headers["X-Frame-Options"] == "DENY"
        assert welcome.headers["Referrer-Policy"] == "same-origin"
        assert "Strict-Transport-Security" not in welcome.headers
        html = welcome.get_data(as_text=True)
        assert "Tu block de tareas" in html
        assert "organizarte." in html
        assert 'href="/tareas"' in html
        assert 'href="/registro"' in html
        assert 'href="/iniciar-sesion"' in html
        assert "app.js" not in html

        assert client.get("/tareas").location == "/iniciar-sesion"
        assert client.get("/api/tasks").status_code == 401
        assert client.get("/espacios").location == "/iniciar-sesion?volver=espacios"
        assert client.get("/espacios/1").location == "/iniciar-sesion?volver=espacios&espacio=1"
        assert client.get("/mensajes").location == "/iniciar-sesion?volver=mensajes"
        assert client.get("/invitacion").location == "/mensajes"
        assert client.get("/api/workspaces").status_code == 401
        assert client.get("/api/messages").status_code == 401
        assert client.get("/api/friends").status_code == 401
        assert client.get("/api/chats/1").status_code == 401
        login = {"email": "marta@example.com", "password": PASSWORD}
        assert client.post("/iniciar-sesion", data=login, headers=origin).status_code == 401
        registration = client.post("/registro", data=login, headers=origin)
        assert registration.location == "/iniciar-sesion?cuenta_creada=1"
        signed_in = client.post("/iniciar-sesion", data=login, headers=origin)
        assert signed_in.location == "/tareas"
        assert "HttpOnly" in signed_in.headers["Set-Cookie"]
        assert "SameSite=Lax" in signed_in.headers["Set-Cookie"]

        tasks = client.get("/tareas")
        assert tasks.status_code == 200
        html = tasks.get_data(as_text=True)
        assert login["email"] in html
        assert 'id="year-select"' in html
        assert 'id="calendar-grid"' in html
        assert 'name="start_date"' in html
        assert 'name="end_date"' in html
        assert "app.js" in html
        assert client.get("/static/app.js").status_code == 200
        assert client.get("/static/styles.css").status_code == 200
        workspace = client.post("/api/workspaces", json={"name": "Mi proyecto"}, headers=origin)
        assert workspace.status_code == 201
        path = f"/espacios/{workspace.json['id']}"
        assert client.get(path).status_code == 200
        inbox = client.get("/mensajes")
        assert inbox.status_code == 200
        assert inbox.headers["Cache-Control"] == "no-store"
        assert 'name="session-view"' in inbox.get_data(as_text=True)
        previous_view = client.get_cookie(frontend.SESSION_VIEW_COOKIE).value
        assert client.post("/api/tasks", json={}, headers={"Origin": "https://otra.example.com"}).status_code == 403
        assert client.post("/api/tasks", json={}, headers={**origin, "Sec-Fetch-Site": "cross-site"}).status_code == 403
        assert client.post("/cerrar-sesion", headers=origin).location == "/iniciar-sesion"
        assert client.get("/tareas").location == "/iniciar-sesion"
        assert client.get(path).status_code == 302
        assert client.get("/api/messages").status_code == 401
        assert client.get("/mensajes").status_code == 302

        other = {"email": "otra@example.com", "password": PASSWORD}
        assert api.post("/api/auth/register", json=other).status_code == 201
        denied_login = client.get(path).location
        assert client.post(denied_login, data=other, headers=origin).location == path
        assert client.get(path).status_code == 404
        stale = client.get("/api/friends", headers={"X-Todo-Session-View": previous_view})
        assert stale.status_code == 409 and stale.headers["X-Todo-Session-Changed"] == "1"
