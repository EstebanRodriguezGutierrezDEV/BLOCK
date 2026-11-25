from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import EmailStr, Field, StringConstraints

from .accounts import CurrentUser
from .attachment_policy import AttachmentName, attachment_response, clean_attachment_name, preview_image_type
from .db import PostgresConnection, database, utc_timestamp
from .rate_limits import ATTACHMENT_BYTES, ATTACHMENT_UPLOADS, CHAT_MESSAGES, FRIEND_REQUESTS, enforce_rate_limits
from .request_security import APIInput, MAX_RECORD_ID, RecordID

router = APIRouter(prefix="/api")
CHAT_PAGE_SIZE = 50


class FriendRequest(APIInput):
    email: EmailStr


class DirectMessage(APIInput):
    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class ReadMessage(APIInput):
    message_id: int = Field(ge=1, le=MAX_RECORD_ID)


def require_friend(connection, friendship_id: int, user_id: int, *, write=False) -> None:
    lock = " FOR UPDATE" if write and isinstance(connection, PostgresConnection) else ""
    allowed = connection.execute("""
        SELECT 1 FROM friendships
        WHERE id = ? AND status = 'accepted' AND ? IN (user_low, user_high)
    """ + lock, (friendship_id, user_id)).fetchone()
    if allowed is None:
        raise HTTPException(404, "Conversación no encontrada.")


def create_message(connection, friendship_id: int, user: dict, body: str) -> dict:
    require_friend(connection, friendship_id, user["id"], write=True)
    message = connection.execute("""
        INSERT INTO direct_messages (friendship_id, author_id, body, created_at)
        VALUES (?, ?, ?, ?) RETURNING id, body, created_at
    """, (friendship_id, user["id"], body, utc_timestamp())).fetchone()
    return {**dict(message), "author_email": user["email"]}


def message_with_attachment(message: dict) -> dict:
    file_name = message.pop("file_name")
    size_bytes = message.pop("size_bytes")
    preview_type = message.pop("preview_type")
    if file_name is not None:
        message["attachment"] = {"file_name": file_name, "size_bytes": size_bytes, "preview": bool(preview_type)}
    return message


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


@router.get("/chats/unread")
def unread_messages(user: CurrentUser):
    with database() as connection:
        rows = connection.execute("""
            SELECT f.id AS friendship_id, COUNT(*) AS unread_count
            FROM friendships f
            LEFT JOIN direct_message_reads r ON r.friendship_id = f.id AND r.user_id = ?
            JOIN direct_messages m ON m.friendship_id = f.id
                AND m.id > COALESCE(r.last_read_id, 0) AND m.author_id <> ?
            WHERE f.status = 'accepted' AND (f.user_low = ? OR f.user_high = ?)
            GROUP BY f.id
            ORDER BY f.id
        """, (user["id"],) * 4).fetchall()
    chats = [dict(row) for row in rows]
    return {"total": sum(chat["unread_count"] for chat in chats), "chats": chats}


@router.post("/chats/{friendship_id}/read", status_code=204)
def mark_chat_read(friendship_id: RecordID, data: ReadMessage, user: CurrentUser):
    with database(write=True) as connection:
        require_friend(connection, friendship_id, user["id"])
        message = connection.execute("""
            SELECT id FROM direct_messages WHERE friendship_id = ? AND id = ?
        """, (friendship_id, data.message_id)).fetchone()
        if message is None:
            raise HTTPException(404, "Mensaje no encontrado.")
        connection.execute("""
            INSERT INTO direct_message_reads (friendship_id, user_id, last_read_id)
            VALUES (?, ?, ?)
            ON CONFLICT (friendship_id, user_id) DO UPDATE SET last_read_id = excluded.last_read_id
            WHERE direct_message_reads.last_read_id < excluded.last_read_id
        """, (friendship_id, user["id"], message["id"]))
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
            SELECT m.id, m.body, m.created_at, u.email AS author_email, a.file_name, a.size_bytes, a.preview_type
            FROM direct_messages m JOIN users u ON u.id = m.author_id
            LEFT JOIN direct_message_attachments a ON a.message_id = m.id
            WHERE m.friendship_id = ? {cutoff}
            ORDER BY m.id {order} LIMIT ?
        """, values).fetchall()
        page = rows[:CHAT_PAGE_SIZE]
        if order == "DESC":
            page.reverse()
        return {"messages": [message_with_attachment(dict(row)) for row in page], "has_more": len(rows) > CHAT_PAGE_SIZE}


@router.post("/chats/{friendship_id}", status_code=201)
def send_message(friendship_id: RecordID, data: DirectMessage, user: CurrentUser):
    enforce_rate_limits((CHAT_MESSAGES, user["id"]))
    with database(write=True) as connection:
        return create_message(connection, friendship_id, user, data.body)


def store_chat_attachment(friendship_id: int, user: dict, file_name: str, content: bytes) -> dict:
    preview_type = preview_image_type(content)
    with database(write=True) as connection:
        message = create_message(connection, friendship_id, user, "")
        attachment = connection.execute("""
            INSERT INTO direct_message_attachments (message_id, file_name, size_bytes, preview_type, content)
            VALUES (?, ?, ?, ?, ?) RETURNING file_name, size_bytes, preview_type
        """, (message["id"], file_name, len(content), preview_type, content)).fetchone()
        return message_with_attachment({**message, **dict(attachment)})


@router.post("/chats/{friendship_id}/attachments", status_code=201)
async def upload_chat_attachment(friendship_id: RecordID, user: CurrentUser, request: Request, file_name: AttachmentName):
    file_name = clean_attachment_name(file_name)
    await run_in_threadpool(enforce_rate_limits, (ATTACHMENT_UPLOADS, user["id"]), (CHAT_MESSAGES, user["id"]))
    content = await request.body()
    await run_in_threadpool(enforce_rate_limits, (ATTACHMENT_BYTES, user["id"], len(content)))
    return await run_in_threadpool(store_chat_attachment, friendship_id, user, file_name, content)


def read_chat_attachment(friendship_id: int, message_id: int, user: dict, *, preview=False) -> Response:
    with database() as connection:
        require_friend(connection, friendship_id, user["id"])
        attachment = connection.execute("""
            SELECT a.file_name, a.preview_type, a.content
            FROM direct_message_attachments a JOIN direct_messages m ON m.id = a.message_id
            WHERE m.friendship_id = ? AND m.id = ?
        """, (friendship_id, message_id)).fetchone()
    if attachment is None or (preview and not attachment["preview_type"]):
        raise HTTPException(404, "Archivo no encontrado.")
    media_type = attachment["preview_type"] if preview else None
    return attachment_response(attachment["file_name"], attachment["content"], preview_type=media_type)


@router.get("/chats/{friendship_id}/attachments/{message_id}")
def download_chat_attachment(friendship_id: RecordID, message_id: RecordID, user: CurrentUser):
    return read_chat_attachment(friendship_id, message_id, user)


@router.get("/chats/{friendship_id}/attachments/{message_id}/preview")
def preview_chat_attachment(friendship_id: RecordID, message_id: RecordID, user: CurrentUser):
    return read_chat_attachment(friendship_id, message_id, user, preview=True)
