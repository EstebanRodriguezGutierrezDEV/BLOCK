import { request } from "./http.js";
import { refreshUnread, updateChatBadges } from "./notifications.js";
import { formatFileSize, saveDownload } from "./files.js";

const $ = (selector) => document.querySelector(selector);
const friendForm = $("#friend-form");
const chatForm = $("#chat-form");
const fileForm = $("#chat-file-form");
const fileInput = $("#chat-file");
const sendFile = $("#send-chat-file");
const clearFile = $("#clear-chat-file");
const chatLogin = "/iniciar-sesion?volver=mensajes";
const history = $("#chat-history");
const messageList = $("#chat-messages");
const olderButton = $("#chat-older");
const currentEmail = $("#main-content").dataset.userEmail;
const messagesApp = $("#messages-app");
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
  return request(path, options, chatLogin);
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
    open.dataset.chatLabel = "Abrir chat con " + friend.email;
    open.dataset.chatId = friend.friendship_id;
    const unread = node("span", "", "chat-unread-count");
    unread.dataset.unreadChat = friend.friendship_id;
    unread.setAttribute("aria-hidden", "true");
    unread.hidden = true;
    open.append(unread);
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
  updateChatBadges();
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

function attachmentItem(chat, message) {
  const file = message.attachment;
  const path = `/api/chats/${chat.id}/attachments/${message.id}`;
  const item = node("div", "", "chat-attachment");
  if (file.preview) {
    const preview = document.createElement("img");
    preview.className = "chat-attachment-preview";
    preview.alt = "Vista previa de " + file.file_name;
    preview.width = 300;
    preview.height = 225;
    preview.loading = "lazy";
    preview.decoding = "async";
    preview.addEventListener("error", () => preview.remove(), { once: true });
    preview.src = path + "/preview";
    item.append(preview);
  }
  const download = button("Descargar", async () => {
    download.disabled = true;
    try {
      const blob = await request(path, { responseType: "blob", timeoutMs: 60000 }, chatLogin);
      saveDownload(blob, file.file_name);
    } catch (error) {
      if (activeChat === chat) show($("#chat-error"), error.message);
    } finally {
      download.disabled = false;
    }
  }, "text-button");
  download.setAttribute("aria-label", "Descargar " + file.file_name);
  item.append(
    node("strong", file.file_name, "chat-attachment-name"),
    node("span", formatFileSize(file.size_bytes), "attachment-detail"),
    download,
  );
  return item;
}

function messageItem(chat, message) {
  const item = node("li", "", "chat-message");
  const own = message.author_email === currentEmail;
  item.classList.toggle("own", own);
  const time = node("time", timestamp.format(new Date(message.created_at)));
  time.dateTime = message.created_at;
  const meta = node("div", "", "chat-meta");
  meta.append(node("strong", own ? "Tú" : message.author_email), time);
  item.append(meta);
  if (message.body) item.append(node("p", message.body));
  if (message.attachment) item.append(attachmentItem(chat, message));
  return item;
}

function renderFileSelection() {
  const file = fileInput.files[0];
  show($("#chat-file-name"), file ? `${file.name} · ${formatFileSize(file.size)}` : "");
  clearFile.hidden = !file;
  clearFile.disabled = fileInput.disabled;
  sendFile.hidden = !file;
  sendFile.disabled = !file || fileInput.disabled || file.size > Number(fileForm.dataset.limit);
}

function resetFileSelection() {
  fileForm.reset();
  fileInput.disabled = false;
  sendFile.textContent = "Enviar archivo";
  show($("#chat-file-error"));
  renderFileSelection();
}

function selectChat(friend) {
  if (activeChat?.id === friend.friendship_id) {
    $("#chat-body").focus();
    return;
  }
  activeChat = { id: friend.friendship_id, first: null, last: null, loading: null, received: 0, read: 0, marking: false };
  messagesApp.classList.add("chat-open");
  $("#chat-placeholder").hidden = true;
  $("#chat-thread").hidden = false;
  $("#chat-friend").textContent = friend.email;
  show($("#chat-empty"), "Cargando conversación…");
  show($("#chat-error"));
  olderButton.hidden = true;
  olderButton.disabled = false;
  messageList.replaceChildren();
  chatForm.reset();
  resetFileSelection();
  chatForm.querySelector("button").disabled = false;
  refreshChat(activeChat);
  $("#chat-friend").focus({ preventScroll: true });
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
    const nearEnd = atEnd();
    const first = page.messages[0];
    if (initial) {
      chat.first = first?.id ?? null;
      olderButton.hidden = !page.has_more;
    } else if (chat.first === null && first) {
      chat.first = first.id;
    }
    chat.last = page.messages.at(-1)?.id ?? chat.last ?? 0;
    for (const item of page.messages) {
      if (item.author_email !== currentEmail) chat.received = item.id;
    }
    messageList.append(...page.messages.map((item) => messageItem(chat, item)));
    show($("#chat-empty"), chat.last === 0 ? "Aún no hay mensajes." : "");
    show($("#chat-error"));
    if (nearEnd || scrollToEnd) history.scrollTop = history.scrollHeight;
    markRead(chat);
  } catch (error) {
    if (chat === activeChat) {
      show($("#chat-empty"));
      show($("#chat-error"), error.message);
    }
  }
}

function atEnd() {
  return history.scrollHeight - history.scrollTop - history.clientHeight < 64;
}

async function markRead(chat) {
  if (!chat || chat !== activeChat || chat.marking || chat.received <= chat.read
      || document.hidden || !document.hasFocus() || $("#unread-dialog").open || !atEnd()) return;
  const messageId = chat.received;
  chat.marking = true;
  try {
    await api(`/api/chats/${chat.id}/read`, { message_id: messageId });
    chat.read = messageId;
    refreshUnread(true);
  } catch {
    return;
  } finally {
    chat.marking = false;
  }
}

history.addEventListener("scroll", () => markRead(activeChat));
$("#unread-dialog").addEventListener("close", () => markRead(activeChat));

$("#back-to-friends").addEventListener("click", () => {
  const id = activeChat?.id;
  activeChat = null;
  messagesApp.classList.remove("chat-open");
  $("#chat-thread").hidden = true;
  $("#chat-placeholder").hidden = false;
  resetFileSelection();
  ($(`[data-chat-id="${id}"]`) || $("#friends-title")).focus();
});

olderButton.addEventListener("click", async () => {
  const chat = activeChat;
  olderButton.disabled = true;
  try {
    const page = await api(`/api/chats/${chat.id}?before_id=${chat.first}`);
    if (chat !== activeChat) return;
    const previousTop = history.scrollTop;
    const previousHeight = history.scrollHeight;
    messageList.prepend(...page.messages.map((item) => messageItem(chat, item)));
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

fileInput.addEventListener("change", () => {
  const file = fileInput.files[0];
  show($("#chat-file-error"), file?.size > Number(fileForm.dataset.limit) ? $("#chat-file-limit").textContent : "");
  renderFileSelection();
});
clearFile.addEventListener("click", () => {
  resetFileSelection();
  fileInput.focus();
});
fileForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const chat = activeChat;
  const file = fileInput.files[0];
  if (!chat || !file || sendFile.disabled) return;

  fileInput.disabled = true;
  sendFile.textContent = "Enviando archivo…";
  show($("#chat-file-error"));
  renderFileSelection();
  try {
    const query = new URLSearchParams({ file_name: file.name });
    await request(`/api/chats/${chat.id}/attachments?${query}`, {
      method: "POST", body: file, headers: { "Content-Type": "application/octet-stream" }, timeoutMs: 60000,
    }, chatLogin);
    if (chat === activeChat) {
      fileForm.reset();
      renderFileSelection();
      await chat.loading;
      await refreshChat(chat, true);
    }
  } catch (error) {
    if (chat === activeChat) show($("#chat-file-error"), error.message);
  } finally {
    if (chat === activeChat) {
      fileInput.disabled = false;
      sendFile.textContent = "Enviar archivo";
      renderFileSelection();
    }
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
