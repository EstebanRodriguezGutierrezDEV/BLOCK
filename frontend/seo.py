import os
from urllib.parse import urlsplit
from xml.etree.ElementTree import Element, SubElement, tostring

from flask import Blueprint, Response, abort, url_for

pages = Blueprint("seo", __name__)
SITE_NAME = "Tu block de tareas"
SITE_TITLE = "Organizador de tareas con calendario | Tu block de tareas"
SITE_DESCRIPTION = (
    "Organiza tareas personales y compartidas en un calendario. "
    "Planifica fechas, mueve tarjetas y comparte comentarios y archivos con tu equipo."
)
PUBLIC_ENDPOINTS = {"index", "static", "favicon", "seo.robots", "seo.sitemap"}


def is_preview():
    return os.environ.get("VERCEL_ENV") in {"preview", "development"}


def public_origin():
    address = os.environ.get("TODO_PUBLIC_URL", "").strip()
    if not address and (host := os.environ.get("VERCEL_PROJECT_PRODUCTION_URL", "").strip()):
        address = "https://" + host
    if any(char.isspace() for char in address) or "\\" in address:
        return None
    try:
        parsed = urlsplit(address)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme not in {"https", "http"} or not parsed.hostname
        or parsed.username is not None or parsed.password is not None
        or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
        or port == 0
    ):
        return None
    return f"{parsed.scheme}://{parsed.netloc.lower()}"


def homepage_metadata():
    metadata = {"name": SITE_NAME, "title": SITE_TITLE, "description": SITE_DESCRIPTION}
    if origin := public_origin():
        canonical = origin + url_for("index")
        metadata.update(
            canonical=canonical,
            image=origin + url_for("static", filename="icons/social-preview.png"),
            website={"@context": "https://schema.org", "@type": "WebSite", "name": SITE_NAME, "url": canonical},
        )
        if not is_preview():
            metadata["verification"] = os.environ.get("TODO_GOOGLE_SITE_VERIFICATION", "").strip()
    return metadata


@pages.get("/robots.txt")
def robots():
    rules = "User-agent: *\nAllow: /\n"
    if not is_preview() and (origin := public_origin()):
        rules += f"\nSitemap: {origin}{url_for('seo.sitemap')}\n"
    return Response(rules, mimetype="text/plain")


@pages.get("/sitemap.xml")
def sitemap():
    origin = public_origin()
    if not origin or is_preview():
        abort(404)
    root = Element("urlset", xmlns="http://www.sitemaps.org/schemas/sitemap/0.9")
    SubElement(SubElement(root, "url"), "loc").text = origin + url_for("index")
    return Response(tostring(root, encoding="utf-8", xml_declaration=True), mimetype="application/xml")
