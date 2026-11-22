from typing import Annotated
from urllib.parse import quote

from fastapi import HTTPException, Query, Response

MAX_ATTACHMENT_BYTES = 4 * 1024 * 1024
ATTACHMENT_LIMIT_MESSAGE = "Cada archivo puede ocupar hasta 4 MiB."
AttachmentName = Annotated[str, Query(min_length=1, max_length=255)]
PREVIEW_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp"}


def clean_attachment_name(name: str) -> str:
    name = "".join(char for char in name if char.isprintable()).strip()
    if name in {"", ".", ".."} or "/" in name or "\\" in name:
        raise HTTPException(422, "El nombre del archivo no es válido.")
    return name


def preview_image_type(content: bytes) -> str | None:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if content.startswith(b"RIFF") and content[8:14] == b"WEBPVP":
        return "image/webp"
    if content.startswith(b"BM"):
        return "image/bmp"
    return None


def attachment_response(file_name: str, content: bytes, *, preview_type: str | None = None) -> Response:
    encoded_name = quote(file_name, safe="")
    disposition = "inline" if preview_type in PREVIEW_IMAGE_TYPES else "attachment"
    media_type = preview_type if disposition == "inline" else "application/octet-stream"
    return Response(
        content=content, media_type=media_type,
        headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{encoded_name}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )
