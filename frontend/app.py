from hashlib import sha256
from http.client import HTTPConnection, HTTPException, HTTPSConnection
import json
import os
from pathlib import Path
import re
from urllib.parse import urlencode, urlsplit

from flask import Flask, abort, redirect, render_template, request, send_from_directory, url_for

PUBLIC_DIR = Path(__file__).resolve().parents[1] / "public"
app = Flask(__name__, static_folder=PUBLIC_DIR / "static", static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024
SESSION_COOKIE = "todo_session"
SESSION_VIEW_COOKIE = "todo_session_view"
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")


def api_request(path, method="GET", data=None, token=None):
    if app.config.get("TODO_EMBEDDED_API"):
        from .embedded_api import request_api

        return request_api(path, method, data, token)
    api = urlsplit(os.environ.get("TODO_API_URL", "http://127.0.0.1:8000"))
    connection_type = HTTPSConnection if api.scheme == "https" else HTTPConnection
    connection = connection_type(api.hostname, api.port, timeout=15)
    headers = {}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        connection.request(method, api.path.rstrip("/") + path, body=data, headers=headers)
        response = connection.getresponse()
        return response.status, response.read()
    except (OSError, HTTPException):
        return 503, json.dumps({"detail": "No se pudo conectar con el servidor de tareas."}).encode()
    finally:
        connection.close()


def json_api(path, data=None, *, token=None):
    method = "GET" if data is None else "POST"
    body = None if data is None else json.dumps(data).encode()
    status, content = api_request(path, method, body, token)
    try:
        return status, json.loads(content) if content else {}
    except ValueError:
        return 503, {"detail": "No se pudo conectar con el servidor de tareas."}


def session_token():
    token = request.cookies.get(SESSION_COOKIE, "")
    return token if TOKEN_PATTERN.fullmatch(token) else None


def secure_cookie():
    return bool(
        app.config.get("TODO_EMBEDDED_API")
        or request.is_secure
        or os.environ.get("TODO_PUBLIC_URL", "").startswith("https://")
    )


def session_view(token):
    return sha256(token.encode()).hexdigest()[:32]


def clear_session_cookies(response):
    response.delete_cookie(SESSION_COOKIE, httponly=True, secure=secure_cookie(), samesite="Lax")
    response.delete_cookie(SESSION_VIEW_COOKIE, secure=secure_cookie(), samesite="Lax")
    return response


@app.before_request
def require_same_origin_for_writes():
    if request.method not in {"POST", "PATCH", "DELETE"}:
        return
    origin = os.environ.get("TODO_PUBLIC_URL", request.host_url).rstrip("/")
    if request.headers.get("Origin") != origin:
        abort(403)


@app.after_request
def prevent_private_caching(response):
    if request.endpoint != "static":
        response.headers["Cache-Control"] = "no-store"
    return response


def error_message(payload):
    return payload.get("detail", "No se pudo completar la operación. Inténtalo de nuevo.")


def auth_destination():
    if request.args.get("volver") == "mensajes" or request.args.get("invitacion") == "1":
        return {"volver": "mensajes"}
    if request.args.get("volver") == "espacios":
        workspace_id = request.args.get("espacio", type=int)
        return {"volver": "espacios", "espacio": workspace_id if 0 < (workspace_id or 0) < 2**63 else None}
    return {}


def auth_form(mode, **kwargs):
    return render_template("auth.html", mode=mode, destination=auth_destination(), **kwargs)


@app.get("/")
def index():
    user = None
    if token := session_token():
        status, payload = json_api("/api/auth/me", token=token)
        if status == 200:
            user = payload
    return render_template("index.html", user=user)


@app.get("/favicon.ico")
def favicon():
    return send_from_directory(PUBLIC_DIR, "favicon.ico")


@app.route("/registro", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        return auth_form("register")
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")
    status, payload = json_api("/api/auth/register", {"email": email, "password": password})
    if status == 201:
        return redirect(url_for("login", cuenta_creada="1", **auth_destination()), code=303)
    return auth_form("register", email=email, error=error_message(payload)), status


@app.route("/iniciar-sesion", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return auth_form("login", account_created=request.args.get("cuenta_creada") == "1")
    email = request.form.get("email", "").strip()
    status, payload = json_api("/api/auth/login", {"email": email, "password": request.form.get("password", "")})
    if status != 200:
        return auth_form("login", email=email, error=error_message(payload)), status

    if token := session_token():
        json_api("/api/auth/logout", {}, token=token)
    destination = auth_destination()
    if destination.get("volver") == "mensajes":
        landing = url_for("messages")
    elif destination.get("volver") == "espacios":
        landing = url_for("workspaces", workspace_id=destination["espacio"])
    else:
        landing = url_for("tasks")
    response = redirect(landing, code=303)
    secure = secure_cookie()
    response.set_cookie(
        SESSION_COOKIE, payload["token"], max_age=payload["expires_in"],
        httponly=True, secure=secure, samesite="Lax",
    )
    response.set_cookie(
        SESSION_VIEW_COOKIE, session_view(payload["token"]), max_age=payload["expires_in"],
        secure=secure, samesite="Lax",
    )
    return response


@app.post("/cerrar-sesion")
def logout():
    if token := session_token():
        status, _ = json_api("/api/auth/logout", {}, token=token)
        if status not in {204, 401}:
            page = render_template(
                "service_error.html", title="Sesión cerrada en este navegador",
                error="El servidor no pudo confirmar la revocación de la sesión.",
            )
            return clear_session_cookies(app.make_response((page, status)))
    return clear_session_cookies(redirect(url_for("login"), code=303))


def protected_page(template, *, sign_in=None, workspace_id=None):
    sign_in = sign_in or url_for("login")
    token = session_token()
    if not token:
        return redirect(sign_in)

    status, user = json_api("/api/auth/me", token=token)
    error = user
    if status == 200 and workspace_id is not None:
        if not 0 < workspace_id < 2**63:
            abort(404)
        status, error = json_api(f"/api/workspaces/{workspace_id}", token=token)
    if status == 401:
        return clear_session_cookies(redirect(sign_in))
    if status == 404:
        return render_template("service_error.html", title="Espacio no disponible", error=error_message(error)), 404
    if status != 200:
        return render_template("service_error.html", error=error_message(error)), status
    view = session_view(token)
    response = app.make_response(render_template(template, user=user, workspace_id=workspace_id, session_view=view))
    if request.cookies.get(SESSION_VIEW_COOKIE) != view:
        response.set_cookie(SESSION_VIEW_COOKIE, view, secure=secure_cookie(), samesite="Lax")
    return response


@app.get("/tareas")
def tasks():
    return protected_page("tasks.html")


@app.get("/espacios")
@app.get("/espacios/<int:workspace_id>")
def workspaces(workspace_id=None):
    sign_in = url_for("login", volver="espacios", espacio=workspace_id)
    return protected_page("workspaces.html", sign_in=sign_in, workspace_id=workspace_id)


@app.get("/invitacion")
def invitation():
    return redirect(url_for("messages"))


@app.get("/mensajes")
def messages():
    return protected_page("messages.html", sign_in=url_for("login", volver="mensajes"))


def proxy_api(path):
    token = session_token()
    if not token:
        return {"detail": "Inicia sesión para acceder a tus tareas."}, 401
    if request.method in {"POST", "PATCH"} and not request.is_json:
        return {"detail": "La petición debe utilizar JSON."}, 415
    method = "GET" if request.method == "HEAD" else request.method
    body = request.get_data() if method in {"POST", "PATCH"} else None
    status, content = api_request(path, method, body, token)
    return app.response_class(content, status=status, mimetype="application/json")


def task_workspace_query():
    workspace_id = request.args.get("workspace_id")
    return "?" + urlencode({"workspace_id": workspace_id}) if workspace_id is not None else ""


@app.route("/api/tasks", methods=["GET", "POST"])
@app.route("/api/tasks/<int:task_id>", methods=["GET", "PATCH", "DELETE"])
def task_api(task_id=None):
    path = "/api/tasks" if task_id is None else f"/api/tasks/{task_id}"
    return proxy_api(path + task_workspace_query())


@app.route("/api/tasks/<int:task_id>/comments", methods=["GET", "POST"])
def comments_api(task_id):
    return proxy_api(f"/api/tasks/{task_id}/comments" + task_workspace_query())


@app.route("/api/workspaces", methods=["GET", "POST"])
@app.get("/api/workspaces/<int:workspace_id>")
def workspace_api(workspace_id=None):
    path = "/api/workspaces" if workspace_id is None else f"/api/workspaces/{workspace_id}"
    return proxy_api(path)


@app.post("/api/workspaces/<int:workspace_id>/invitations")
def invite_api(workspace_id):
    return proxy_api(f"/api/workspaces/{workspace_id}/invitations")


@app.get("/api/messages")
def messages_api():
    return proxy_api("/api/messages")


@app.post("/api/messages/<invitation_id>/<action>")
def respond_message_api(invitation_id, action):
    if not re.fullmatch(r"[a-f0-9]{64}", invitation_id) or action not in {"accept", "reject"}:
        abort(404)
    return proxy_api(f"/api/messages/{invitation_id}/{action}")


if __name__ == "__main__":
    app.run(debug=True, port=5000)
