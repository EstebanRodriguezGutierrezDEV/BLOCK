export async function request(path, settings = {}, loginPath = "/iniciar-sesion") {
  const { headers: suppliedHeaders, responseType, timeoutMs = 15000, ...options } = settings;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  const headers = new Headers(suppliedHeaders);
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const sessionView = document.querySelector('meta[name="session-view"]');
  if (sessionView) headers.set("X-Todo-Session-View", sessionView.content);
  try {
    let response;
    try {
      response = await fetch(path, {
        ...options,
        signal: controller.signal,
        headers,
      });
    } catch (error) {
      throw new Error(error.name === "AbortError"
        ? "La conexión está tardando demasiado. Vuelve a intentarlo."
        : "No se pudo conectar con el servidor.");
    }
    const endingSession = document.documentElement.hasAttribute("data-ending-session");
    if (response.status === 409 && response.headers.has("X-Todo-Session-Changed")) {
      if (!endingSession) window.location.reload();
      throw new Error("La sesión ha cambiado.");
    }
    if (response.status === 401) {
      if (!endingSession) window.location.replace(loginPath);
      throw new Error("Inicia sesión para continuar.");
    }
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      const detail = typeof error.detail === "string" ? error.detail
        : Array.isArray(error.detail) ? error.detail.map((item) => item.msg.replace(/^Value error, /, "")).join(". ")
        : "No se pudo completar la operación. Vuelve a intentarlo.";
      throw new Error(detail);
    }
    if (responseType === "blob") return await response.blob();
    return response.status === 204 ? null : await response.json();
  } finally {
    clearTimeout(timeout);
  }
}
