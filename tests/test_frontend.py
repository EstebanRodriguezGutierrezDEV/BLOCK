from frontend import app as frontend
from conftest import PASSWORD


def test_welcome_and_task_screens(api, monkeypatch):
    def forward(path, method, data, token):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        response = api.request(method, path, content=data, headers=headers)
        return response.status_code, response.content

    monkeypatch.setattr(frontend, "api_request", forward)
    monkeypatch.delenv("TODO_PUBLIC_URL", raising=False)
    origin = {"Origin": "http://localhost"}
    with frontend.app.test_client() as client:
        welcome = client.get("/")
        assert welcome.status_code == 200
        html = welcome.get_data(as_text=True)
        assert "Tu block de tareas" in html
        assert "organizarte." in html
        assert 'href="/tareas"' in html
        assert 'href="/registro"' in html
        assert 'href="/iniciar-sesion"' in html
        assert "app.js" not in html

        assert client.get("/tareas").location == "/iniciar-sesion"
        assert client.get("/api/tasks").status_code == 401
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
        assert client.post("/api/tasks", json={}, headers={"Origin": "https://otra.example.com"}).status_code == 403
        assert client.post("/cerrar-sesion", headers=origin).location == "/iniciar-sesion"
        assert client.get("/tareas").location == "/iniciar-sesion"
