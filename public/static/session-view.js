const view = document.querySelector('meta[name="session-view"]').content;
const cookieName = "todo_session_view=";
const root = document.documentElement;
let reloading = false;

function hide() {
  root.classList.add("session-hidden");
}

function reload() {
  if (reloading) return;
  reloading = true;
  hide();
  window.location.reload();
}

function checkSession() {
  if (reloading || root.hasAttribute("data-ending-session")) return;
  const cookie = document.cookie.split("; ").find((value) => value.startsWith(cookieName));
  if (cookie !== cookieName + view) {
    reload();
  } else {
    root.classList.toggle("session-hidden", document.hidden);
  }
}

document.addEventListener("submit", (event) => {
  if (!event.target.matches("form[data-ends-session]")) return;
  root.setAttribute("data-ending-session", "");
  hide();
  window.setTimeout(() => {
    root.removeAttribute("data-ending-session");
    checkSession();
  }, 15000);
});

window.addEventListener("pagehide", hide);
window.addEventListener("pageshow", (event) => {
  root.removeAttribute("data-ending-session");
  if (event.persisted) reload();
  else checkSession();
});
window.addEventListener("focus", checkSession);
document.addEventListener("visibilitychange", checkSession);
if (window.cookieStore) window.cookieStore.addEventListener("change", checkSession);
else window.setInterval(checkSession, 1500);
