import { request } from "./http.js";

const $ = (selector) => document.querySelector(selector);
const friendForm = $("#friend-form");
const chatForm = $("#chat-form");
const history = $("#chat-history");
const messageList = $("#chat-messages");
const olderButton = $("#chat-older");
const currentEmail = $("#main-content").dataset.userEmail;
const timestamp = new Intl.DateTimeFormat("es", { dateStyle: "short", timeStyle: "short" });
let activeChat = null;
let friendsBusy = false;

function show(element, text = "") {
  element.textContent = text;
  element.hidden = !text;
}

function node(tag, text, className = "") {
  const element = document.createElement(tag);
  element.textContent = text;
  element.className = className;
  return element;
}

function api(path, data) {
  const options = data === undefined ? undefined : { method: "POST", body: JSON.stringify(data) };
  return request(path, options, "/iniciar-sesion?volver=mensajes");
}

function button(label, action, className = "secondary-button") {
  const element = node("button", label, className);
  element.type = "button";
  element.addEventListener("click", action);
  return element;
}

function friendRow(friend) {
  const row = node("li", "", "friend-row");
  row.append(node("strong", friend.email));
  if (friend.status === "friend") {
    const open = button("Abrir chat", () => selectChat(friend), "text-button");
    open.setAttribute("aria-label", "Abrir chat con " + friend.email);
    row.append(open);
  } else if (friend.status === "received") {
    row.append(
      button("Aceptar solicitud", () => respond(friend, "accept")),
      button("Rechazar", () => respond(friend, "reject"), "text-button"),
    );
  } else {
    row.append(node("span", "Pendiente", "field-hint"), button("Cancelar solicitud", () => respond(friend, "cancel"), "text-button"));
  }
  return row;
}

async function loadFriends() {
  const entries = await api("/api/friends");
  const friends = entries.filter((friend) => friend.status === "friend");
  const pending = entries.filter((friend) => friend.status !== "friend");
  $("#friends-list").replaceChildren(...friends.map(friendRow));
  $("#friend-requests-list").replaceChildren(...pending.map(friendRow));
  $("#friend-requests").hidden = pending.length === 0;
  $("#friends-empty").hidden = friends.length > 0;
}

async function updateFriends(action) {
  if (friendsBusy) return;
  friendsBusy = true;
  const submit = friendForm.querySelector("button");
  submit.disabled = true;
  $("#friend-requests").inert = true;
  show($("#friends-error"));
  show($("#friends-status"));
  try {
    if (action) await action();
    await loadFriends();
  } catch (error) {
    show($("#friends-error"), error.message);
  } finally {
    friendsBusy = false;
    submit.disabled = false;
    $("#friend-requests").inert = false;
  }
}

function respond(friend, action) {
  updateFriends(async () => {
    await api(`/api/friends/${friend.friendship_id}/${action}`, {});
    const confirmations = { accept: "Solicitud aceptada.", reject: "Solicitud rechazada.", cancel: "Solicitud cancelada." };
    show($("#friends-status"), confirmations[action]);
  });
}

friendForm.addEventListener("submit", (event) => {
  event.preventDefault();
  updateFriends(async () => {
    await api("/api/friends", { email: $("#friend-email").value.trim() });
    friendForm.reset();
    show($("#friends-status"), "Solicitud de amistad enviada.");
  });
});

function messageItem(message) {
  const item = node("li", "", "chat-message");
  const own = message.author_email === currentEmail;
  item.classList.toggle("own", own);
  const time = node("time", timestamp.format(new Date(message.created_at)));
  time.dateTime = message.created_at;
  const meta = node("div", "", "chat-meta");
  meta.append(node("strong", own ? "Tú" : message.author_email), time);
  item.append(meta, node("p", message.body));
  return item;
}

function selectChat(friend) {
  if (activeChat?.id === friend.friendship_id) {
    $("#chat-body").focus();
    return;
  }
  activeChat = { id: friend.friendship_id, first: null, last: null, loading: null };
  $("#chat-placeholder").hidden = true;
  $("#chat-thread").hidden = false;
  $("#chat-friend").textContent = friend.email;
  show($("#chat-empty"), "Cargando conversación…");
  show($("#chat-error"));
  olderButton.hidden = true;
  olderButton.disabled = false;
  messageList.replaceChildren();
  chatForm.reset();
  chatForm.querySelector("button").disabled = false;
  refreshChat(activeChat);
  $("#chat-body").focus();
}

function refreshChat(chat, scrollToEnd = false) {
  if (!chat || chat !== activeChat) return;
  if (!chat.loading) {
    chat.loading = loadChat(chat, scrollToEnd).finally(() => { chat.loading = null; });
  }
  return chat.loading;
}

async function loadChat(chat, scrollToEnd) {
  const initial = chat.last === null;
  try {
    const path = "/api/chats/" + chat.id;
    const page = await api(initial ? path : path + "?after_id=" + chat.last);
    if (chat !== activeChat) return;
    const nearEnd = history.scrollHeight - history.scrollTop - history.clientHeight < 64;
    const first = page.messages[0];
    if (initial) {
      chat.first = first?.id ?? null;
      olderButton.hidden = !page.has_more;
    } else if (chat.first === null && first) {
      chat.first = first.id;
    }
    chat.last = page.messages.at(-1)?.id ?? chat.last ?? 0;
    messageList.append(...page.messages.map(messageItem));
    show($("#chat-empty"), chat.last === 0 ? "Aún no hay mensajes." : "");
    show($("#chat-error"));
    if (nearEnd || scrollToEnd) history.scrollTop = history.scrollHeight;
  } catch (error) {
    if (chat === activeChat) {
      show($("#chat-empty"));
      show($("#chat-error"), error.message);
    }
  }
}

olderButton.addEventListener("click", async () => {
  const chat = activeChat;
  olderButton.disabled = true;
  try {
    const page = await api(`/api/chats/${chat.id}?before_id=${chat.first}`);
    if (chat !== activeChat) return;
    const previousTop = history.scrollTop;
    const previousHeight = history.scrollHeight;
    messageList.prepend(...page.messages.map(messageItem));
    chat.first = page.messages[0]?.id ?? chat.first;
    olderButton.hidden = !page.has_more;
    history.scrollTop = previousTop + history.scrollHeight - previousHeight;
  } catch (error) {
    if (chat === activeChat) show($("#chat-error"), error.message);
  } finally {
    if (chat === activeChat) olderButton.disabled = false;
  }
});

chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const chat = activeChat;
  const input = $("#chat-body");
  const submit = chatForm.querySelector("button");
  const body = input.value.trim();
  if (!body || !chat || submit.disabled) return;
  submit.disabled = true;
  show($("#chat-error"));
  try {
    await api("/api/chats/" + chat.id, { body });
    if (chat !== activeChat) return;
    if (input.value.trim() === body) input.value = "";
    await chat.loading;
    await refreshChat(chat, true);
  } catch (error) {
    if (chat === activeChat) show($("#chat-error"), error.message);
  } finally {
    if (chat === activeChat) submit.disabled = false;
  }
});

$("#refresh-messages").addEventListener("click", () => {
  updateFriends();
  refreshChat(activeChat);
});
window.addEventListener("focus", () => {
  updateFriends();
  refreshChat(activeChat);
});
window.setInterval(() => {
  if (!document.hidden) refreshChat(activeChat);
}, 6000);
updateFriends();
