from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import EmailStr, StringConstraints

from .accounts import CurrentUser
from .db import PostgresConnection, database, utc_timestamp
from .rate_limits import CHAT_MESSAGES, FRIEND_REQUESTS, enforce_rate_limits
from .request_security import APIInput, MAX_RECORD_ID, RecordID

router = APIRouter(prefix="/api")
CHAT_PAGE_SIZE = 50


class FriendRequest(APIInput):
    email: EmailStr


class DirectMessage(APIInput):
    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


def require_friend(connection, friendship_id: int, user_id: int, *, write=False) -> None:
    lock = " FOR UPDATE" if write and isinstance(connection, PostgresConnection) else ""
    allowed = connection.execute("""
        SELECT 1 FROM friendships
        WHERE id = ? AND status = 'accepted' AND ? IN (user_low, user_high)
    """ + lock, (friendship_id, user_id)).fetchone()
    if allowed is None:
        raise HTTPException(404, "Conversación no encontrada.")


@router.get("/friends")
def list_friends(user: CurrentUser):
    with database() as connection:
        rows = connection.execute("""
            SELECT f.id AS friendship_id, u.email,
                   CASE WHEN f.status = 'accepted' THEN 'friend'
                        WHEN f.requested_by = ? THEN 'sent' ELSE 'received' END AS status
            FROM friendships f
            JOIN users u ON u.id = CASE WHEN f.user_low = ? THEN f.user_high ELSE f.user_low END
            WHERE f.user_low = ? OR f.user_high = ?
            ORDER BY u.email
        """, (user["id"],) * 4).fetchall()
        return [dict(row) for row in rows]


@router.post("/friends", status_code=201)
def send_friend_request(data: FriendRequest, user: CurrentUser):
    enforce_rate_limits((FRIEND_REQUESTS, user["id"]))
    with database(write=True) as connection:
        recipient = connection.execute("""
            SELECT id FROM users WHERE email = ? AND password_hash IS NOT NULL
        """, (data.email.lower(),)).fetchone()
        if recipient is None:
            raise HTTPException(404, "No hay una cuenta registrada con ese correo.")
        if recipient["id"] == user["id"]:
            raise HTTPException(422, "Tu correo ya pertenece a tu cuenta.")
        low, high = sorted((user["id"], recipient["id"]))
        inserted = connection.execute("""
            INSERT INTO friendships (user_low, user_high, requested_by)
            VALUES (?, ?, ?) ON CONFLICT (user_low, user_high) DO NOTHING
            RETURNING id
        """, (low, high, user["id"])).fetchone()
        if inserted is None:
            raise HTTPException(409, "Ya sois amigos o hay una solicitud pendiente.")
        return {"friendship_id": inserted["id"]}


@router.post("/friends/{friendship_id}/{action}", status_code=204)
def respond_to_friend_request(friendship_id: RecordID, action: Literal["accept", "reject", "cancel"], user: CurrentUser):
    with database(write=True) as connection:
        if action == "accept":
            operation = "UPDATE friendships SET status = 'accepted'"
            requester = "requested_by <> ?"
        else:
            operation = "DELETE FROM friendships"
            requester = "requested_by = ?" if action == "cancel" else "requested_by <> ?"
        response = connection.execute(f"""
            {operation}
            WHERE id = ? AND status = 'pending' AND ? IN (user_low, user_high) AND {requester}
        """, (friendship_id, user["id"], user["id"]))
        if not response.rowcount:
            raise HTTPException(404, "Esta solicitud ya no está disponible.")
    return Response(status_code=204)


@router.get("/chats/{friendship_id}")
def list_chat_messages(
    friendship_id: RecordID, user: CurrentUser,
    before_id: Annotated[int | None, Query(ge=1, le=MAX_RECORD_ID)] = None,
    after_id: Annotated[int | None, Query(ge=0, le=MAX_RECORD_ID)] = None,
):
    if before_id is not None and after_id is not None:
        raise HTTPException(422, "Utiliza un solo límite para el historial.")
    with database() as connection:
        require_friend(connection, friendship_id, user["id"])
        cutoff, order = "", "DESC"
        values = [friendship_id]
        if before_id is not None:
            cutoff = "AND m.id < ?"
            values.append(before_id)
        if after_id is not None:
            cutoff, order = "AND m.id > ?", "ASC"
            values.append(after_id)
        values.append(CHAT_PAGE_SIZE + 1)
        rows = connection.execute(f"""
            SELECT m.id, m.body, m.created_at, u.email AS author_email
            FROM direct_messages m JOIN users u ON u.id = m.author_id
            WHERE m.friendship_id = ? {cutoff}
            ORDER BY m.id {order} LIMIT ?
        """, values).fetchall()
        page = rows[:CHAT_PAGE_SIZE]
        if order == "DESC":
            page.reverse()
        return {"messages": [dict(row) for row in page], "has_more": len(rows) > CHAT_PAGE_SIZE}


@router.post("/chats/{friendship_id}", status_code=201)
def send_message(friendship_id: RecordID, data: DirectMessage, user: CurrentUser):
    enforce_rate_limits((CHAT_MESSAGES, user["id"]))
    with database(write=True) as connection:
        require_friend(connection, friendship_id, user["id"], write=True)
        message = connection.execute("""
            INSERT INTO direct_messages (friendship_id, author_id, body, created_at)
            VALUES (?, ?, ?, ?) RETURNING id, body, created_at
        """, (friendship_id, user["id"], data.body, utc_timestamp())).fetchone()
        return {**dict(message), "author_email": user["email"]}
