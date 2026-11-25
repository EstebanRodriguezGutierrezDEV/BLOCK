from frontend.app import app
from frontend.deployment import configure_deployment

app.config["TODO_EMBEDDED_API"] = True
configure_deployment(app)
