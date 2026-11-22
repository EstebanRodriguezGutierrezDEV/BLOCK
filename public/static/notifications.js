import { request } from "./http.js";

const dialog = document.querySelector("#unread-dialog");
const description = document.querySelector("#unread-description");
const sessionView = document.querySelector('meta[name="session-view"]').content;
const dots = document.querySelectorAll("[data-unread-dot]");
let unreadByChat = new Map();
let pending = null;
let entryChecked = false;

function unreadText(count) {
  return count === 1 ? "1 mensaje sin leer" : `${count} mensajes sin leer`;
}

function firstEntry() {
  if (entryChecked) return false;
  entryChecked = true;
  try {
    const key = "todo.unread-entry";
    if (sessionStorage.getItem(key) === sessionView) return false;
    sessionStorage.setItem(key, sessionView);
  } catch {
    return true;
  }
  return true;
}

export function updateChatBadges() {
  for (const badge of document.querySelectorAll("[data-unread-chat]")) {
    const count = unreadByChat.get(Number(badge.dataset.unreadChat)) ?? 0;
    badge.hidden = !count;
    badge.textContent = count || "";
    const button = badge.closest("button");
    const label = button.dataset.chatLabel;
    button.setAttribute("aria-label", count ? `${label}. ${unreadText(count)}` : label);
  }
}

function showUnread({ total, chats }) {
  unreadByChat = new Map(chats.map(({ friendship_id, unread_count }) => [friendship_id, unread_count]));
  const label = total ? `Tienes ${unreadText(total)}.` : "";
  if (description.textContent !== label) description.textContent = label;
  for (const dot of dots) dot.hidden = !total;
  updateChatBadges();
  const entering = firstEntry();
  if (total && (entering || dialog.open)) {
    document.querySelector("#unread-summary").textContent = label;
    if (entering) dialog.showModal();
  } else if (!total && dialog.open) {
    dialog.close();
  }
}

export function refreshUnread(force = false) {
  if (document.hidden || document.documentElement.hasAttribute("data-ending-session")) return;
  if (pending && !force) return pending;
  const current = request("/api/chats/unread", undefined, dialog.dataset.loginUrl)
    .then((summary) => {
      if (pending === current && !document.hidden) showUnread(summary);
    })
    .catch(() => {})
    .finally(() => {
      if (pending === current) pending = null;
    });
  pending = current;
  return current;
}

document.querySelector("#open-unread").addEventListener("click", (event) => {
  if (location.pathname === event.currentTarget.pathname) {
    event.preventDefault();
    dialog.close();
    document.querySelector("#friends-title")?.focus();
  }
});
document.querySelector("#refresh-messages")?.addEventListener("click", () => refreshUnread());
document.addEventListener("visibilitychange", () => refreshUnread());
window.addEventListener("focus", () => refreshUnread());
window.setInterval(refreshUnread, 10000);
refreshUnread();
