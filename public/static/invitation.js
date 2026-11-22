import { request } from "./http.js";

const key = "todo-invitation";
const fragment = window.location.hash.slice(1);
const errorBox = document.querySelector("#invitation-error");
let token = /^[A-Za-z0-9_-]{43}$/.test(fragment) ? fragment : "";

try {
  if (token) {
    sessionStorage.setItem(key, token);
    history.replaceState(null, "", window.location.pathname);
  } else if (!fragment) {
    token = sessionStorage.getItem(key);
  }
} catch {}

if (!token) {
  errorBox.textContent = "Abre el enlace completo que te compartieron.";
  errorBox.hidden = false;
} else {
  document.querySelector("#invitation-actions").hidden = false;
  document.querySelector("#accept-invitation")?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    errorBox.hidden = true;
    try {
      const result = await request("/api/invitations/accept", {
        method: "POST", body: JSON.stringify({ token }),
      }, "/iniciar-sesion?invitacion=1");
      try {
        sessionStorage.removeItem(key);
      } catch {}
      window.location.replace("/espacios/" + result.workspace_id);
    } catch (error) {
      errorBox.textContent = error.message;
      errorBox.hidden = false;
      button.disabled = false;
    }
  });
}
