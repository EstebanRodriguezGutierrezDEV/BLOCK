import secrets
from time import time
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path
from pydantic import EmailStr, StrictBool, StringConstraints

from .accounts import CurrentUser
from .db import PostgresConnection, database
from .rate_limits import WORKSPACE_CREATIONS, WORKSPACE_INVITES, enforce_rate_limits
from .request_security import APIInput, RecordID

router = APIRouter(prefix="/api")
INVITATION_LIFETIME = 7 * 24 * 60 * 60
INVITATION_RESEND_DELAY = 60


class WorkspaceCreate(APIInput):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class InvitationCreate(APIInput):
    email: EmailStr


class MemberPermissions(APIInput):
    can_change_status: StrictBool


def require_member(connection, workspace_id: int, user_id: int, *, write=False) -> dict:
    if write and isinstance(connection, PostgresConnection):
        locked = connection.execute("""
            SELECT id FROM workspaces WHERE id = ? AND EXISTS (
                SELECT 1 FROM workspace_members WHERE workspace_id = workspaces.id AND user_id = ?
            ) FOR UPDATE
        """, (workspace_id, user_id)).fetchone()
        if locked is None:
            raise HTTPException(404, "Espacio de trabajo no encontrado.")
    workspace = connection.execute("""
        SELECT w.id, w.name, w.owner_id, m.can_change_status
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
    enforce_rate_limits((WORKSPACE_CREATIONS, user["id"]))
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
def get_workspace(workspace_id: RecordID, user: CurrentUser):
    with database() as connection:
        workspace = require_member(connection, workspace_id, user["id"])
        members = connection.execute("""
            SELECT users.id, users.email, m.can_change_status
            FROM workspace_members m JOIN users ON users.id = m.user_id
            WHERE m.workspace_id = ? ORDER BY users.email
        """, (workspace_id,)).fetchall()
        owner = workspace["owner_id"] == user["id"]
        overrides = connection.execute("""
            SELECT task_id, can_change_status FROM task_status_permissions
            WHERE workspace_id = ? AND user_id = ?
        """, (workspace_id, user["id"])).fetchall() if not owner else []
        return {
            **workspace,
            "can_invite": owner,
            "can_change_status": owner or bool(workspace["can_change_status"]),
            "task_status_overrides": {row["task_id"]: bool(row["can_change_status"]) for row in overrides},
            "members": [
                {**dict(row), "can_change_status": row["id"] == workspace["owner_id"] or bool(row["can_change_status"])}
                for row in members
            ],
        }


@router.patch("/workspaces/{workspace_id}/members/{member_id}")
def update_member_permissions(workspace_id: RecordID, member_id: RecordID, data: MemberPermissions, user: CurrentUser):
    with database(write=True) as connection:
        workspace = require_member(connection, workspace_id, user["id"], write=True)
        if workspace["owner_id"] != user["id"]:
            raise HTTPException(403, "Solo el propietario puede cambiar los permisos.")
        if member_id == user["id"]:
            raise HTTPException(422, "El propietario siempre puede cambiar el estado.")
        updated = connection.execute("""
            UPDATE workspace_members SET can_change_status = ?
            WHERE workspace_id = ? AND user_id = ?
        """, (int(data.can_change_status), workspace_id, member_id))
        if not updated.rowcount:
            raise HTTPException(404, "El miembro no pertenece al espacio.")
        return {"can_change_status": data.can_change_status}


@router.post("/workspaces/{workspace_id}/invitations", status_code=201)
def invite_to_workspace(workspace_id: RecordID, data: InvitationCreate, user: CurrentUser):
    enforce_rate_limits((WORKSPACE_INVITES, user["id"]))
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

    return {"invited": True}


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
