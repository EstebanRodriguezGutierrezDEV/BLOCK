from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
import sqlite3
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

from .accounts import CurrentUser, router as accounts_router
from .db import PostgresConnection, database, init_db
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
)
app.include_router(accounts_router)
app.include_router(workspaces_router)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, error: RequestValidationError):
    if not request.url.path.startswith("/api/auth/"):
        return await request_validation_exception_handler(request, error)

    messages = {
        "email": "Introduce una dirección de correo válida.",
        "password": "Introduce una contraseña válida.",
    }
    if request.url.path == "/api/auth/register":
        messages["password"] = "La contraseña debe tener entre 12 y 128 caracteres."
    field = error.errors()[0]["loc"][-1]
    return JSONResponse({"detail": messages.get(field, "Revisa los datos introducidos.")}, status_code=422)


BoardStatus = Literal["todo", "doing", "done"]


class TaskCreate(BaseModel):
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


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    completed: bool | None = None
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


class CommentCreate(BaseModel):
    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


def task_scope(connection, user_id: int, workspace_id: int | None) -> tuple[str, int]:
    if workspace_id is None:
        return "workspace_id IS NULL AND user_id = ?", user_id
    require_member(connection, workspace_id, user_id)
    return "workspace_id = ?", workspace_id


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def get_task_or_404(
    connection: sqlite3.Connection | PostgresConnection, task_id: int,
    scope: tuple[str, int], *, for_update: bool = False,
) -> dict:
    condition, scope_id = scope
    query = f"SELECT * FROM tasks WHERE id = ? AND {condition}"
    if for_update and isinstance(connection, PostgresConnection):
        query += " FOR UPDATE"
    task = connection.execute(query, (task_id, scope_id)).fetchone()
    if task is None:
        raise HTTPException(status_code=404, detail="Tarea no encontrada")
    return dict(task)


@app.get("/api/tasks", response_model=list[Task])
def list_tasks(user: CurrentUser, workspace_id: int | None = None):
    with database() as connection:
        condition, scope_id = task_scope(connection, user["id"], workspace_id)
        rows = connection.execute(
            f"SELECT * FROM tasks WHERE {condition} ORDER BY completed ASC, id DESC", (scope_id,)
        ).fetchall()
        return [dict(row) for row in rows]


@app.get("/api/tasks/{task_id}", response_model=Task)
def get_task(task_id: int, user: CurrentUser, workspace_id: int | None = None):
    with database() as connection:
        return get_task_or_404(connection, task_id, task_scope(connection, user["id"], workspace_id))


@app.post("/api/tasks", response_model=Task, status_code=status.HTTP_201_CREATED)
def create_task(data: TaskCreate, user: CurrentUser, workspace_id: int | None = None):
    with database() as connection:
        task_scope(connection, user["id"], workspace_id)
        row = connection.execute(
            """INSERT INTO tasks (user_id, workspace_id, title, description, start_date, end_date, board_status, completed)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING *""",
            (user["id"], workspace_id, data.title, data.description.strip(), data.start_date.isoformat(),
             data.end_date.isoformat(), data.board_status, data.board_status == "done"),
        ).fetchone()
        return dict(row)


@app.patch("/api/tasks/{task_id}", response_model=Task)
def update_task(task_id: int, data: TaskUpdate, user: CurrentUser, workspace_id: int | None = None):
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
        scope = task_scope(connection, user["id"], workspace_id)
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
        fields = ", ".join(f"{column} = ?" for column in changes)
        condition, scope_id = scope
        row = connection.execute(
            f"UPDATE tasks SET {fields}, updated_at = ? WHERE id = ? AND {condition} RETURNING *",
            (*changes.values(), utc_timestamp(), task_id, scope_id),
        ).fetchone()
        return dict(row)


@app.delete("/api/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(task_id: int, user: CurrentUser, workspace_id: int | None = None):
    with database() as connection:
        condition, scope_id = task_scope(connection, user["id"], workspace_id)
        deleted = connection.execute(f"DELETE FROM tasks WHERE id = ? AND {condition}", (task_id, scope_id))
        if not deleted.rowcount:
            raise HTTPException(status_code=404, detail="Tarea no encontrada")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/api/tasks/{task_id}/comments")
def list_comments(task_id: int, workspace_id: int, user: CurrentUser):
    with database() as connection:
        get_task_or_404(connection, task_id, task_scope(connection, user["id"], workspace_id))
        comments = connection.execute("""
            SELECT c.id, c.body, c.created_at, u.email AS author_email
            FROM task_comments c JOIN users u ON u.id = c.author_id
            WHERE c.task_id = ? ORDER BY c.id
        """, (task_id,)).fetchall()
        return [dict(row) for row in comments]


@app.post("/api/tasks/{task_id}/comments", status_code=201)
def create_comment(task_id: int, workspace_id: int, data: CommentCreate, user: CurrentUser):
    with database(write=True) as connection:
        get_task_or_404(connection, task_id, task_scope(connection, user["id"], workspace_id), for_update=True)
        comment = connection.execute("""
            INSERT INTO task_comments (task_id, author_id, body, created_at) VALUES (?, ?, ?, ?)
            RETURNING id, body, created_at
        """, (task_id, user["id"], data.body, utc_timestamp())).fetchone()
        return {**dict(comment), "author_email": user["email"]}
