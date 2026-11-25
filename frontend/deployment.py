import os
from urllib.parse import urlsplit

from werkzeug.middleware.proxy_fix import ProxyFix


def configure_deployment(app):
    """Trust only the Vercel ingress and explicitly known deployment hosts."""
    if os.environ.get("VERCEL") != "1":
        return

    hosts = set()
    public_url = os.environ.get("TODO_PUBLIC_URL", "").strip()
    if public_url:
        parsed = urlsplit(public_url)
        if (
            parsed.scheme != "https" or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
            or parsed.port not in {None, 443}
            or any(char.isspace() for char in public_url) or "\\" in public_url
        ):
            raise ValueError("TODO_PUBLIC_URL debe ser un origen HTTPS sin ruta ni credenciales.")
        hosts.add(parsed.hostname)

    for name in ("VERCEL_URL", "VERCEL_BRANCH_URL", "VERCEL_PROJECT_PRODUCTION_URL"):
        host = os.environ.get(name, "").strip().lower()
        if host:
            if any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-." for char in host) or host.startswith("."):
                raise ValueError(f"{name} debe contener un nombre de dominio.")
            hosts.add(host)
    if not hosts:
        raise ValueError("Configura TODO_PUBLIC_URL o habilita las variables de sistema de Vercel.")

    app.config["TRUSTED_HOSTS"] = sorted(hosts)
    # ProxyFix defaults to trusting X-Forwarded-For. IP identity is instead read
    # exclusively from Vercel's own header in client_address().
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=0, x_proto=1)
