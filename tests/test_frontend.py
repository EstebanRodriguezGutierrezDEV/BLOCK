from struct import unpack
from xml.etree.ElementTree import fromstring

from frontend import app as frontend
from conftest import PASSWORD, registration_data


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
        assert "Organiza tus tareas con un" in html
        assert 'class="primary-button welcome-cta" href="/registro">Crear mi cuenta' in html
        assert 'href="#ejemplo"' in html
        assert "Una semana de ejemplo" in html
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
        form = client.get("/registro")
        assert form.status_code == 200
        assert b'type="checkbox" name="privacy_acknowledgement"' in form.data
        assert b'href="/privacidad.pdf"' in form.data
        assert client.post("/registro", data=login, headers=origin).status_code == 422
        assert client.get("/privacidad").status_code == 200
        pdf = client.get("/privacidad.pdf")
        assert pdf.headers["Content-Type"] == "application/pdf"
        assert pdf.data.startswith(b"%PDF-")
        registration = client.post("/registro", data=registration_data(login["email"]), headers=origin)
        assert registration.location == "/iniciar-sesion?cuenta_creada=1"
        signed_in = client.post("/iniciar-sesion", data=login, headers=origin)
        assert signed_in.location == "/tareas"
        assert "HttpOnly" in signed_in.headers["Set-Cookie"]
        assert "SameSite=Lax" in signed_in.headers["Set-Cookie"]
        signed_in_home = client.get("/").get_data(as_text=True)
        assert 'class="primary-button welcome-cta" href="/tareas">Entrar a mi block' in signed_in_home

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
        assert api.post("/api/auth/register", json=registration_data(other["email"])).status_code == 201
        denied_login = client.get(path).location
        assert client.post(denied_login, data=other, headers=origin).location == path
        assert client.get(path).status_code == 404
        stale = client.get("/api/friends", headers={"X-Todo-Session-View": previous_view})
        assert stale.status_code == 409 and stale.headers["X-Todo-Session-Changed"] == "1"
        stale_form = client.post("/cuenta/eliminar", headers=origin, data={
            "session_view": previous_view, "current_password": PASSWORD,
        })
        assert stale_form.location == "/cuenta"
        assert client.get("/cuenta").status_code == 200
        monkeypatch.delenv("TODO_PRIVACY_CONTROLLER")
        client.post("/cerrar-sesion", headers=origin)
        form = client.get("/registro")
        assert form.status_code == 200 and b'privacy_acknowledgement' not in form.data
        ordinary_signup = client.post("/registro", headers=origin, data={**login, "email": "tercera@example.com"})
        assert ordinary_signup.location == "/iniciar-sesion?cuenta_creada=1"


def test_search_metadata_and_sitemap_use_the_public_origin(monkeypatch):
    monkeypatch.setenv("TODO_PUBLIC_URL", "https://tareas.example.com/")
    monkeypatch.setenv("VERCEL_PROJECT_PRODUCTION_URL", "app-original.vercel.app")
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.setenv("TODO_GOOGLE_SITE_VERIFICATION", "test-verification")
    site = "https://tareas.example.com"
    with frontend.app.test_client() as client:
        landing = client.get("/?utm_source=ejemplo", base_url="https://alias.example.com")
        html = landing.get_data(as_text=True)
        assert "X-Robots-Tag" not in landing.headers
        assert "<title>Organizador de tareas con calendario | Tu block de tareas</title>" in html
        assert '<meta name="description" content="Organiza tareas personales y compartidas en un calendario.' in html
        assert f'<link rel="canonical" href="{site}/"' in html
        assert f'<meta property="og:url" content="{site}/"' in html
        assert f'<meta property="og:image" content="{site}/static/icons/social-preview.png"' in html
        assert '<meta name="google-site-verification" content="test-verification"' in html
        assert '"@type": "WebSite"' in html
        assert "alias.example.com" not in html and "app-original.vercel.app" not in html

        robots = client.get("/robots.txt")
        assert robots.mimetype == "text/plain"
        assert robots.get_data(as_text=True) == f"User-agent: *\nAllow: /\n\nSitemap: {site}/sitemap.xml\n"
        sitemap = client.get("/sitemap.xml")
        assert sitemap.mimetype == "application/xml"
        namespace = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        assert [entry.text for entry in fromstring(sitemap.data).findall("s:url/s:loc", namespace)] == [site + "/"]
        preview_image = client.get("/static/icons/social-preview.png")
        assert preview_image.mimetype == "image/png"
        assert "X-Robots-Tag" not in preview_image.headers
        assert unpack(">II", preview_image.data[16:24]) == (1200, 630)

        for private in ("/tareas", "/espacios/1", "/mensajes", "/cuenta", "/iniciar-sesion", "/registro", "/api/tasks"):
            response = client.get(private)
            assert response.headers["X-Robots-Tag"] == "noindex"
            assert 'rel="canonical"' not in response.get_data(as_text=True)


def test_preview_has_noindex_and_uses_the_vercel_production_domain(monkeypatch):
    monkeypatch.delenv("TODO_PUBLIC_URL", raising=False)
    monkeypatch.setenv("VERCEL_PROJECT_PRODUCTION_URL", "app-original.vercel.app")
    monkeypatch.setenv("VERCEL_ENV", "preview")
    monkeypatch.setenv("TODO_GOOGLE_SITE_VERIFICATION", "test-verification")
    with frontend.app.test_client() as client:
        landing = client.get("/", base_url="https://preview.example.com")
        assert landing.headers["X-Robots-Tag"] == "noindex"
        assert '<link rel="canonical" href="https://app-original.vercel.app/"' in landing.get_data(as_text=True)
        assert 'name="google-site-verification"' not in landing.get_data(as_text=True)
        assert "Sitemap:" not in client.get("/robots.txt").get_data(as_text=True)
        assert client.get("/sitemap.xml").status_code == 404

        monkeypatch.setenv("VERCEL_ENV", "production")
        assert "X-Robots-Tag" not in client.get("/").headers
        assert "https://app-original.vercel.app/" in client.get("/sitemap.xml").get_data(as_text=True)

        monkeypatch.delenv("VERCEL_PROJECT_PRODUCTION_URL")
        assert 'rel="canonical"' not in client.get("/", base_url="https://unverified.example.com").get_data(as_text=True)
        assert client.get("/sitemap.xml").status_code == 404
