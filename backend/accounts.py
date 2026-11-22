from hashlib import sha256
import secrets
from time import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from psycopg.errors import DeadlockDetected, SerializationFailure
from pydantic import EmailStr, Field, SecretStr
from werkzeug.security import check_password_hash, generate_password_hash

from .db import PostgresConnection, database
from .rate_limits import LOGIN_ACCOUNT, LOGIN_IP, REGISTER_IP, RateLimit, client_network, enforce_rate_limits
from .request_security import APIInput, SESSION_TOKEN_PATTERN

router = APIRouter(prefix="/api/auth")
SESSION_SECONDS = 7 * 24 * 60 * 60
ACCOUNT_CHANGES = RateLimit("account_change", 10, 300)
DUMMY_PASSWORD_HASH = generate_password_hash(secrets.token_urlsafe(32))


class Login(APIInput):
    email: EmailStr
    password: SecretStr = Field(min_length=1, max_length=128)


class Registration(Login):
    password: SecretStr = Field(min_length=12, max_length=128)


class AccountConfirmation(APIInput):
    current_password: SecretStr = Field(min_length=1, max_length=128)


class PasswordChange(AccountConfirmation):
    new_password: SecretStr = Field(min_length=12, max_length=128)


def token_hash(token: str) -> str:
    return sha256(token.encode()).hexdigest()


def session_hash(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(HTTPBearer(auto_error=False))]) -> str:
    if credentials is None or not SESSION_TOKEN_PATTERN.fullmatch(credentials.credentials):
        raise HTTPException(401, "Inicia sesión para acceder a tus tareas", headers={"WWW-Authenticate": "Bearer"})
    return token_hash(credentials.credentials)


def current_user(session: Annotated[str, Depends(session_hash)]) -> dict:
    with database() as connection:
        user = connection.execute("""
            SELECT users.id, users.email
            FROM sessions JOIN users ON users.id = sessions.user_id
            WHERE token_hash = ? AND expires_at > ? AND users.password_hash IS NOT NULL
        """, (session, int(time()))).fetchone()
    if user is None:
        raise HTTPException(401, "Inicia sesión para acceder a tus tareas", headers={"WWW-Authenticate": "Bearer"})
    return dict(user)


CurrentUser = Annotated[dict, Depends(current_user)]
SessionHash = Annotated[str, Depends(session_hash)]


def lock_account(connection, user_id, password_hash):
    lock = " FOR UPDATE" if isinstance(connection, PostgresConnection) else ""
    return connection.execute(
        "SELECT id FROM users WHERE id = ? AND password_hash = ?" + lock, (user_id, password_hash),
    ).fetchone()


def check_current_password(user_id, current_password):
    enforce_rate_limits((ACCOUNT_CHANGES, user_id))
    with database() as connection:
        row = connection.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
    password_hash = row["password_hash"] if row else None
    if not password_hash or not check_password_hash(password_hash, current_password):
        raise HTTPException(403, "La contraseña actual no coincide.")
    return password_hash


def require_current_session(connection, user_id, password_hash, session):
    account = lock_account(connection, user_id, password_hash)
    if account is None or connection.execute("""
        SELECT 1 FROM sessions WHERE token_hash = ? AND user_id = ? AND expires_at > ?
    """, (session, user_id, int(time()))).fetchone() is None:
        raise HTTPException(401, "La sesión ha cambiado. Vuelve a iniciar sesión.")


@router.post("/register", status_code=201)
def register(data: Registration, request: Request):
    enforce_rate_limits((REGISTER_IP, client_network(request)))
    password_hash = generate_password_hash(data.password.get_secret_value())
    with database() as connection:
        created = connection.execute("""
            INSERT INTO users (email, password_hash) VALUES (?, ?)
            ON CONFLICT(email) DO UPDATE SET password_hash = excluded.password_hash
            WHERE users.password_hash IS NULL
        """, (data.email.lower(), password_hash))
        if not created.rowcount:
            raise HTTPException(409, "Ya existe una cuenta con este correo. Inicia sesión.")
    return Response(status_code=201)


@router.post("/login")
def login(data: Login, request: Request):
    email = data.email.lower()
    enforce_rate_limits((LOGIN_IP, client_network(request)), (LOGIN_ACCOUNT, email))
    with database() as connection:
        user = connection.execute("SELECT id, password_hash FROM users WHERE email = ?", (email,)).fetchone()
    password_hash = user["password_hash"] if user else None
    correct = check_password_hash(password_hash or DUMMY_PASSWORD_HASH, data.password.get_secret_value())
    if not correct or not password_hash:
        raise HTTPException(401, "El correo o la contraseña no coinciden.")

    token = secrets.token_urlsafe(32)
    now = int(time())
    with database(write=True) as connection:
        if lock_account(connection, user["id"], password_hash) is None:
            raise HTTPException(401, "El correo o la contraseña no coinciden.")
        connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
        connection.execute("INSERT INTO sessions VALUES (?, ?, ?)", (token_hash(token), user["id"], now + SESSION_SECONDS))
    return {"token": token, "expires_in": SESSION_SECONDS}


@router.get("/me")
def me(user: CurrentUser):
    return user


@router.post("/change-password", status_code=204)
def change_password(data: PasswordChange, user: CurrentUser, session: SessionHash):
    current_password = data.current_password.get_secret_value()
    password_hash = check_current_password(user["id"], current_password)
    new_password = data.new_password.get_secret_value()
    if new_password == current_password:
        raise HTTPException(422, "Elige una contraseña distinta de la actual.")
    new_hash = generate_password_hash(new_password)
    with database(write=True) as connection:
        require_current_session(connection, user["id"], password_hash, session)
        connection.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, user["id"]))
        connection.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash <> ?", (user["id"], session))
    return Response(status_code=204)


def remove_account(connection, user):
    user_id = user["id"]
    owned = connection.execute("SELECT id FROM workspaces WHERE owner_id = ? ORDER BY id", (user_id,)).fetchall()
    for workspace in owned:
        successor = connection.execute("""
            SELECT m.user_id
            FROM workspace_members m JOIN users u ON u.id = m.user_id
            WHERE m.workspace_id = ? AND m.user_id <> ? AND u.password_hash IS NOT NULL
            ORDER BY m.user_id LIMIT 1
        """, (workspace["id"], user_id)).fetchone()
        if successor is None:
            connection.execute("DELETE FROM workspaces WHERE id = ?", (workspace["id"],))
        else:
            connection.execute("UPDATE workspaces SET owner_id = ? WHERE id = ?", (successor["user_id"], workspace["id"]))
    connection.execute("""
        UPDATE tasks SET user_id = (SELECT w.owner_id FROM workspaces w WHERE w.id = tasks.workspace_id)
        WHERE user_id = ? AND workspace_id IS NOT NULL
    """, (user_id,))
    connection.execute("DELETE FROM tasks WHERE user_id = ? AND workspace_id IS NULL", (user_id,))
    connection.execute("DELETE FROM task_comments WHERE author_id = ?", (user_id,))
    connection.execute("DELETE FROM task_attachments WHERE uploaded_by = ?", (user_id,))
    connection.execute("DELETE FROM workspace_invitations WHERE recipient_id = ? OR email = ?", (user_id, user["email"]))
    connection.execute("DELETE FROM friendships WHERE user_low = ? OR user_high = ?", (user_id, user_id))
    connection.execute("DELETE FROM users WHERE id = ?", (user_id,))


@router.post("/delete-account", status_code=204)
def delete_account(data: AccountConfirmation, user: CurrentUser, session: SessionHash):
    password_hash = check_current_password(user["id"], data.current_password.get_secret_value())
    for attempt in range(2):
        try:
            with database(write=True) as connection:
                if isinstance(connection, PostgresConnection):
                    connection.execute("SELECT pg_advisory_xact_lock(638102598)")
                require_current_session(connection, user["id"], password_hash, session)
                remove_account(connection, user)
            return Response(status_code=204)
        except (DeadlockDetected, SerializationFailure):
            if attempt:
                raise HTTPException(409, "La cuenta está en uso. Vuelve a intentar la eliminación.") from None


@router.post("/logout", status_code=204)
def logout(session: SessionHash):
    with database() as connection:
        connection.execute("DELETE FROM sessions WHERE token_hash = ?", (session,))
    return Response(status_code=204)
