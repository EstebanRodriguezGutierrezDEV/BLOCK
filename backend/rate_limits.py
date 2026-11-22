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


def enforce_rate_limits(*checks: tuple[RateLimit, str | int]) -> None:
    now = int(time())
    retry_after = None
    with database(write=True) as connection:
        connection.execute("DELETE FROM request_limits WHERE resets_at <= ?", (now,))
        for policy, subject in checks:
            bucket = sha256(str(subject).encode()).hexdigest()
            resets_at = (now // policy.seconds + 1) * policy.seconds
            allowed = connection.execute("""
                INSERT INTO request_limits (action, bucket, resets_at, attempts) VALUES (?, ?, ?, 1)
                ON CONFLICT (action, bucket, resets_at) DO UPDATE SET attempts = request_limits.attempts + 1
                WHERE request_limits.attempts < ?
                RETURNING attempts
            """, (policy.action, bucket, resets_at, policy.attempts)).fetchone()
            if allowed is None:
                retry_after = resets_at - now
                break
    if retry_after is not None:
        raise HTTPException(
            429, "Demasiados intentos. Espera un momento antes de volver a intentarlo.",
            headers={"Retry-After": str(retry_after)},
        )
