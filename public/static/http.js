export async function request(path, options = {}, loginPath = "/iniciar-sesion") {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);
  try {
    let response;
    try {
      response = await fetch(path, {
        ...options,
        signal: controller.signal,
        headers: options.body ? { "Content-Type": "application/json" } : {},
      });
    } catch (error) {
      throw new Error(error.name === "AbortError"
        ? "La conexión está tardando demasiado. Vuelve a intentarlo."
        : "No se pudo conectar con el servidor.");
    }
    if (response.status === 401) {
      window.location.replace(loginPath);
      throw new Error("Inicia sesión para continuar.");
    }
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      const detail = typeof error.detail === "string" ? error.detail
        : Array.isArray(error.detail) ? error.detail.map((item) => item.msg.replace(/^Value error, /, "")).join(". ")
        : "No se pudo completar la operación. Vuelve a intentarlo.";
      throw new Error(detail);
    }
    return response.status === 204 ? null : await response.json();
  } finally {
    clearTimeout(timeout);
  }
}
