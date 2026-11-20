"""Entrypoint WSGI para Vercel."""

from werkzeug.middleware.proxy_fix import ProxyFix

from frontend.app import app

app.config["TODO_EMBEDDED_API"] = True
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1)
