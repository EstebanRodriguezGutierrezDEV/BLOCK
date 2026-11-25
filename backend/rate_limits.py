from hashlib import sha256
from ipaddress import IPv6Address, ip_address, ip_network
from time import time
from typing import NamedTuple

from fastapi import HTTPException, Request

from .db import database


class RateLimit(NamedTuple):
    action: str
    attempts: int
    seconds: int


LOGIN_IP = RateLimit("login_ip", 60, 300)
LOGIN_ACCOUNT = RateLimit("login_account", 12, 300)
REGISTER_IP = RateLimit("register_ip", 15, 3600)
WORKSPACE_INVITES = RateLimit("workspace_invite", 30, 3600)
FRIEND_REQUESTS = RateLimit("friend_request", 40, 3600)
CHAT_MESSAGES = RateLimit("direct_message", 120, 60)
ATTACHMENT_UPLOADS = RateLimit("attachment_upload", 30, 300)
ATTACHMENT_BYTES = RateLimit("attachment_bytes", 100 * 1024 * 1024, 86400)
AUTHENTICATED_WRITES = RateLimit("authenticated_write", 120, 60)
TASK_CREATIONS = RateLimit("task_create", 1000, 86400)
WORKSPACE_CREATIONS = RateLimit("workspace_create", 50, 86400)


def client_network(request: Request) -> str:
    host = request.client.host if request.client else ""
    try:
        address = ip_address(host)
    except ValueError:
        return "unknown"
    if isinstance(address, IPv6Address):
        if address.ipv4_mapped:
            return str(address.ipv4_mapped)
        return str(ip_network(f"{address}/64", strict=False))
    return str(address)


def enforce_rate_limits(*checks: tuple[RateLimit, str | int] | tuple[RateLimit, str | int, int]) -> None:
    now = int(time())
    retry_after = None
    with database(write=True) as connection:
        connection.execute("DELETE FROM request_limits WHERE resets_at <= ?", (now,))
        for check in checks:
            policy, subject = check[:2]
            amount = check[2] if len(check) == 3 else 1
            if amount == 0:
                continue
            if amount < 0:
                raise ValueError("El consumo del límite no puede ser negativo.")
            bucket = sha256(str(subject).encode()).hexdigest()
            resets_at = (now // policy.seconds + 1) * policy.seconds
            allowed = connection.execute("""
                INSERT INTO request_limits (action, bucket, resets_at, attempts)
                SELECT ?, ?, ?, ? WHERE ? <= ?
                ON CONFLICT (action, bucket, resets_at) DO UPDATE SET attempts = request_limits.attempts + excluded.attempts
                WHERE request_limits.attempts + excluded.attempts <= ?
                RETURNING attempts
            """, (policy.action, bucket, resets_at, amount, amount, policy.attempts, policy.attempts)).fetchone()
            if allowed is None:
                retry_after = resets_at - now
                break
    if retry_after is not None:
        raise HTTPException(
            429, "Demasiados intentos. Espera un momento antes de volver a intentarlo.",
            headers={"Retry-After": str(retry_after)},
        )
