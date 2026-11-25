import re
from typing import Annotated

from fastapi import HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, SecretStr, field_validator
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse

from .attachment_policy import ATTACHMENT_LIMIT_MESSAGE, MAX_ATTACHMENT_BYTES

MAX_JSON_BYTES = 16 * 1024
MAX_JSON_DEPTH = 32
MAX_RECORD_ID = 2**63 - 1
RecordID = Annotated[int, Path(ge=1, le=MAX_RECORD_ID)]
WorkspaceID = Annotated[int, Query(ge=1, le=MAX_RECORD_ID)]
OptionalWorkspaceID = Annotated[int | None, Query(ge=1, le=MAX_RECORD_ID)]
SESSION_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")
ATTACHMENT_ROUTE = re.compile(r"/api/(?:tasks|chats)/[0-9]+/attachments/?")


class APIInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*")
    @classmethod
    def validate_text(cls, value):
        text = value.get_secret_value() if isinstance(value, SecretStr) else value
        if isinstance(text, str):
            try:
                text.encode("utf-8")
            except UnicodeEncodeError:
                raise ValueError("El texto contiene caracteres no válidos.") from None
            if isinstance(value, str) and "\x00" in value:
                raise ValueError("El texto contiene caracteres no válidos.")
        return value


class RequestSecurity:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_protected(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "no-store"
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Cross-Origin-Resource-Policy"] = "same-origin"
                headers["X-Frame-Options"] = "DENY"
            await send(message)

        if scope["method"] not in {"POST", "PATCH", "PUT", "DELETE"}:
            await self.app(scope, receive, send_protected)
            return

        path = scope["path"]
        root = scope.get("root_path", "").rstrip("/")
        if root and path.startswith(root + "/"):
            path = path[len(root):]
        attachment = scope["method"] == "POST" and ATTACHMENT_ROUTE.fullmatch(path)
        limit = MAX_ATTACHMENT_BYTES if attachment else MAX_JSON_BYTES
        overflow = ATTACHMENT_LIMIT_MESSAGE if attachment else "La petición es demasiado grande."
        headers = Headers(scope=scope)
        try:
            declared_size = int(headers.get("Content-Length", "0"))
            if declared_size < 0:
                raise ValueError
        except ValueError:
            await JSONResponse({"detail": "Tamaño de petición no válido."}, 400)(scope, receive, send_protected)
            return
        if declared_size > limit:
            await JSONResponse({"detail": overflow}, 413)(scope, receive, send_protected)
            return

        media_type = headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        accepted_type = (
            media_type == "application/octet-stream" if attachment
            else media_type == "application/json" or (media_type.startswith("application/") and media_type.endswith("+json"))
        )
        if not accepted_type and (attachment or declared_size):
            await JSONResponse({"detail": "El formato de la petición no es compatible."}, 415)(scope, receive, send_protected)
            return
        size = 0
        depth = 0
        in_string = False
        escaped = False

        async def receive_bounded():
            nonlocal size, depth, in_string, escaped
            message = await receive()
            if message["type"] == "http.request":
                size += len(message.get("body", b""))
                if size > limit:
                    raise HTTPException(413, overflow)
                if size and not accepted_type:
                    raise HTTPException(415, "El formato de la petición no es compatible.")
                if not attachment and accepted_type:
                    # Network JSON is UTF-8. Reject raw NULs, including UTF-16
                    # and UTF-32 bodies that Python's decoder also accepts.
                    if b"\x00" in message.get("body", b""):
                        raise HTTPException(400, "El JSON debe utilizar UTF-8.")
                    # Count JSON containers without treating braces in strings
                    # as structure, including when escapes span ASGI chunks.
                    for byte in message.get("body", b""):
                        if in_string:
                            if escaped:
                                escaped = False
                            elif byte == 92:
                                escaped = True
                            elif byte == 34:
                                in_string = False
                        elif byte == 34:
                            in_string = True
                        elif byte in (91, 123):
                            depth += 1
                            if depth > MAX_JSON_DEPTH:
                                raise HTTPException(400, "El JSON contiene demasiados niveles.")
                        elif byte in (93, 125):
                            depth -= 1
            return message

        await self.app(scope, receive_bounded, send_protected)
