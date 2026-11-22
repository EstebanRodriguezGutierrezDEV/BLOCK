import asyncio
import os
from threading import Lock

import httpx
from psycopg import OperationalError

from backend.db import init_db
from backend.main import app as api_app

_init_lock = Lock()
_initialized = False


def ensure_schema():
    global _initialized
    with _init_lock:
        if not _initialized:
            init_db()
            _initialized = True


async def call_api(path, method, data, token):
    headers = {}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api_app), base_url="http://internal") as client:
        return await client.request(method, path, content=data, headers=headers)


def request_api(path, method, data, token):
    if not os.environ.get("DATABASE_URL"):
        return 503, b'{"detail":"Falta configurar DATABASE_URL en el despliegue."}'
    try:
        ensure_schema()
        response = asyncio.run(call_api(path, method, data, token))
    except OperationalError:
        return 503, b'{"detail":"No se pudo conectar con la base de datos."}'
    return response.status_code, response.content
