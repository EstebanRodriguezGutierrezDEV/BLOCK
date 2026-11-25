from html import escape
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from backend.privacy import PrivacyNotice


def create_privacy_pdf(notice: PrivacyNotice) -> BytesIO:
    output = BytesIO()
    document = SimpleDocTemplate(
        output, pagesize=A4, title="Aviso de privacidad · Tu block de tareas",
        author="Tu block de tareas", leftMargin=54, rightMargin=54, topMargin=42, bottomMargin=42,
    )
    styles = getSampleStyleSheet()
    styles["Heading1"].textColor = colors.HexColor("#202342")
    styles["Heading2"].textColor = colors.HexColor("#52359c")
    styles["BodyText"].leading = 14
    content = [
        Paragraph("Tu block de tareas", styles["Normal"]),
        Paragraph("Aviso de privacidad", styles["Heading1"]),
        Paragraph(f"Versión {notice.version}", styles["BodyText"]),
    ]
    if not notice.published:
        content.append(Paragraph("Borrador pendiente de publicación", styles["Heading2"]))
    content.append(Spacer(1, 8))
    for heading, body in notice.sections:
        content.append(Paragraph(escape(heading), styles["Heading2"]))
        content.append(Paragraph(escape(body), styles["BodyText"]))
    document.build(content)
    output.seek(0)
    return output
