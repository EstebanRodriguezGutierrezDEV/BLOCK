import { request } from "./http.js";

const list = document.querySelector("#messages-list");
const status = document.querySelector("#messages-status");
const errorBox = document.querySelector("#messages-error");
const empty = document.querySelector("#messages-empty");
const refresh = document.querySelector("#refresh-messages");
const expiryFormat = new Intl.DateTimeFormat("es", { dateStyle: "medium" });
let responding = false;

function show(element, text = "") {
  element.textContent = text;
  element.hidden = !text;
}

function element(tag, text, className = "") {
  const node = document.createElement(tag);
  node.textContent = text;
  node.className = className;
  return node;
}

function api(path, options) {
  return request(path, options, "/iniciar-sesion?volver=mensajes");
}

async function respond(invitation, action, buttons) {
  if (responding) return;
  responding = true;
  refresh.disabled = true;
  buttons.forEach((button) => { button.disabled = true; });
  show(errorBox);
  try {
    const result = await api("/api/messages/" + invitation.id + "/" + action, {
      method: "POST", body: "{}",
    });
    if (action === "accept") {
      window.location.assign("/espacios/" + result.workspace_id);
      return;
    }
    await loadMessages();
    show(status, "Invitación rechazada.");
  } catch (error) {
    show(errorBox, error.message);
  } finally {
    responding = false;
    refresh.disabled = false;
    buttons.forEach((button) => { button.disabled = false; });
  }
}

function messageCard(invitation) {
  const item = element("li", "", "inbox-card");
  const summary = element("div", "", "inbox-summary");
  summary.append(
    element("h2", invitation.workspace_name),
    element("p", invitation.sender_email + " te ha invitado a colaborar."),
    element("p", "Caduca el " + expiryFormat.format(new Date(invitation.expires_at * 1000)), "inbox-expiry"),
  );
  const actions = element("div", "", "inbox-actions");
  const accept = element("button", "Aceptar", "primary-button");
  const reject = element("button", "Rechazar", "secondary-button");
  accept.type = reject.type = "button";
  accept.addEventListener("click", () => respond(invitation, "accept", [accept, reject]));
  reject.addEventListener("click", () => respond(invitation, "reject", [accept, reject]));
  actions.append(reject, accept);
  item.append(summary, actions);
  return item;
}

async function loadMessages() {
  refresh.disabled = true;
  show(errorBox);
  show(status, "Cargando invitaciones…");
  try {
    const invitations = await api("/api/messages");
    list.replaceChildren(...invitations.map(messageCard));
    empty.hidden = invitations.length !== 0;
  } catch (error) {
    show(errorBox, error.message);
  } finally {
    show(status);
    refresh.disabled = false;
  }
}

refresh.addEventListener("click", () => {
  if (!responding) loadMessages();
});
loadMessages();
