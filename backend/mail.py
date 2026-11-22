import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)


def messages_url() -> str:
    public_url = os.environ.get("TODO_PUBLIC_URL", "").strip().rstrip("/")
    if not public_url and (host := os.environ.get("VERCEL_PROJECT_PRODUCTION_URL")):
        public_url = f"https://{host}"
    parsed = urlsplit(public_url)
    if (
        parsed.scheme not in {"http", "https"} or not parsed.hostname
        or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment
    ):
        raise ValueError("URL pública inválida")
    return public_url + "/mensajes"


def send_workspace_notice(recipient: str, workspace_name: str, sender_email: str) -> str:
    host = os.environ.get("TODO_SMTP_HOST")
    origin = os.environ.get("TODO_SMTP_FROM")
    if not host or not origin:
        return "not_configured"

    try:
        security = os.environ.get("TODO_SMTP_SECURITY", "starttls").lower()
        if security not in {"starttls", "ssl"}:
            raise ValueError("Se requiere TLS en SMTP")
        default_port = "465" if security == "ssl" else "587"
        port = int(os.environ.get("TODO_SMTP_PORT", default_port))
        user = os.environ.get("TODO_SMTP_USER")
        password = os.environ.get("TODO_SMTP_PASSWORD")
        if bool(user) != bool(password):
            raise ValueError("Configuración SMTP incompleta")

        message = EmailMessage()
        message["Subject"] = "Tienes una invitación en Tu block de tareas"
        message["From"] = origin
        message["To"] = recipient
        message.set_content(
            f"{sender_email} te ha invitado al espacio «{workspace_name}».\n\n"
            "Entra con tu cuenta para responder desde Mensajes:\n"
            f"{messages_url()}\n\n"
            "La invitación caduca en siete días."
        )

        context = ssl.create_default_context()
        if security == "ssl":
            connection = smtplib.SMTP_SSL(host, port, timeout=5, context=context)
        else:
            connection = smtplib.SMTP(host, port, timeout=5)
        with connection as smtp:
            if security == "starttls":
                smtp.starttls(context=context)
            if user and password:
                smtp.login(user, password)
            smtp.send_message(message)
    except (ValueError, OSError) as error:
        logger.warning("No se pudo enviar un aviso de invitación por SMTP (%s)", type(error).__name__)
        return "failed"
    return "sent"
