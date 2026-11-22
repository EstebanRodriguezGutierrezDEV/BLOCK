import secrets
from time import time
from typing import Annotated

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr, Field, StringConstraints

from .accounts import CurrentUser, token_hash
from .db import database

router = APIRouter(prefix="/api")
INVITATION_LIFETIME = 7 * 24 * 60 * 60


class WorkspaceCreate(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class InvitationCreate(BaseModel):
    email: EmailStr


class InvitationAccept(BaseModel):
    token: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")


def require_member(connection, workspace_id: int, user_id: int) -> dict:
    workspace = connection.execute("""
        SELECT w.id, w.name, w.owner_id
        FROM workspaces w JOIN workspace_members m ON m.workspace_id = w.id
        WHERE w.id = ? AND m.user_id = ?
    """, (workspace_id, user_id)).fetchone()
    if workspace is None:
        raise HTTPException(404, "Espacio de trabajo no encontrado.")
    return dict(workspace)


@router.get("/workspaces")
def list_workspaces(user: CurrentUser):
    with database() as connection:
        return [dict(row) for row in connection.execute("""
            SELECT w.id, w.name, w.owner_id
            FROM workspaces w JOIN workspace_members m ON m.workspace_id = w.id
            WHERE m.user_id = ? ORDER BY lower(w.name), w.id
        """, (user["id"],)).fetchall()]


@router.post("/workspaces", status_code=201)
def create_workspace(data: WorkspaceCreate, user: CurrentUser):
    with database(write=True) as connection:
        workspace = connection.execute(
            "INSERT INTO workspaces (name, owner_id) VALUES (?, ?) RETURNING id, name, owner_id",
            (data.name, user["id"]),
        ).fetchone()
        connection.execute(
            "INSERT INTO workspace_members (workspace_id, user_id) VALUES (?, ?)",
            (workspace["id"], user["id"]),
        )
        return dict(workspace)


@router.get("/workspaces/{workspace_id}")
def get_workspace(workspace_id: int, user: CurrentUser):
    with database() as connection:
        workspace = require_member(connection, workspace_id, user["id"])
        members = connection.execute("""
            SELECT users.id, users.email
            FROM workspace_members m JOIN users ON users.id = m.user_id
            WHERE m.workspace_id = ? ORDER BY users.email
        """, (workspace_id,)).fetchall()
        return {**workspace, "can_invite": workspace["owner_id"] == user["id"], "members": [dict(row) for row in members]}


@router.post("/workspaces/{workspace_id}/invitations", status_code=201)
def invite_to_workspace(workspace_id: int, data: InvitationCreate, user: CurrentUser):
    email = data.email.lower()
    token = secrets.token_urlsafe(32)
    expires_at = int(time()) + INVITATION_LIFETIME
    with database(write=True) as connection:
        workspace = require_member(connection, workspace_id, user["id"])
        if workspace["owner_id"] != user["id"]:
            raise HTTPException(403, "Solo el propietario puede invitar a otras personas.")
        member = connection.execute("""
            SELECT 1 FROM workspace_members m JOIN users u ON u.id = m.user_id
            WHERE m.workspace_id = ? AND u.email = ?
        """, (workspace_id, email)).fetchone()
        if member:
            raise HTTPException(409, "Ese correo ya pertenece al espacio.")
        connection.execute("""
            INSERT INTO workspace_invitations (workspace_id, email, token_hash, expires_at) VALUES (?, ?, ?, ?)
            ON CONFLICT (workspace_id, email) DO UPDATE SET
                token_hash = excluded.token_hash, expires_at = excluded.expires_at
        """, (workspace_id, email, token_hash(token), expires_at))
    return {"token": token, "expires_at": expires_at}


@router.post("/invitations/accept")
def accept_invitation(data: InvitationAccept, user: CurrentUser):
    with database(write=True) as connection:
        invitation = connection.execute("""
            DELETE FROM workspace_invitations
            WHERE token_hash = ? AND email = ? AND expires_at > ?
            RETURNING workspace_id
        """, (token_hash(data.token), user["email"], int(time()))).fetchone()
        if invitation is None:
            raise HTTPException(404, "La invitación caducó, ya se usó o pertenece a otro correo.")
        workspace_id = invitation["workspace_id"]
        connection.execute("""
            INSERT INTO workspace_members (workspace_id, user_id) VALUES (?, ?)
            ON CONFLICT (workspace_id, user_id) DO NOTHING
        """, (workspace_id, user["id"]))
        return {"workspace_id": workspace_id}
