from dataclasses import dataclass
from hashlib import sha256
import json
import os

from email_validator import EmailNotValidError, validate_email


PRIVACY_NOTICE_DATE = "2026-11-23"
PRIVACY_ACKNOWLEDGEMENT_ERROR = "Confirma que has leído el aviso de privacidad vigente."


@dataclass(frozen=True)
class PrivacyNotice:
    controller: str
    contact: str
    sections: tuple[tuple[str, str], ...]
    version: str
    published: bool


def current_privacy_notice() -> PrivacyNotice:
    controller = os.environ.get("TODO_PRIVACY_CONTROLLER", "").strip()
    contact = os.environ.get("TODO_PRIVACY_CONTACT_EMAIL", "").strip()
    infrastructure = os.environ.get("TODO_PRIVACY_INFRASTRUCTURE", "").strip()
    if contact:
        try:
            contact = validate_email(contact, check_deliverability=False).normalized
        except EmailNotValidError:
            contact = ""

    sections = (
        (
            "Quién trata tus datos",
            f"Responsable del tratamiento: {controller or 'pendiente de identificar'}. "
            f"Correo de contacto para privacidad: {contact or 'pendiente de configurar'}.",
        ),
        (
            "Qué datos usamos y para qué",
            "Tu correo identifica tu cuenta y permite iniciar sesión, recibir invitaciones y conectar con otros "
            "usuarios. Guardamos una huella de la contraseña y la fecha y versión del aviso que confirmas al "
            "registrarte. Tratamos las tareas, mensajes, comentarios y archivos que decidas añadir para que "
            "puedas consultar tu información y usar las funciones compartidas.",
        ),
        (
            "Base jurídica",
            "Usamos los datos necesarios para prestar el servicio que solicitas (artículo 6.1.b del RGPD). "
            "El correo y la contraseña son necesarios para crear la cuenta. Usamos cookies de sesión y "
            "registros técnicos para mantener el acceso y contamos los intentos mediante identificadores "
            "derivados para prevenir abusos, en interés legítimo de proteger el servicio y sus usuarios "
            "(artículo 6.1.f). Registramos la confirmación de lectura para poder acreditar el cumplimiento "
            "del deber de información (artículos 5.2, 6.1.c y 13).",
        ),
        (
            "Quién puede recibir los datos",
            "Las personas con las que colaboras o conversas pueden ver tu correo y el contenido que compartas "
            "con ellas según los permisos de la app. Los proveedores de alojamiento "
            "y base de datos tratan los datos necesarios para prestar "
            "esos servicios.",
        ),
        (
            "Alojamiento, copias y transferencias internacionales",
            infrastructure or "Pendiente de confirmar los proveedores, la conservación de copias y, si hay "
            "transferencias fuera del Espacio Económico Europeo, los destinos y las garantías aplicables.",
        ),
        (
            "Cuánto tiempo se conservan",
            "Los datos de cuenta y la confirmación de lectura se conservan en la base activa mientras mantengas "
            "la cuenta. Puedes eliminarla desde tu panel. Se borran tus tareas personales, tus chats y tus "
            "comentarios y adjuntos de tareas. Las tareas compartidas pueden quedar en el espacio bajo la "
            "responsabilidad de otro miembro. Los contadores técnicos caducan al acabar su ventana de seguridad.",
        ),
        (
            "Tus derechos",
            "Puedes solicitar acceso, rectificación, supresión, limitación, portabilidad u oposición, según "
            "proceda, escribiendo al contacto del responsable. También puedes reclamar ante la autoridad de "
            "control competente, como la Agencia Española de Protección de Datos (www.aepd.es) en España.",
        ),
    )
    digest = sha256(json.dumps(sections, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
    return PrivacyNotice(
        controller, contact, sections, f"{PRIVACY_NOTICE_DATE}-{digest}", bool(controller and contact and infrastructure),
    )
