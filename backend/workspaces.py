import secrets
from time import time
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel, EmailStr, StringConstraints

from .accounts import CurrentUser
from .db import database
from .mail import send_workspace_notice

router = APIRouter(prefix="/api")
INVITATION_LIFETIME = 7 * 24 * 60 * 60
INVITATION_RESEND_DELAY = 60


class WorkspaceCreate(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class InvitationCreate(BaseModel):
    email: EmailStr


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
    invitation_id = secrets.token_hex(32)
    expires_at = int(time()) + INVITATION_LIFETIME
    with database(write=True) as connection:
        workspace = require_member(connection, workspace_id, user["id"])
        if workspace["owner_id"] != user["id"]:
            raise HTTPException(403, "Solo el propietario puede invitar a otras personas.")
        recipient = connection.execute(
            "SELECT id FROM users WHERE email = ? AND password_hash IS NOT NULL", (email,)
        ).fetchone()
        if recipient is None:
            raise HTTPException(404, "No hay una cuenta registrada con ese correo.")
        member = connection.execute("""
            SELECT 1 FROM workspace_members WHERE workspace_id = ? AND user_id = ?
        """, (workspace_id, recipient["id"])).fetchone()
        if member:
            raise HTTPException(409, "Ese correo ya pertenece al espacio.")
        created = connection.execute("""
            INSERT INTO workspace_invitations (workspace_id, email, token_hash, expires_at, recipient_id)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (workspace_id, email) DO UPDATE SET
                token_hash = excluded.token_hash, expires_at = excluded.expires_at, recipient_id = excluded.recipient_id
            WHERE workspace_invitations.recipient_id IS NULL OR workspace_invitations.expires_at <= ?
            RETURNING token_hash
        """, (workspace_id, email, invitation_id, expires_at, recipient["id"], expires_at - INVITATION_RESEND_DELAY)).fetchone()
        if created is None:
            raise HTTPException(409, "Ya enviaste una invitación a este correo. Espera un minuto para reenviarla.")

    delivery = send_workspace_notice(email, workspace["name"], user["email"])
    return {"email_delivery": delivery}


@router.get("/messages")
def list_messages(user: CurrentUser):
    with database() as connection:
        messages = connection.execute("""
            SELECT i.token_hash AS id, w.name AS workspace_name, u.email AS sender_email, i.expires_at
            FROM workspace_invitations i
            JOIN workspaces w ON w.id = i.workspace_id
            JOIN users u ON u.id = w.owner_id
            WHERE i.recipient_id = ? AND i.expires_at > ?
            ORDER BY i.expires_at DESC, i.token_hash
        """, (user["id"], int(time()))).fetchall()
        return [dict(message) for message in messages]


@router.post("/messages/{invitation_id}/{action}")
def respond_to_invitation(
    invitation_id: Annotated[str, Path(pattern=r"^[a-f0-9]{64}$")],
    action: Literal["accept", "reject"],
    user: CurrentUser,
):
    with database(write=True) as connection:
        invitation = connection.execute("""
            DELETE FROM workspace_invitations
            WHERE token_hash = ? AND recipient_id = ? AND expires_at > ?
            RETURNING workspace_id
        """, (invitation_id, user["id"], int(time()))).fetchone()
        if invitation is None:
            raise HTTPException(404, "Esta invitación ya no está disponible.")
        workspace_id = invitation["workspace_id"]
        if action == "accept":
            connection.execute("""
                INSERT INTO workspace_members (workspace_id, user_id) VALUES (?, ?)
                ON CONFLICT (workspace_id, user_id) DO NOTHING
            """, (workspace_id, user["id"]))
        return {"workspace_id": workspace_id} if action == "accept" else {"rejected": True}
