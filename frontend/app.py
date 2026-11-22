from hashlib import sha256
from http.client import HTTPConnection, HTTPException, HTTPSConnection
from ipaddress import ip_address
import json
import os
from pathlib import Path
import re
from urllib.parse import urlencode, urlsplit

from flask import Flask, abort, redirect, render_template, request, send_from_directory, url_for
from werkzeug.exceptions import RequestEntityTooLarge

from backend.attachment_policy import ATTACHMENT_LIMIT_MESSAGE, MAX_ATTACHMENT_BYTES, PREVIEW_IMAGE_TYPES
from backend.request_security import MAX_JSON_BYTES, MAX_RECORD_ID, SESSION_TOKEN_PATTERN

PUBLIC_DIR = Path(__file__).resolve().parents[1] / "public"
app = Flask(__name__, static_folder=PUBLIC_DIR / "static", static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = MAX_JSON_BYTES
SESSION_COOKIE = "todo_session"
SESSION_VIEW_COOKIE = "todo_session_view"
CONTENT_SECURITY_POLICY = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; "
    "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)


def client_address():
    source = request.remote_addr
    if app.config.get("TODO_EMBEDDED_API") and os.environ.get("VERCEL") == "1":
        source = request.headers.get("X-Vercel-Forwarded-For") or source
    try:
        return str(ip_address(source)).split("%", 1)[0]
    except ValueError:
        return None


def api_request(path, method="GET", data=None, token=None, *, content_type="application/json"):
    client_ip = client_address()
    if app.config.get("TODO_EMBEDDED_API"):
        from .embedded_api import request_api

        return request_api(path, method, data, token, content_type=content_type, client_ip=client_ip)
    api = urlsplit(os.environ.get("TODO_API_URL", "http://127.0.0.1:8000"))
    connection_type = HTTPSConnection if api.scheme == "https" else HTTPConnection
    connection = connection_type(api.hostname, api.port, timeout=15)
    headers = {}
    if data is not None:
        headers["Content-Type"] = content_type
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if client_ip:
        headers["X-Forwarded-For"] = client_ip
    try:
        connection.request(method, api.path.rstrip("/") + path, body=data, headers=headers)
        response = connection.getresponse()
        return response.status, response.read(), {name.lower(): value for name, value in response.getheaders()}
    except (OSError, HTTPException):
        return 503, json.dumps({"detail": "No se pudo conectar con el servidor de tareas."}).encode(), {}
    finally:
        connection.close()


def json_api(path, data=None, *, token=None):
    method = "GET" if data is None else "POST"
    body = None if data is None else json.dumps(data).encode()
    status, content, headers = api_request(path, method, body, token)
    try:
        return status, json.loads(content) if content else {}, headers
    except ValueError:
        return 503, {"detail": "No se pudo conectar con el servidor de tareas."}, {}


def session_token():
    token = request.cookies.get(SESSION_COOKIE, "")
    return token if SESSION_TOKEN_PATTERN.fullmatch(token) else None


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
    origin = (os.environ.get("TODO_PUBLIC_URL") or request.host_url).strip().rstrip("/")
    if request.headers.get("Origin") != origin or request.headers.get("Sec-Fetch-Site") == "cross-site":
        abort(403)


@app.after_request
def secure_response(response):
    if request.endpoint != "static":
        response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if request.is_secure:
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    return response


def error_message(payload):
    return payload.get("detail", "No se pudo completar la operación. Inténtalo de nuevo.")


def retry_headers(headers):
    retry = headers.get("retry-after")
    return {"Retry-After": retry} if retry else {}


def auth_destination():
    if request.args.get("volver") == "mensajes" or request.args.get("invitacion") == "1":
        return {"volver": "mensajes"}
    if request.args.get("volver") == "espacios":
        workspace_id = request.args.get("espacio", type=int)
        return {"volver": "espacios", "espacio": workspace_id if 0 < (workspace_id or 0) <= MAX_RECORD_ID else None}
    return {}


def auth_form(mode, **kwargs):
    return render_template("auth.html", mode=mode, destination=auth_destination(), **kwargs)


@app.get("/")
def index():
    if token := session_token():
        status, user, _ = json_api("/api/auth/me", token=token)
        if status == 200:
            return render_session_page("index.html", user, token, url_for("index"))
    return render_template("index.html")


@app.get("/favicon.ico")
def favicon():
    return send_from_directory(PUBLIC_DIR, "favicon.ico")


@app.route("/registro", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        return auth_form("register")
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")
    status, payload, headers = json_api("/api/auth/register", {"email": email, "password": password})
    if status == 201:
        return redirect(url_for("login", cuenta_creada="1", **auth_destination()), code=303)
    return auth_form("register", email=email, error=error_message(payload)), status, retry_headers(headers)


@app.route("/iniciar-sesion", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return auth_form("login", account_created=request.args.get("cuenta_creada") == "1")
    email = request.form.get("email", "").strip()
    status, payload, headers = json_api("/api/auth/login", {"email": email, "password": request.form.get("password", "")})
    if status != 200:
        return auth_form("login", email=email, error=error_message(payload)), status, retry_headers(headers)

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
        status, _, _ = json_api("/api/auth/logout", {}, token=token)
        if status not in {204, 401}:
            page = render_template(
                "service_error.html", title="Sesión cerrada en este navegador",
                error="El servidor no pudo confirmar la revocación de la sesión.",
            )
            return clear_session_cookies(app.make_response((page, status)))
    return clear_session_cookies(redirect(url_for("login"), code=303))


def render_session_page(template, user, token, sign_in, **context):
    view = session_view(token)
    response = app.make_response(render_template(
        template, user=user, session_view=view, session_sign_in=sign_in, **context,
    ))
    if request.cookies.get(SESSION_VIEW_COOKIE) != view:
        response.set_cookie(SESSION_VIEW_COOKIE, view, secure=secure_cookie(), samesite="Lax")
    return response


def protected_page(template, *, sign_in=None, workspace_id=None):
    sign_in = sign_in or url_for("login")
    token = session_token()
    if not token:
        return redirect(sign_in)

    status, user, _ = json_api("/api/auth/me", token=token)
    error = user
    if status == 200 and workspace_id is not None:
        if not 0 < workspace_id <= MAX_RECORD_ID:
            abort(404)
        status, error, _ = json_api(f"/api/workspaces/{workspace_id}", token=token)
    if status == 401:
        return clear_session_cookies(redirect(sign_in))
    if status == 404:
        return render_template("service_error.html", title="Espacio no disponible", error=error_message(error)), 404
    if status != 200:
        return render_template("service_error.html", error=error_message(error)), status
    return render_session_page(
        template, user, token, sign_in, workspace_id=workspace_id,
        attachment_limit_bytes=MAX_ATTACHMENT_BYTES, attachment_limit_message=ATTACHMENT_LIMIT_MESSAGE,
    )


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


def proxy_api(path, *, binary_upload=False, file_response=False):
    token = session_token()
    if not token:
        return {"detail": "Inicia sesión para acceder a tus tareas."}, 401
    view = request.headers.get("X-Todo-Session-View")
    if view is not None and view != session_view(token):
        return {"detail": "La sesión ha cambiado."}, 409, {"X-Todo-Session-Changed": "1"}
    method = "GET" if request.method == "HEAD" else request.method
    body = None
    content_type = "application/json"
    if method in {"POST", "PATCH"}:
        if binary_upload:
            if request.mimetype != "application/octet-stream":
                return {"detail": "Envía el archivo como datos binarios."}, 415
            content_type = "application/octet-stream"
            request.max_content_length = MAX_ATTACHMENT_BYTES
            try:
                body = request.get_data()
            except RequestEntityTooLarge:
                return {"detail": ATTACHMENT_LIMIT_MESSAGE}, 413
        elif request.is_json:
            body = request.get_data()
        else:
            return {"detail": "La petición debe utilizar JSON."}, 415
    status, content, headers = api_request(path, method, body, token, content_type=content_type)
    if file_response and status == 200:
        media_type = headers.get("content-type")
        if media_type not in PREVIEW_IMAGE_TYPES and media_type != "application/octet-stream":
            return {"detail": "El formato del archivo no está disponible."}, 502
        return app.response_class(content, mimetype=media_type, headers={
            "Content-Disposition": headers.get("content-disposition", "attachment"),
            "X-Content-Type-Options": "nosniff",
            "Cross-Origin-Resource-Policy": "same-origin",
        })
    return app.response_class(content, status=status, mimetype="application/json", headers=retry_headers(headers))


def task_workspace_query():
    workspace_id = request.args.get("workspace_id")
    return "?" + urlencode({"workspace_id": workspace_id}) if workspace_id is not None else ""


@app.get("/api/calendar/tasks")
def calendar_tasks_api():
    before_id = request.args.get("before_id")
    query = "?" + urlencode({"before_id": before_id}) if before_id is not None else ""
    return proxy_api("/api/calendar/tasks" + query)


@app.route("/api/tasks", methods=["GET", "POST"])
@app.route("/api/tasks/<int:task_id>", methods=["GET", "PATCH", "DELETE"])
def task_api(task_id=None):
    path = "/api/tasks" if task_id is None else f"/api/tasks/{task_id}"
    return proxy_api(path + task_workspace_query())


@app.route("/api/tasks/<int:task_id>/comments", methods=["GET", "POST"])
def comments_api(task_id):
    return proxy_api(f"/api/tasks/{task_id}/comments" + task_workspace_query())


@app.route("/api/tasks/<int:task_id>/attachments", methods=["GET", "POST"])
def attachments_api(task_id):
    filters = {name: request.args[name] for name in ("workspace_id", "file_name") if name in request.args}
    query = "?" + urlencode(filters) if filters else ""
    return proxy_api(f"/api/tasks/{task_id}/attachments" + query, binary_upload=True)


@app.get("/api/tasks/<int:task_id>/attachments/<int:attachment_id>")
def download_attachment_api(task_id, attachment_id):
    return proxy_api(f"/api/tasks/{task_id}/attachments/{attachment_id}" + task_workspace_query(), file_response=True)


@app.patch("/api/tasks/<int:task_id>/position")
def task_position_api(task_id):
    return proxy_api(f"/api/tasks/{task_id}/position" + task_workspace_query())


@app.route("/api/workspaces", methods=["GET", "POST"])
@app.get("/api/workspaces/<int:workspace_id>")
def workspace_api(workspace_id=None):
    path = "/api/workspaces" if workspace_id is None else f"/api/workspaces/{workspace_id}"
    return proxy_api(path)


@app.post("/api/workspaces/<int:workspace_id>/invitations")
def invite_api(workspace_id):
    return proxy_api(f"/api/workspaces/{workspace_id}/invitations")


@app.patch("/api/workspaces/<int:workspace_id>/members/<int:member_id>")
def member_permissions_api(workspace_id, member_id):
    return proxy_api(f"/api/workspaces/{workspace_id}/members/{member_id}")


@app.get("/api/messages")
def messages_api():
    return proxy_api("/api/messages")


@app.post("/api/messages/<invitation_id>/<action>")
def respond_message_api(invitation_id, action):
    if not re.fullmatch(r"[a-f0-9]{64}", invitation_id) or action not in {"accept", "reject"}:
        abort(404)
    return proxy_api(f"/api/messages/{invitation_id}/{action}")


@app.route("/api/friends", methods=["GET", "POST"])
def friends_api():
    return proxy_api("/api/friends")


@app.post("/api/friends/<int:friendship_id>/<action>")
def respond_friend_api(friendship_id, action):
    if action not in {"accept", "reject", "cancel"}:
        abort(404)
    return proxy_api(f"/api/friends/{friendship_id}/{action}")


@app.route("/api/chats/<int:friendship_id>", methods=["GET", "POST"])
def chat_api(friendship_id):
    filters = {name: request.args[name] for name in ("before_id", "after_id") if name in request.args}
    query = "?" + urlencode(filters) if filters else ""
    return proxy_api(f"/api/chats/{friendship_id}{query}")


@app.get("/api/chats/unread")
def unread_messages_api():
    return proxy_api("/api/chats/unread")


@app.post("/api/chats/<int:friendship_id>/read")
def mark_chat_read_api(friendship_id):
    return proxy_api(f"/api/chats/{friendship_id}/read")


@app.post("/api/chats/<int:friendship_id>/attachments")
def upload_chat_attachment_api(friendship_id):
    file_name = request.args.get("file_name")
    query = "?" + urlencode({"file_name": file_name}) if file_name is not None else ""
    return proxy_api(f"/api/chats/{friendship_id}/attachments" + query, binary_upload=True)


@app.get("/api/chats/<int:friendship_id>/attachments/<int:message_id>")
@app.get("/api/chats/<int:friendship_id>/attachments/<int:message_id>/preview", defaults={"preview": True})
def chat_attachment_api(friendship_id, message_id, preview=False):
    suffix = "/preview" if preview else ""
    return proxy_api(f"/api/chats/{friendship_id}/attachments/{message_id}{suffix}", file_response=True)


if __name__ == "__main__":
    app.run(port=5000)
