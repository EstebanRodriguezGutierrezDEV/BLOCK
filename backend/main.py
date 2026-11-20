"""API REST para un gestor de tareas."""

from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
import sqlite3

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from .accounts import CurrentUser, router as accounts_router
from .db import PostgresConnection, database, init_db


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


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=1000)
    start_date: date
    end_date: date

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


def get_task_or_404(
    connection: sqlite3.Connection | PostgresConnection, task_id: int, user_id: int, *, for_update: bool = False
) -> dict:
    query = "SELECT * FROM tasks WHERE id = ? AND user_id = ?"
    if for_update and isinstance(connection, PostgresConnection):
        query += " FOR UPDATE"
    task = connection.execute(query, (task_id, user_id)).fetchone()
    if task is None:
        raise HTTPException(status_code=404, detail="Tarea no encontrada")
    return dict(task)


@app.get("/api/tasks", response_model=list[Task])
def list_tasks(user: CurrentUser):
    with database() as connection:
        rows = connection.execute(
            "SELECT * FROM tasks WHERE user_id = ? ORDER BY completed ASC, id DESC", (user["id"],)
        ).fetchall()
        return [dict(row) for row in rows]


@app.get("/api/tasks/{task_id}", response_model=Task)
def get_task(task_id: int, user: CurrentUser):
    with database() as connection:
        return get_task_or_404(connection, task_id, user["id"])


@app.post("/api/tasks", response_model=Task, status_code=status.HTTP_201_CREATED)
def create_task(data: TaskCreate, user: CurrentUser):
    with database() as connection:
        row = connection.execute(
            "INSERT INTO tasks (user_id, title, description, start_date, end_date) VALUES (?, ?, ?, ?, ?) RETURNING *",
            (user["id"], data.title, data.description.strip(), data.start_date.isoformat(), data.end_date.isoformat()),
        ).fetchone()
        return dict(row)


@app.patch("/api/tasks/{task_id}", response_model=Task)
def update_task(task_id: int, data: TaskUpdate, user: CurrentUser):
    changes = data.model_dump(exclude_unset=True, mode="json")
    if not changes:
        raise HTTPException(status_code=400, detail="No hay cambios para guardar")
    if any(value is None for value in changes.values()):
        raise HTTPException(status_code=422, detail="Los campos no se aceptan")
    if "description" in changes:
        changes["description"] = changes["description"].strip()

    fields = ", ".join(f"{column} = ?" for column in changes)
    with database(write=True) as connection:
        existing = get_task_or_404(connection, task_id, user["id"], for_update=True)
        start = changes.get("start_date", existing["start_date"])
        end = changes.get("end_date", existing["end_date"])
        if end < start:
            raise HTTPException(status_code=422, detail="La fecha de fin no puede ser anterior a la de inicio")
        updated_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        row = connection.execute(
            f"UPDATE tasks SET {fields}, updated_at = ? WHERE id = ? AND user_id = ? RETURNING *",
            (*changes.values(), updated_at, task_id, user["id"]),
        ).fetchone()
        return dict(row)


@app.delete("/api/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(task_id: int, user: CurrentUser):
    with database() as connection:
        deleted = connection.execute("DELETE FROM tasks WHERE id = ? AND user_id = ?", (task_id, user["id"]))
        if not deleted.rowcount:
            raise HTTPException(status_code=404, detail="Tarea no encontrada")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
