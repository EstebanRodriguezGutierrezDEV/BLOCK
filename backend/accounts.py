from hashlib import sha256
import secrets
from time import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import EmailStr, Field, SecretStr
from werkzeug.security import check_password_hash, generate_password_hash

from .db import database
from .rate_limits import LOGIN_ACCOUNT, LOGIN_IP, REGISTER_IP, client_network, enforce_rate_limits
from .request_security import APIInput, SESSION_TOKEN_PATTERN

router = APIRouter(prefix="/api/auth")
SESSION_SECONDS = 7 * 24 * 60 * 60
DUMMY_PASSWORD_HASH = generate_password_hash(secrets.token_urlsafe(32))


class Login(APIInput):
    email: EmailStr
    password: SecretStr = Field(min_length=1, max_length=128)


class Registration(Login):
    password: SecretStr = Field(min_length=12, max_length=128)


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
    with database() as connection:
        connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
        connection.execute("INSERT INTO sessions VALUES (?, ?, ?)", (token_hash(token), user["id"], now + SESSION_SECONDS))
    return {"token": token, "expires_in": SESSION_SECONDS}


@router.get("/me")
def me(user: CurrentUser):
    return user


@router.post("/logout", status_code=204)
def logout(session: Annotated[str, Depends(session_hash)]):
    with database() as connection:
        connection.execute("DELETE FROM sessions WHERE token_hash = ?", (session,))
    return Response(status_code=204)
