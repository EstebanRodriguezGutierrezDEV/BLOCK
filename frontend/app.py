from http.client import HTTPConnection, HTTPException, HTTPSConnection
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from flask import Flask, abort, redirect, render_template, request, send_from_directory, url_for

PUBLIC_DIR = Path(__file__).resolve().parents[1] / "public"
app = Flask(__name__, static_folder=PUBLIC_DIR / "static", static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024
SESSION_COOKIE = "todo_session"
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")


def api_request(path, method="GET", data=None, token=None):
    """Llama a FastAPI sin entregar el token de sesión a JavaScript."""
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


@app.before_request
def require_same_origin_for_writes():
    if request.method not in {"POST", "PATCH", "DELETE"}:
        return
    # Los formularios y las peticiones del navegador incluyen Origin al escribir.
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


def auth_form(mode, **kwargs):
    return render_template("auth.html", mode=mode, **kwargs)


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
        return redirect(url_for("login", cuenta_creada="1"), code=303)
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
    response = redirect(url_for("tasks"), code=303)
    secure = (
        app.config.get("TODO_EMBEDDED_API", False)
        or request.is_secure
        or os.environ.get("TODO_PUBLIC_URL", "").startswith("https://")
    )
    response.set_cookie(
        SESSION_COOKIE, payload["token"], max_age=payload["expires_in"],
        httponly=True, secure=secure, samesite="Lax",
    )
    return response


@app.post("/cerrar-sesion")
def logout():
    if token := session_token():
        status, payload = json_api("/api/auth/logout", {}, token=token)
        if status not in {204, 401}:
            return render_template("service_error.html", error=error_message(payload)), status
    response = redirect(url_for("login"), code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.get("/tareas")
def tasks():
    token = session_token()
    if not token:
        return redirect(url_for("login"))
    status, payload = json_api("/api/auth/me", token=token)
    if status == 401:
        response = redirect(url_for("login"))
        response.delete_cookie(SESSION_COOKIE)
        return response
    if status != 200:
        return render_template("service_error.html", error=error_message(payload)), status
    return render_template("tasks.html", user=payload)


@app.route("/api/tasks", methods=["GET", "POST"])
@app.route("/api/tasks/<int:task_id>", methods=["GET", "PATCH", "DELETE"])
def task_api(task_id=None):
    token = session_token()
    if not token:
        return {"detail": "Inicia sesión para acceder a tus tareas."}, 401
    if request.method in {"POST", "PATCH"} and not request.is_json:
        return {"detail": "La petición debe utilizar JSON."}, 415
    path = "/api/tasks" if task_id is None else f"/api/tasks/{task_id}"
    method = "GET" if request.method == "HEAD" else request.method
    body = request.get_data() if method in {"POST", "PATCH"} else None
    status, content = api_request(path, method, body, token)
    return app.response_class(content, status=status, mimetype="application/json")


if __name__ == "__main__":
    app.run(debug=True, port=5000)
