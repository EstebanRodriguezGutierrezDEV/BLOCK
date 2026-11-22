const view = document.querySelector('meta[name="session-view"]').content;
const cookieName = "todo_session_view=";
let reloading = false;

function hide() {
  document.documentElement.classList.add("session-hidden");
}

function reload() {
  if (reloading) return;
  reloading = true;
  hide();
  window.location.reload();
}

function checkSession() {
  if (reloading) return;
  const cookie = document.cookie.split("; ").find((value) => value.startsWith(cookieName));
  if (cookie !== cookieName + view) {
    reload();
  } else {
    document.documentElement.classList.toggle("session-hidden", document.hidden);
  }
}

window.addEventListener("pagehide", hide);
window.addEventListener("pageshow", (event) => {
  if (event.persisted) reload();
  else checkSession();
});
window.addEventListener("focus", checkSession);
document.addEventListener("visibilitychange", checkSession);
if (window.cookieStore) window.cookieStore.addEventListener("change", checkSession);
else window.setInterval(checkSession, 1500);
