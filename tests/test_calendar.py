"""Pruebas opcionales de navegador: instalar Playwright y Chromium para ejecutarlas."""

import os
from pathlib import Path
import threading

import pytest
from werkzeug.serving import make_server

from conftest import PASSWORD, register_and_login
from frontend import app as frontend
from frontend.app import app as frontend_app

playwright = pytest.importorskip("playwright.sync_api")


@pytest.fixture
def calendar_page(api, tmp_path, monkeypatch):
    monkeypatch.delenv("TODO_PUBLIC_URL", raising=False)
    token = register_and_login(api, "calendario@example.com")

    def forward(path, method, data, token):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        response = api.request(method, path, content=data, headers=headers)
        return response.status_code, response.content

    monkeypatch.setattr(frontend, "api_request", forward)
    with playwright.sync_playwright() as driver:
        executable = os.environ.get("TODO_TEST_BROWSER", driver.chromium.executable_path)
        if not Path(executable).is_file():
            pytest.skip("Instala Chromium con python -m playwright install chromium")
        browser = driver.chromium.launch(executable_path=executable, headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, locale="es-ES", timezone_id="Europe/Berlin")
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        server = make_server("127.0.0.1", 0, frontend_app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_port}"
        context.add_cookies([{"name": "todo_session", "value": token, "url": base_url, "httpOnly": True}])
        try:
            yield page, api, base_url, tmp_path, {"Authorization": f"Bearer {token}"}
            assert errors == []
        finally:
            context.close()
            browser.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


def test_calendar_workflow(calendar_page):
    page, api, base_url, _, auth = calendar_page
    page.goto(base_url)
    playwright.expect(page.locator("h1")).to_contain_text("organizarte.")
    page.get_by_role("link", name="Entrar a mi block").click()
    playwright.expect(page.locator("#loading-status")).to_be_hidden()
    playwright.expect(page.locator(".month-button")).to_have_count(12)

    page.locator("#new-task-button").click()
    page.locator("#task-title-input").fill("Preparar el nuevo año")
    page.locator("#task-description").fill("Plan que atraviesa diciembre y enero")
    page.locator("#task-start").fill("2026-12-30")
    page.locator("#task-end").fill("2027-01-03")
    page.locator("#save-task").click()
    playwright.expect(page.locator("#task-dialog")).not_to_be_visible()
    playwright.expect(page.locator("#calendar-title")).to_have_text("Diciembre de 2026")
    for day in ("2026-12-30", "2026-12-31", "2027-01-01", "2027-01-03"):
        playwright.expect(page.locator(f'[data-date="{day}"] .calendar-event')).to_have_text("Preparar el nuevo año")
    playwright.expect(page.locator('[data-date="2026-12-29"] .calendar-event')).to_have_count(0)
    playwright.expect(page.locator('[data-date="2027-01-04"] .calendar-event')).to_have_count(0)
    page.locator("#next-month").click()
    playwright.expect(page.locator("#calendar-title")).to_have_text("Enero de 2027")
    playwright.expect(page.locator("#total-count")).to_have_text("1")
    page.locator('[data-date="2027-01-02"]').click()
    playwright.expect(page.locator("#task-list .task-item")).to_have_count(1)
    playwright.expect(page.locator("#clear-day")).to_be_visible()
    page.locator("#clear-day").click()

    page.get_by_role("button", name="Editar Preparar el nuevo año", exact=True).click()
    page.locator("#task-start").fill("2028-02-29")
    page.locator("#task-end").fill("2028-03-02")
    page.locator("#save-task").click()
    playwright.expect(page.locator("#calendar-title")).to_have_text("Febrero de 2028")
    playwright.expect(page.locator('[data-date="2028-02-29"] .calendar-event')).to_have_count(1)
    playwright.expect(page.locator('[data-date="2028-03-02"] .calendar-event')).to_have_count(1)
    playwright.expect(page.locator('[data-date="2028-03-03"] .calendar-event')).to_have_count(0)

    page.locator(".task-checkbox").check()
    playwright.expect(page.locator("#done-count")).to_have_text("1")
    playwright.expect(page.locator(".task-item .status-badge")).to_have_text("Completada")
    playwright.expect(page.locator('[data-date="2028-02-29"] .calendar-event')).to_have_class("calendar-event status-completed")
    page.get_by_role("button", name="Por hacer", exact=True).click()
    playwright.expect(page.locator("#task-list .task-item")).to_have_count(0)
    page.get_by_role("button", name="Todas", exact=True).click()
    page.locator("#search").fill("inexistente")
    playwright.expect(page.locator("#task-list .task-item")).to_have_count(0)
    page.locator("#search").fill("nuevo año")
    playwright.expect(page.locator("#task-list .task-item")).to_have_count(1)
    page.reload()
    playwright.expect(page.locator("#loading-status")).to_be_hidden()
    page.locator("#year-select").select_option("2028")
    page.locator('[data-month="1"]').click()
    playwright.expect(page.locator("#task-list .task-item")).to_have_count(1)
    assert api.get("/api/tasks", headers=auth).json()[0]["completed"] is True
    page.get_by_role("button", name="Eliminar Preparar el nuevo año", exact=True).click()
    confirmation = page.get_by_role("dialog", name="¿Deseas eliminar la tarea?")
    playwright.expect(confirmation).to_contain_text("Preparar el nuevo año")
    confirmation.get_by_role("button", name="Cancelar").click()
    playwright.expect(confirmation).to_be_hidden()
    assert len(api.get("/api/tasks", headers=auth).json()) == 1
    page.get_by_role("button", name="Eliminar Preparar el nuevo año", exact=True).click()
    confirmation.get_by_role("button", name="Eliminar tarea").click()
    playwright.expect(page.locator("#total-count")).to_have_text("0")
    assert api.get("/api/tasks", headers=auth).json() == []


def test_status_colors_many_tasks_and_mobile_layout(calendar_page):
    page, api, base_url, tmp_path, auth = calendar_page
    for title, start, end, completed in (
        ("Tarea vencida", "2026-11-01", "2026-11-19", False),
        ("Tarea en curso", "2026-11-19", "2026-11-21", False),
        ("Tarea futura", "2026-11-21", "2026-11-24", False),
        ("Tarea completada", "2026-11-20", "2026-11-21", True),
        ("Otra tarea", "2026-11-20", "2026-11-21", False),
    ):
        result = api.post("/api/tasks", headers=auth, json={"title": title, "start_date": start, "end_date": end})
        assert result.status_code == 201
        if completed:
            api.patch(f"/api/tasks/{result.json()['id']}", headers=auth, json={"completed": True})
    page.clock.set_fixed_time("2026-11-20T12:00:00+01:00")
    page.goto(f"{base_url}/tareas")
    playwright.expect(page.locator("#total-count")).to_have_text("5")
    for status in ("overdue", "active", "upcoming", "completed"):
        assert page.locator(f".task-item.status-{status}").count() >= 1
    playwright.expect(page.locator('[data-date="2026-11-20"] .calendar-more')).to_have_text("+1 más")
    page.locator('[data-date="2026-11-20"]').click()
    playwright.expect(page.locator("#task-list .task-item")).to_have_count(3)
    page.screenshot(path=str(tmp_path / "calendar-desktop.png"), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    page.screenshot(path=str(tmp_path / "calendar-mobile.png"), full_page=True)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert page.locator(".calendar-scroll").evaluate("el => el.scrollWidth > el.clientWidth")
    page.goto(base_url)
    page.screenshot(path=str(tmp_path / "welcome-mobile.png"), full_page=True)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_date_validation_and_api_error_recovery(calendar_page):
    page, api, base_url, _, auth = calendar_page
    page.goto(f"{base_url}/tareas")
    playwright.expect(page.locator("#loading-status")).to_be_hidden()
    page.locator("#new-task-button").click()
    page.locator("#task-title-input").fill("Fechas inválidas")
    page.locator("#task-start").fill("2026-11-25")
    page.locator("#task-end").fill("2026-11-20")
    page.locator("#save-task").click()
    assert not page.locator("#task-end").evaluate("el => el.checkValidity()")
    assert api.get("/api/tasks", headers=auth).json() == []
    page.locator("#task-end").fill("2026-11-25")
    page.locator("#save-task").click()
    playwright.expect(page.locator("#task-dialog")).not_to_be_visible()
    page.route("**/api/tasks", lambda route: route.fulfill(status=503, json={"detail": "Servicio temporalmente no disponible"}), times=1)
    page.reload()
    playwright.expect(page.locator("#notice")).to_contain_text("Servicio temporalmente no disponible")
    playwright.expect(page.locator("#empty-state")).to_be_hidden()
    page.get_by_role("button", name="Reintentar").click()
    playwright.expect(page.locator("#notice")).to_be_hidden()
    playwright.expect(page.locator("#total-count")).to_have_text("1")


def test_browser_registration_and_logout(calendar_page):
    page, _, base_url, _, _ = calendar_page
    page.context.clear_cookies()
    page.goto(f"{base_url}/tareas")
    playwright.expect(page.get_by_role("heading", name="Iniciar sesión")).to_be_visible()
    page.get_by_role("link", name="Registrarse").click()
    email = "nueva@example.com"
    page.get_by_label("Correo electrónico").fill(email)
    page.get_by_label("Contraseña", exact=True).fill(PASSWORD)
    page.get_by_role("button", name="Crear cuenta").click()
    playwright.expect(page.get_by_role("status")).to_contain_text("Cuenta creada")
    page.get_by_label("Correo electrónico").fill(email)
    page.get_by_label("Contraseña", exact=True).fill(PASSWORD)
    page.get_by_role("button", name="Iniciar sesión").click()
    playwright.expect(page.locator(".account-email")).to_have_text(email)
    page.get_by_role("button", name="Cerrar sesión").click()
    playwright.expect(page.get_by_role("heading", name="Iniciar sesión")).to_be_visible()
    page.goto(f"{base_url}/tareas")
    playwright.expect(page.get_by_role("heading", name="Iniciar sesión")).to_be_visible()
