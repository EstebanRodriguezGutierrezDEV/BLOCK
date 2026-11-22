"""Pruebas opcionales de navegador: instalar Playwright y Chromium para ejecutarlas."""

import os
from pathlib import Path
import threading

import pytest
from werkzeug.serving import make_server

from backend import main
from conftest import PASSWORD, TEST_IMAGE, register_and_login
from frontend import app as frontend
from frontend.app import app as frontend_app

playwright = pytest.importorskip("playwright.sync_api")


@pytest.fixture
def calendar_page(api, tmp_path, monkeypatch):
    monkeypatch.delenv("TODO_PUBLIC_URL", raising=False)
    token = register_and_login(api, "calendario@example.com")

    def forward(path, method, data, token, *, content_type="application/json"):
        headers = {"Content-Type": content_type}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        response = api.request(method, path, content=data, headers=headers)
        return response.status_code, response.content, response.headers

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
    playwright.expect(page.get_by_role("heading", level=1)).to_have_text("Organiza tus tareas con un calendario")
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
    playwright.expect(page.locator('[data-date="2026-11-20"] .calendar-event')).to_have_text(
        ["Tarea en curso", "Otra tarea", "Tarea completada"],
    )
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


def test_calendar_includes_multiple_shared_and_personal_tasks(calendar_page, monkeypatch):
    page, api, base_url, _, auth = calendar_page
    monkeypatch.setattr(main, "CALENDAR_PAGE_SIZE", 2)
    owner_token = register_and_login(api, "equipo@example.com")
    owner = {"Authorization": f"Bearer {owner_token}"}
    space = api.post("/api/workspaces", headers=owner, json={"name": "Equipo de pruebas"}).json()["id"]
    hidden = api.post("/api/workspaces", headers=owner, json={"name": "Solo equipo"}).json()["id"]
    dates = {"start_date": "2026-11-30", "end_date": "2026-12-02"}
    for title, headers, scope, status in (
        ("Tarea personal", auth, {}, "todo"),
        ("Otra personal", auth, {}, "todo"),
        ("Entrega conjunta", owner, {"workspace_id": space}, "todo"),
        ("Revisión compartida", owner, {"workspace_id": space}, "done"),
        ("Tarea de otro tablero", owner, {"workspace_id": hidden}, "todo"),
    ):
        created = api.post("/api/tasks", headers=headers, params=scope, json={
            "title": title, "board_status": status, **dates,
        })
        assert created.status_code == 201
    assert api.post(
        f"/api/workspaces/{space}/invitations", headers=owner, json={"email": "calendario@example.com"},
    ).status_code == 201
    invite = api.get("/api/messages", headers=auth).json()[0]["id"]
    assert api.post(f"/api/messages/{invite}/accept", headers=auth).status_code == 200
    member_id = api.get("/api/auth/me", headers=auth).json()["id"]
    assert api.patch(
        f"/api/workspaces/{space}/members/{member_id}", headers=owner, json={"can_change_status": True},
    ).status_code == 200

    page.clock.set_fixed_time("2026-11-30T12:00:00+01:00")
    page.goto(f"{base_url}/tareas")
    playwright.expect(page.locator("#total-count")).to_have_text("4")
    november = page.locator('[data-date="2026-11-30"]')
    playwright.expect(november.locator(".calendar-event")).to_have_text(
        ["Tarea personal", "Otra personal", "Entrega conjunta", "Revisión compartida"],
    )
    playwright.expect(november.locator(".calendar-event-shared")).to_have_count(2)
    delivery = november.get_by_text("Entrega conjunta", exact=True)
    playwright.expect(delivery).to_have_class("calendar-event status-upcoming calendar-event-shared")
    playwright.expect(november.get_by_text("Tarea personal", exact=True)).to_have_class("calendar-event status-active")
    playwright.expect(november.get_by_text("Revisión compartida", exact=True)).to_have_class(
        "calendar-event status-completed calendar-event-shared",
    )
    page.locator("#next-month").click()
    page.locator('[data-date="2026-12-02"]').click()
    playwright.expect(page.locator("#task-list .task-item")).to_have_count(4)
    playwright.expect(page.locator("#task-list .task-checkbox")).to_have_count(2)
    card = page.locator("#task-list .task-item").filter(has_text="Entrega conjunta")
    playwright.expect(card.locator(".status-badge")).to_have_text("Por empezar")
    playwright.expect(page.locator('[data-date="2026-12-03"] .calendar-event')).to_have_count(0)
    page.locator("#search").fill("Equipo de pruebas")
    playwright.expect(page.locator("#task-list .task-item")).to_have_count(2)
    page.get_by_role("link", name="Equipo de pruebas", exact=True).first.click()
    playwright.expect(page).to_have_url(f"{base_url}/espacios/{space}")
    page.get_by_label("Mover Entrega conjunta").select_option("doing")
    playwright.expect(page.locator("[data-column=doing]")).to_contain_text("Entrega conjunta")
    page.get_by_role("link", name="Mi calendario").click()
    playwright.expect(delivery).to_have_class("calendar-event status-active calendar-event-shared")
    playwright.expect(card.locator(".status-badge")).to_have_text("En curso")


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
    page.route("**/api/calendar/tasks", lambda route: route.fulfill(status=503, json={"detail": "Servicio temporalmente no disponible"}), times=1)
    page.reload()
    playwright.expect(page.locator("#notice")).to_contain_text("Servicio temporalmente no disponible")
    playwright.expect(page.locator("#empty-state")).to_be_hidden()
    page.get_by_role("button", name="Reintentar").click()
    playwright.expect(page.locator("#notice")).to_be_hidden()
    playwright.expect(page.locator("#total-count")).to_have_text("1")


def test_browser_registration_and_logout(calendar_page):
    page, _, base_url, _, _ = calendar_page
    page.context.clear_cookies()
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(base_url)
    primary = page.get_by_role("link", name="Crear mi cuenta", exact=True)
    playwright.expect(primary).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth === window.innerWidth")
    page.get_by_role("link", name="Ver un ejemplo").click()
    demo = page.get_by_role("region", name="Una semana de ejemplo")
    playwright.expect(demo.get_by_text("Revisar el borrador")).to_be_visible()
    thursday = demo.get_by_role("button", name="Jueves 15, 1 tarea")
    thursday.focus()
    page.keyboard.press("Enter")
    playwright.expect(thursday).to_have_attribute("aria-pressed", "true")
    playwright.expect(demo.get_by_text("Adjuntar los documentos")).to_be_visible()
    playwright.expect(demo.get_by_text("Revisar el borrador")).to_be_hidden()
    primary.click()
    playwright.expect(page).to_have_url(f"{base_url}/registro")
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


def test_account_panel_and_sidebar_work_across_the_app(calendar_page):
    page, api, base_url, _, _ = calendar_page
    navigation = page.get_by_role("navigation", name="Secciones")
    labels = ["Mi calendario", "Espacios compartidos", "Mensajes", "Mi cuenta"]
    page.goto(f"{base_url}/tareas")
    playwright.expect(navigation.get_by_role("link")).to_have_text(labels)
    for name, route in (("Espacios compartidos", "espacios"), ("Mensajes", "mensajes"), ("Mi cuenta", "cuenta")):
        navigation.get_by_role("link", name=name, exact=True).click()
        playwright.expect(page).to_have_url(f"{base_url}/{route}")
        playwright.expect(navigation.get_by_role("link")).to_have_text(labels)
        playwright.expect(navigation.get_by_role("link", name=name, exact=True)).to_have_attribute("aria-current", "page")
    playwright.expect(page.get_by_role("heading", name="Mi cuenta")).to_be_visible()

    new_password = "Contraseña nueva del panel 8"
    page.get_by_label("Contraseña actual").fill(PASSWORD)
    page.get_by_label("Contraseña nueva", exact=True).fill(new_password)
    page.get_by_label("Confirma la contraseña nueva").fill("Otra contraseña distinta")
    page.get_by_role("button", name="Guardar contraseña").click()
    playwright.expect(page.get_by_role("alert")).to_contain_text("La confirmación debe coincidir")
    page.get_by_label("Contraseña actual").fill(PASSWORD)
    page.get_by_label("Contraseña nueva", exact=True).fill(new_password)
    page.get_by_label("Confirma la contraseña nueva").fill(new_password)
    page.get_by_role("button", name="Guardar contraseña").click()
    playwright.expect(page.locator(".account-success")).to_contain_text("Contraseña actualizada")

    page.get_by_role("button", name="Cerrar mi sesión").click()
    page.goto(f"{base_url}/cuenta")
    playwright.expect(page).to_have_url(f"{base_url}/iniciar-sesion?volver=cuenta")
    page.get_by_label("Correo electrónico").fill("calendario@example.com")
    page.get_by_label("Contraseña", exact=True).fill(new_password)
    page.get_by_role("button", name="Iniciar sesión").click()
    playwright.expect(page).to_have_url(f"{base_url}/cuenta")
    page.set_viewport_size({"width": 390, "height": 840})
    assert page.evaluate("document.documentElement.scrollWidth === window.innerWidth")

    confirmation = page.get_by_role("dialog", name="¿Quieres eliminar tu cuenta?")
    page.get_by_role("button", name="Eliminar mi cuenta").click()
    playwright.expect(confirmation).to_be_visible()
    confirmation.get_by_role("button", name="Cancelar").click()
    playwright.expect(confirmation).to_be_hidden()
    page.get_by_role("button", name="Eliminar mi cuenta").click()
    confirmation.get_by_label("Contraseña para confirmar").fill("Incorrecta")
    confirmation.get_by_role("button", name="Eliminar definitivamente").click()
    playwright.expect(confirmation.get_by_role("alert")).to_contain_text("La contraseña actual no coincide.")
    confirmation.get_by_label("Contraseña para confirmar").fill(new_password)
    confirmation.get_by_role("button", name="Eliminar definitivamente").click()
    playwright.expect(page.get_by_role("status")).to_contain_text("Tu cuenta se ha eliminado.")
    assert api.post("/api/auth/login", json={"email": "calendario@example.com", "password": new_password}).status_code == 401


def test_browser_shared_workspace_invitation_and_discussion(calendar_page):
    page, api, base_url, _, _ = calendar_page
    register_and_login(api, "equipo@example.com")
    register_and_login(api, "otra@example.com")
    page.goto(f"{base_url}/tareas")
    page.get_by_role("link", name="Espacios compartidos").click()
    page.locator("#empty-create-workspace").click()
    page.get_by_label("Nombre del espacio").fill("Proyecto en común")
    page.locator("#workspace-form").get_by_role("button", name="Crear espacio").click()
    playwright.expect(page.get_by_role("heading", name="Proyecto en común")).to_be_visible()
    page.get_by_role("button", name="Nueva tarea").click()
    page.get_by_label("Título", exact=True).fill("Preparar la entrega")
    page.get_by_role("button", name="Guardar tarea").click()
    playwright.expect(page.locator("[data-column='todo']")).to_contain_text("Preparar la entrega")
    file_name = "Notas del equipo.xyz"
    file_content = b"Proyecto\x00\xff"
    page.get_by_role("button", name="Preparar la entrega", exact=True).click()
    page.get_by_label("Archivo para compartir").set_input_files({
        "name": file_name, "mimeType": "application/octet-stream", "buffer": file_content,
    })
    page.get_by_role("button", name="Adjuntar archivo").click()
    playwright.expect(page.locator("#attachment-list")).to_contain_text(file_name)
    page.locator("#card-dialog").get_by_role("button", name="Cerrar", exact=True).click()
    page.get_by_role("button", name="Nueva tarea").click()
    page.get_by_label("Título", exact=True).fill("Revisar el borrador")
    page.get_by_role("button", name="Guardar tarea").click()
    todo = page.locator("[data-column='todo'] .kanban-card")
    playwright.expect(todo.locator(".kanban-card-title")).to_have_text(["Preparar la entrega", "Revisar el borrador"])
    todo.nth(1).drag_to(todo.nth(0), target_position={"x": 20, "y": 8})
    playwright.expect(todo.locator(".kanban-card-title")).to_have_text(["Revisar el borrador", "Preparar la entrega"])
    page.reload()
    playwright.expect(todo.locator(".kanban-card-title")).to_have_text(["Revisar el borrador", "Preparar la entrega"])

    page.get_by_role("button", name="Invitar", exact=True).click()
    page.get_by_label("Correo electrónico").fill("equipo@example.com")
    page.get_by_role("button", name="Enviar invitación").click()
    playwright.expect(page.locator("#invitation-result")).to_contain_text("La invitación está en Mensajes")
    page.locator("#invite-form").get_by_role("button", name="Cerrar", exact=True).last.click()

    context = page.context.browser.new_context(viewport={"width": 400, "height": 850})
    try:
        guest = context.new_page()
        guest.goto(f"{base_url}/mensajes")
        playwright.expect(guest).to_have_url(f"{base_url}/iniciar-sesion?volver=mensajes")
        playwright.expect(guest.get_by_role("heading", name="Iniciar sesión")).to_be_visible()
        assert guest.get_by_role("button", name="Aceptar", exact=True).count() == 0
        guest.get_by_label("Correo electrónico").fill("equipo@example.com")
        guest.get_by_label("Contraseña", exact=True).fill(PASSWORD)
        guest.get_by_role("button", name="Iniciar sesión").click()
        playwright.expect(guest.get_by_role("heading", name="Mensajes")).to_be_visible()
        guest.get_by_role("button", name="Aceptar", exact=True).click()
        playwright.expect(guest.get_by_role("heading", name="Proyecto en común", level=1)).to_be_visible()
        board_url = guest.url
        assert guest.evaluate("document.documentElement.scrollWidth === window.innerWidth")
        playwright.expect(guest.get_by_label("Mover Preparar la entrega")).to_be_disabled()
        playwright.expect(guest.get_by_role("button", name="Permisos")).to_be_hidden()
        guest.get_by_role("button", name="Preparar la entrega", exact=True).click()
        with guest.expect_download() as download_info:
            guest.get_by_role("button", name="Descargar " + file_name).click()
        assert download_info.value.suggested_filename == file_name
        assert download_info.value.path().read_bytes() == file_content
        guest.locator("#card-dialog").get_by_role("button", name="Cerrar", exact=True).click()
        page.bring_to_front()
        page.get_by_role("button", name="Actualizar", exact=True).click()
        playwright.expect(page.locator("#workspace-members")).to_contain_text("equipo@example.com")
        page.get_by_role("button", name="Permisos").click()
        page.get_by_role("checkbox", name="equipo@example.com").check()
        playwright.expect(page.locator("#permissions-status")).to_contain_text("Permiso actualizado")
        page.locator("#permissions-dialog").get_by_role("button", name="Cerrar").click()
        guest.bring_to_front()
        guest.get_by_role("button", name="Actualizar", exact=True).click()
        guest.get_by_label("Mover Preparar la entrega").select_option("doing")
        playwright.expect(guest.locator("[data-column='doing']")).to_contain_text("Preparar la entrega")
        guest.get_by_role("button", name="Comentarios de Preparar la entrega").click()
        guest.get_by_label("Escribe un comentario").fill("He revisado la documentación.")
        guest.get_by_role("button", name="Publicar comentario").click()
        playwright.expect(guest.locator("#comment-list")).to_contain_text("He revisado la documentación.")
        guest.locator("#card-dialog").get_by_role("button", name="Cerrar", exact=True).click()
        other_tab = context.new_page()
        other_tab.goto(f"{base_url}/mensajes")
        other_tab.get_by_role("button", name="Cerrar sesión").click()
        playwright.expect(other_tab.get_by_role("heading", name="Iniciar sesión")).to_be_visible()
        guest.bring_to_front()
        playwright.expect(guest.get_by_role("heading", name="Iniciar sesión")).to_be_visible()
        assert guest.get_by_role("button", name="Preparar la entrega").count() == 0
        assert guest.request.get(f"{base_url}/api/messages").status == 401
        guest.get_by_label("Correo electrónico").fill("equipo@example.com")
        guest.get_by_label("Contraseña", exact=True).fill(PASSWORD)
        guest.get_by_role("button", name="Iniciar sesión").click()
        playwright.expect(guest).to_have_url(board_url)
        playwright.expect(guest.get_by_role("heading", name="Proyecto en común")).to_be_visible()

        other_tab.goto(f"{base_url}/iniciar-sesion")
        other_tab.get_by_label("Correo electrónico").fill("otra@example.com")
        other_tab.get_by_label("Contraseña", exact=True).fill(PASSWORD)
        other_tab.get_by_role("button", name="Iniciar sesión").click()
        playwright.expect(other_tab.locator(".account-email")).to_have_text("otra@example.com")
        guest.bring_to_front()
        playwright.expect(guest.get_by_role("heading", name="Espacio no disponible")).to_be_visible()
        assert guest.get_by_role("button", name="Preparar la entrega").count() == 0
    finally:
        context.close()

    page.get_by_role("button", name="Actualizar", exact=True).click()
    playwright.expect(page.locator("[data-column='doing']")).to_contain_text("Preparar la entrega")
    page.get_by_role("button", name="Comentarios de Preparar la entrega").click()
    playwright.expect(page.locator("#comment-list")).to_contain_text("He revisado la documentación.")
    playwright.expect(page.locator("#comment-list")).to_contain_text("equipo@example.com")
    page.locator("#card-dialog").get_by_role("button", name="Cerrar", exact=True).click()
    page.locator("[data-column='doing'] .kanban-card").drag_to(page.locator("[data-column='done'] .column-empty"))
    playwright.expect(page.locator("[data-column='done']")).to_contain_text("Preparar la entrega")


def test_browser_friends_and_private_chat(calendar_page):
    page, api, base_url, _, _ = calendar_page
    register_and_login(api, "amiga@example.com")
    page.goto(f"{base_url}/mensajes")
    page.get_by_label("Correo de una cuenta registrada").fill("amiga@example.com")
    page.get_by_role("button", name="Añadir amigo").click()
    playwright.expect(page.locator("#friend-requests")).to_contain_text("amiga@example.com")
    playwright.expect(page.get_by_role("button", name="Abrir chat con amiga@example.com")).to_have_count(0)

    context = page.context.browser.new_context(viewport={"width": 390, "height": 840})
    try:
        friend = context.new_page()
        friend.goto(f"{base_url}/mensajes")
        friend.get_by_label("Correo electrónico").fill("amiga@example.com")
        friend.get_by_label("Contraseña", exact=True).fill(PASSWORD)
        friend.get_by_role("button", name="Iniciar sesión").click()
        friend.get_by_role("button", name="Aceptar solicitud").click()
        friend.get_by_role("button", name="Abrir chat con calendario@example.com").click()
        friend.get_by_label("Escribe un mensaje").fill("¡Hacemos equipo!")
        friend.get_by_role("button", name="Enviar mensaje").click()
        playwright.expect(friend.locator("#chat-messages")).to_contain_text("¡Hacemos equipo!")
        friend.get_by_label("Adjuntar archivo").set_input_files({
            "name": "Imagen del equipo.png", "mimeType": "image/png", "buffer": TEST_IMAGE,
        })
        playwright.expect(friend.locator("#chat-file-name")).to_contain_text("Imagen del equipo.png")
        friend.get_by_role("button", name="Enviar archivo").click()
        playwright.expect(friend.get_by_role("img", name="Vista previa de Imagen del equipo.png")).to_be_visible()
        assert friend.evaluate("document.documentElement.scrollWidth === window.innerWidth")
        assert friend.locator(".chat-panel").evaluate("panel => panel.clientHeight >= window.innerHeight - 1")

        page.bring_to_front()
        page.get_by_role("button", name="Actualizar", exact=True).click()
        page.get_by_role("button", name="Abrir chat con amiga@example.com").click()
        playwright.expect(page.locator("#chat-messages")).to_contain_text("¡Hacemos equipo!")
        preview = page.get_by_role("img", name="Vista previa de Imagen del equipo.png")
        playwright.expect(preview).to_be_visible()
        page.wait_for_function("() => document.querySelector('.chat-attachment-preview')?.naturalWidth > 0")
        with page.expect_download() as download_info:
            page.get_by_role("button", name="Descargar Imagen del equipo.png").click()
        assert download_info.value.suggested_filename == "Imagen del equipo.png"
        assert download_info.value.path().read_bytes() == TEST_IMAGE
        friend.bring_to_front()
        friend.get_by_role("button", name="Volver a amigos").click()
        friend.get_by_role("button", name="Cerrar sesión").click()
        playwright.expect(friend.get_by_role("heading", name="Iniciar sesión")).to_be_visible()
        assert friend.request.get(f"{base_url}/api/chats/1").status == 401
    finally:
        context.close()


def test_unread_popup_and_badges_clear_after_opening_chat(calendar_page):
    page, api, base_url, _, auth = calendar_page
    sender = {"Authorization": f"Bearer {register_and_login(api, 'ana@example.com')}"}
    invited = api.post("/api/friends", headers=sender, json={"email": "calendario@example.com"})
    friendship = invited.json()["friendship_id"]
    assert api.post(f"/api/friends/{friendship}/accept", headers=auth).status_code == 204
    for body in ("Primera nota", "Segunda nota"):
        assert api.post(f"/api/chats/{friendship}", headers=sender, json={"body": body}).status_code == 201

    page.goto(base_url)
    modal = page.get_by_role("dialog", name="Mensajes pendientes de leer")
    playwright.expect(modal).to_be_visible()
    playwright.expect(modal).to_contain_text("Tienes 2 mensajes sin leer.")
    playwright.expect(page.locator("[data-unread-dot]")).to_be_visible()
    modal.get_by_role("link", name="Ver mensajes").click()
    playwright.expect(page).to_have_url(f"{base_url}/mensajes")
    playwright.expect(page.locator("[data-unread-chat]")).to_have_text("2")
    playwright.expect(modal).to_be_hidden()
    assert page.locator(".inbox").evaluate("inbox => inbox.clientWidth > 1050")

    page.get_by_role("button", name="Abrir chat con ana@example.com").click()
    playwright.expect(page.locator("#chat-messages")).to_contain_text("Segunda nota")
    playwright.expect(page.locator("[data-unread-dot]")).to_be_hidden()
    playwright.expect(page.locator("[data-unread-chat]")).to_be_hidden()
    assert page.locator("#chat-history").evaluate("history => history.clientHeight > 380")
    assert api.get("/api/chats/unread", headers=auth).json() == {"total": 0, "chats": []}
