from contextlib import asynccontextmanager
from datetime import date
import sqlite3
from typing import Annotated, Literal, NamedTuple

from fastapi import FastAPI, HTTPException, Query, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StrictBool, StringConstraints, field_validator, model_validator

from .accounts import CurrentUser, router as accounts_router
from .attachment_policy import AttachmentName, attachment_response, clean_attachment_name
from .db import PostgresConnection, database, init_db, utc_timestamp
from .privacy import PRIVACY_ACKNOWLEDGEMENT_ERROR
from .rate_limits import ATTACHMENT_BYTES, ATTACHMENT_UPLOADS, TASK_CREATIONS, enforce_rate_limits
from .request_security import APIInput, MAX_RECORD_ID, OptionalWorkspaceID, RecordID, RequestSecurity, WorkspaceID
from .social import router as social_router
from .workspaces import require_member, router as workspaces_router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Tu block de tareas",
    description="CRUD de tareas con persistencia en SQLite o PostgreSQL.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.add_middleware(RequestSecurity)
app.include_router(accounts_router)
app.include_router(workspaces_router)
app.include_router(social_router)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, error: RequestValidationError):
    if not request.url.path.startswith("/api/auth/"):
        details = [{key: item[key] for key in ("loc", "msg", "type")} for item in error.errors()]
        return JSONResponse({"detail": details}, status_code=422)

    messages = {
        "email": "Introduce una dirección de correo válida.",
        "password": "Introduce una contraseña válida.",
        "current_password": "Introduce tu contraseña actual.",
        "new_password": "La contraseña nueva debe tener entre 12 y 128 caracteres.",
    }
    if request.url.path == "/api/auth/register":
        messages["password"] = "La contraseña debe tener entre 12 y 128 caracteres."
        messages["privacy_acknowledgement"] = PRIVACY_ACKNOWLEDGEMENT_ERROR
    field = error.errors()[0]["loc"][-1]
    return JSONResponse({"detail": messages.get(field, "Revisa los datos introducidos.")}, status_code=422)


BoardStatus = Literal["todo", "doing", "done"]
CALENDAR_PAGE_SIZE = 200


class TaskCreate(APIInput):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=1000)
    start_date: date
    end_date: date
    board_status: BoardStatus = "todo"

    @model_validator(mode="after")
    def validate_dates(self):
        if self.end_date < self.start_date:
            raise ValueError("La fecha de fin no puede ser anterior a la de inicio")
        return self

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("El título no puede estar vacío")
        return value


class TaskUpdate(APIInput):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    completed: StrictBool | None = None
    start_date: date | None = None
    end_date: date | None = None
    board_status: BoardStatus | None = None

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str | None) -> str | None:
        if value is not None:
            value = value.strip()
            if not value:
                raise ValueError("El título no puede estar vacío")
        return value


class Task(BaseModel):
    id: int
    title: str
    description: str
    completed: bool
    start_date: date
    end_date: date
    created_at: str
    updated_at: str
    board_status: BoardStatus
    board_position: int


class CalendarTask(Task):
    workspace_id: int | None
    workspace_name: str | None


class CalendarPage(BaseModel):
    tasks: list[CalendarTask]
    next_before_id: str | None


class TaskPosition(APIInput):
    board_status: BoardStatus
    before_task_id: int | None = Field(default=None, gt=0, le=MAX_RECORD_ID)


class TaskStatusPermission(APIInput):
    can_change_status: StrictBool | None


class CommentCreate(APIInput):
    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class TaskScope(NamedTuple):
    condition: str
    key: int
    can_change_status: bool
    is_owner: bool


def task_scope(connection, user_id: int, workspace_id: int | None, *, write=False) -> TaskScope:
    if workspace_id is None:
        return TaskScope("workspace_id IS NULL AND user_id = ?", user_id, True, True)
    workspace = require_member(connection, workspace_id, user_id, write=write)
    owner = workspace["owner_id"] == user_id
    return TaskScope("workspace_id = ?", workspace_id, owner or bool(workspace["can_change_status"]), owner)


def require_status_permission(connection, scope: TaskScope, user_id: int, task_id: int | None = None) -> None:
    allowed = scope.can_change_status
    if task_id is not None and not scope.is_owner:
        override = connection.execute("""
            SELECT can_change_status FROM task_status_permissions WHERE task_id = ? AND user_id = ?
        """, (task_id, user_id)).fetchone()
        if override is not None:
            allowed = bool(override["can_change_status"])
    if not allowed:
        raise HTTPException(403, "El propietario debe darte permiso para cambiar el estado.")


def next_board_position(connection, workspace_id: int, board_status: BoardStatus) -> int:
    return connection.execute("""
        SELECT COALESCE(MAX(board_position), 0) + 1 AS position
        FROM tasks WHERE workspace_id = ? AND board_status = ?
    """, (workspace_id, board_status)).fetchone()["position"]


def read_tasks(connection, scope: TaskScope, shared: bool) -> list[dict]:
    order = "board_position ASC, id DESC" if shared else "completed ASC, id DESC"
    rows = connection.execute(
        f"SELECT * FROM tasks WHERE {scope.condition} ORDER BY {order}", (scope.key,)
    ).fetchall()
    return [dict(row) for row in rows]


def get_task_or_404(
    connection: sqlite3.Connection | PostgresConnection, task_id: int,
    scope: TaskScope, *, for_update: bool = False,
) -> dict:
    query = f"SELECT * FROM tasks WHERE id = ? AND {scope.condition}"
    if for_update and isinstance(connection, PostgresConnection):
        query += " FOR UPDATE"
    task = connection.execute(query, (task_id, scope.key)).fetchone()
    if task is None:
        raise HTTPException(status_code=404, detail="Tarea no encontrada")
    return dict(task)


@app.get("/api/calendar/tasks", response_model=CalendarPage)
def list_calendar_tasks(user: CurrentUser, before_id: Annotated[int | None, Query(ge=1, le=MAX_RECORD_ID)] = None):
    cursor_filter = "AND t.id < ?" if before_id is not None else ""
    params = (user["id"], before_id) if before_id is not None else (user["id"],)
    with database() as connection:
        rows = connection.execute(f"""
            SELECT t.*, NULL AS workspace_name
            FROM tasks t
            WHERE t.user_id = ? AND t.workspace_id IS NULL {cursor_filter}
            UNION ALL
            SELECT t.*, w.name AS workspace_name
            FROM workspace_members m
            JOIN workspaces w ON w.id = m.workspace_id
            JOIN tasks t ON t.workspace_id = w.id
            WHERE m.user_id = ? {cursor_filter}
            ORDER BY id DESC
            LIMIT ?
        """, (*params, *params, CALENDAR_PAGE_SIZE + 1)).fetchall()
    tasks = [dict(row) for row in rows[:CALENDAR_PAGE_SIZE]]
    return {
        "tasks": tasks,
        "next_before_id": str(tasks[-1]["id"]) if len(rows) > CALENDAR_PAGE_SIZE else None,
    }


@app.get("/api/tasks", response_model=list[Task])
def list_tasks(user: CurrentUser, workspace_id: OptionalWorkspaceID = None):
    with database() as connection:
        return read_tasks(connection, task_scope(connection, user["id"], workspace_id), workspace_id is not None)


@app.get("/api/tasks/{task_id}", response_model=Task)
def get_task(task_id: RecordID, user: CurrentUser, workspace_id: OptionalWorkspaceID = None):
    with database() as connection:
        return get_task_or_404(connection, task_id, task_scope(connection, user["id"], workspace_id))


@app.post("/api/tasks", response_model=Task, status_code=status.HTTP_201_CREATED)
def create_task(data: TaskCreate, user: CurrentUser, workspace_id: OptionalWorkspaceID = None):
    enforce_rate_limits((TASK_CREATIONS, user["id"]))
    with database(write=workspace_id is not None) as connection:
        scope = task_scope(connection, user["id"], workspace_id, write=True)
        if data.board_status != "todo":
            require_status_permission(connection, scope, user["id"])
        position = next_board_position(connection, workspace_id, data.board_status) if workspace_id is not None else 0
        row = connection.execute(
            """INSERT INTO tasks (user_id, workspace_id, title, description, start_date, end_date,
                                  board_status, completed, board_position)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING *""",
            (user["id"], workspace_id, data.title, data.description.strip(), data.start_date.isoformat(),
             data.end_date.isoformat(), data.board_status, data.board_status == "done", position),
        ).fetchone()
        return dict(row)


@app.patch("/api/tasks/{task_id}", response_model=Task)
def update_task(task_id: RecordID, data: TaskUpdate, user: CurrentUser, workspace_id: OptionalWorkspaceID = None):
    changes = data.model_dump(exclude_unset=True, mode="json")
    if not changes:
        raise HTTPException(status_code=400, detail="No hay cambios para guardar")
    if any(value is None for value in changes.values()):
        raise HTTPException(status_code=422, detail="Los campos no se aceptan")
    if "description" in changes:
        changes["description"] = changes["description"].strip()
    if "board_status" in changes:
        completed = changes["board_status"] == "done"
        if "completed" in changes and changes["completed"] != completed:
            raise HTTPException(422, "El estado de la tarjeta no coincide con su marca de completada.")
        changes["completed"] = completed

    with database(write=True) as connection:
        scope = task_scope(connection, user["id"], workspace_id, write=True)
        existing = get_task_or_404(connection, task_id, scope, for_update=True)
        start = changes.get("start_date", existing["start_date"])
        end = changes.get("end_date", existing["end_date"])
        if end < start:
            raise HTTPException(status_code=422, detail="La fecha de fin no puede ser anterior a la de inicio")
        if "completed" in changes and "board_status" not in changes:
            if changes["completed"]:
                changes["board_status"] = "done"
            elif existing["board_status"] == "done":
                changes["board_status"] = "todo"
        new_status = changes.get("board_status", existing["board_status"])
        status_changed = new_status != existing["board_status"]
        if status_changed or changes.get("completed", existing["completed"]) != existing["completed"]:
            require_status_permission(connection, scope, user["id"], task_id)
        if status_changed and workspace_id is not None:
            changes["board_position"] = next_board_position(connection, workspace_id, new_status)
        fields = ", ".join(f"{column} = ?" for column in changes)
        row = connection.execute(
            f"UPDATE tasks SET {fields}, updated_at = ? WHERE id = ? AND {scope.condition} RETURNING *",
            (*changes.values(), utc_timestamp(), task_id, scope.key),
        ).fetchone()
        return dict(row)


@app.delete("/api/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(task_id: RecordID, user: CurrentUser, workspace_id: OptionalWorkspaceID = None):
    with database(write=workspace_id is not None) as connection:
        scope = task_scope(connection, user["id"], workspace_id, write=True)
        deleted = connection.execute(f"DELETE FROM tasks WHERE id = ? AND {scope.condition}", (task_id, scope.key))
        if not deleted.rowcount:
            raise HTTPException(status_code=404, detail="Tarea no encontrada")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.patch("/api/tasks/{task_id}/position", response_model=list[Task])
def move_task(task_id: RecordID, workspace_id: WorkspaceID, data: TaskPosition, user: CurrentUser):
    with database(write=True) as connection:
        scope = task_scope(connection, user["id"], workspace_id, write=True)
        task = get_task_or_404(connection, task_id, scope, for_update=True)
        if data.board_status != task["board_status"]:
            require_status_permission(connection, scope, user["id"], task_id)
        cards = connection.execute("""
            SELECT id, board_position FROM tasks
            WHERE workspace_id = ? AND board_status = ? AND id <> ?
            ORDER BY board_position, id DESC
        """, (workspace_id, data.board_status, task_id)).fetchall()
        destination = len(cards)
        if data.before_task_id is not None:
            destination = next((index for index, card in enumerate(cards) if card["id"] == data.before_task_id), -1)
            if destination < 0:
                raise HTTPException(409, "La posición elegida ya no está disponible. Actualiza el tablero.")
        for index, card in enumerate(cards):
            position = index + int(index >= destination) + 1
            if position != card["board_position"]:
                connection.execute("UPDATE tasks SET board_position = ? WHERE id = ?", (position, card["id"]))
        connection.execute("""
            UPDATE tasks SET board_status = ?, completed = ?, board_position = ?, updated_at = ?
            WHERE id = ? AND workspace_id = ?
        """, (data.board_status, data.board_status == "done", destination + 1, utc_timestamp(), task_id, workspace_id))
        return read_tasks(connection, scope, True)


def require_task_permissions_owner(connection, task_id, workspace_id, user_id, *, write=False):
    scope = task_scope(connection, user_id, workspace_id, write=write)
    if not scope.is_owner:
        raise HTTPException(403, "Solo el propietario del tablero puede cambiar los permisos de una tarea.")
    get_task_or_404(connection, task_id, scope)


@app.get("/api/tasks/{task_id}/permissions")
def list_task_permissions(task_id: RecordID, workspace_id: WorkspaceID, user: CurrentUser):
    with database() as connection:
        require_task_permissions_owner(connection, task_id, workspace_id, user["id"])
        members = connection.execute("""
            SELECT u.id, u.email, p.can_change_status
            FROM workspace_members m JOIN users u ON u.id = m.user_id
            LEFT JOIN task_status_permissions p ON p.task_id = ? AND p.user_id = m.user_id
            WHERE m.workspace_id = ? AND m.user_id <> ? ORDER BY u.email
        """, (task_id, workspace_id, user["id"])).fetchall()
        return [
            {**dict(row), "can_change_status": None if row["can_change_status"] is None else bool(row["can_change_status"])}
            for row in members
        ]


@app.patch("/api/tasks/{task_id}/permissions/{member_id}")
def update_task_permission(
    task_id: RecordID, workspace_id: WorkspaceID, member_id: RecordID, data: TaskStatusPermission, user: CurrentUser,
):
    with database(write=True) as connection:
        require_task_permissions_owner(connection, task_id, workspace_id, user["id"], write=True)
        if member_id == user["id"]:
            raise HTTPException(422, "El propietario siempre puede cambiar el estado.")
        member = connection.execute("""
            SELECT 1 FROM workspace_members WHERE workspace_id = ? AND user_id = ?
        """, (workspace_id, member_id)).fetchone()
        if member is None:
            raise HTTPException(404, "El miembro no pertenece al espacio.")

        if data.can_change_status is None:
            connection.execute("""
                DELETE FROM task_status_permissions WHERE task_id = ? AND user_id = ?
            """, (task_id, member_id))
        else:
            connection.execute("""
                INSERT INTO task_status_permissions (task_id, workspace_id, user_id, can_change_status)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (task_id, user_id) DO UPDATE SET can_change_status = excluded.can_change_status
            """, (task_id, workspace_id, member_id, int(data.can_change_status)))
        return {"can_change_status": data.can_change_status}


@app.get("/api/tasks/{task_id}/comments")
def list_comments(task_id: RecordID, workspace_id: WorkspaceID, user: CurrentUser):
    with database() as connection:
        get_task_or_404(connection, task_id, task_scope(connection, user["id"], workspace_id))
        comments = connection.execute("""
            SELECT c.id, c.body, c.created_at, u.email AS author_email
            FROM task_comments c JOIN users u ON u.id = c.author_id
            WHERE c.task_id = ? ORDER BY c.id
        """, (task_id,)).fetchall()
        return [dict(row) for row in comments]


@app.post("/api/tasks/{task_id}/comments", status_code=201)
def create_comment(task_id: RecordID, workspace_id: WorkspaceID, data: CommentCreate, user: CurrentUser):
    with database(write=True) as connection:
        get_task_or_404(connection, task_id, task_scope(connection, user["id"], workspace_id), for_update=True)
        comment = connection.execute("""
            INSERT INTO task_comments (task_id, author_id, body, created_at) VALUES (?, ?, ?, ?)
            RETURNING id, body, created_at
        """, (task_id, user["id"], data.body, utc_timestamp())).fetchone()
        return {**dict(comment), "author_email": user["email"]}


@app.get("/api/tasks/{task_id}/attachments")
def list_attachments(task_id: RecordID, workspace_id: WorkspaceID, user: CurrentUser):
    with database() as connection:
        get_task_or_404(connection, task_id, task_scope(connection, user["id"], workspace_id))
        attachments = connection.execute("""
            SELECT a.id, a.file_name, a.size_bytes, a.created_at, u.email AS uploader_email
            FROM task_attachments a JOIN users u ON u.id = a.uploaded_by
            WHERE a.task_id = ? ORDER BY a.id DESC
        """, (task_id,)).fetchall()
        return [dict(attachment) for attachment in attachments]


def store_attachment(task_id: int, workspace_id: int, user: dict, file_name: str, content: bytes):
    with database(write=True) as connection:
        get_task_or_404(connection, task_id, task_scope(connection, user["id"], workspace_id), for_update=True)
        attachment = connection.execute("""
            INSERT INTO task_attachments (task_id, uploaded_by, file_name, size_bytes, content, created_at)
            VALUES (?, ?, ?, ?, ?, ?) RETURNING id, file_name, size_bytes, created_at
        """, (task_id, user["id"], file_name, len(content), content, utc_timestamp())).fetchone()
        return {**dict(attachment), "uploader_email": user["email"]}


@app.post("/api/tasks/{task_id}/attachments", status_code=201)
async def upload_attachment(
    task_id: RecordID, workspace_id: WorkspaceID, user: CurrentUser,
    request: Request, file_name: AttachmentName,
):
    file_name = clean_attachment_name(file_name)
    await run_in_threadpool(enforce_rate_limits, (ATTACHMENT_UPLOADS, user["id"]))
    content = await request.body()
    await run_in_threadpool(enforce_rate_limits, (ATTACHMENT_BYTES, user["id"], len(content)))
    return await run_in_threadpool(store_attachment, task_id, workspace_id, user, file_name, content)


@app.get("/api/tasks/{task_id}/attachments/{attachment_id}")
def download_attachment(
    task_id: RecordID, attachment_id: RecordID, workspace_id: WorkspaceID,
    user: CurrentUser,
):
    with database() as connection:
        get_task_or_404(connection, task_id, task_scope(connection, user["id"], workspace_id))
        attachment = connection.execute("""
            SELECT file_name, content FROM task_attachments WHERE task_id = ? AND id = ?
        """, (task_id, attachment_id)).fetchone()
        if attachment is None:
            raise HTTPException(404, "Archivo no encontrado.")
        return attachment_response(attachment["file_name"], attachment["content"])
