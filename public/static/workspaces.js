import { request } from "./http.js";

const $ = (selector) => document.querySelector(selector);
const columns = [...document.querySelectorAll("[data-column]")];
const statusNames = { todo: "Por hacer", doing: "En curso", done: "Hecho" };
const dateFormat = new Intl.DateTimeFormat("es", { dateStyle: "medium", timeZone: "UTC" });
const commentFormat = new Intl.DateTimeFormat("es", { dateStyle: "medium", timeStyle: "short" });
const workspaceDialog = $("#workspace-dialog");
const inviteDialog = $("#invite-dialog");
const cardDialog = $("#card-dialog");
const deleteDialog = $("#delete-card-dialog");

let workspaceId = Number($("#shared-app").dataset.workspaceId) || null;
let tasks = [];
let activeCard = null;
let draggedId = null;
let boardRevision = 0;
let commentsRequest = 0;
const movingCards = new Set();

function message(element, body = "") {
  element.textContent = body;
  element.hidden = !body;
}

function node(tag, className, text) {
  const element = document.createElement(tag);
  element.className = className;
  element.textContent = text;
  return element;
}

function taskPath(id = null, comments = false) {
  const task = id === null ? "" : "/" + id + (comments ? "/comments" : "");
  return "/api/tasks" + task + "?workspace_id=" + workspaceId;
}

function json(method, data) {
  return { method, body: JSON.stringify(data) };
}

function todayISO() {
  const date = new Date();
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 10);
}

function formatDate(date) {
  return dateFormat.format(new Date(date + "T00:00:00Z"));
}

function renderLinks(spaces) {
  $("#workspace-links").replaceChildren(...spaces.map((space) => {
    const link = node("a", "workspace-link", space.name);
    link.href = "/espacios/" + space.id;
    if (space.id === workspaceId) link.setAttribute("aria-current", "page");
    return link;
  }));
}

function renderMembers(workspace) {
  const members = workspace.members.map((member) => {
    const item = node("li", "workspace-member", member.email);
    if (member.id === workspace.owner_id) item.append(node("span", "owner-label", "Propietario"));
    return item;
  });
  $("#workspace-members").replaceChildren(...members);
}

async function refreshBoard() {
  const button = $("#refresh-board");
  button.disabled = true;
  message($("#board-error"));
  const revision = boardRevision;
  try {
    const [workspace, cards] = await Promise.all([
      request("/api/workspaces/" + workspaceId),
      request(taskPath()),
    ]);
    $("#workspace-name").textContent = workspace.name;
    document.title = workspace.name + " · Tu block de tareas";
    renderMembers(workspace);
    $("#invite-button").hidden = !workspace.can_invite;
    $("#board-actions").hidden = false;
    $("#board").hidden = false;
    if (revision === boardRevision) {
      tasks = cards;
      renderBoard();
    }
  } catch (error) {
    message($("#board-error"), error.message);
  } finally {
    button.disabled = false;
  }
}

async function start() {
  try {
    const spaces = await request("/api/workspaces");
    if (!workspaceId && spaces.length) {
      workspaceId = spaces[0].id;
      history.replaceState(null, "", "/espacios/" + workspaceId);
    }
    renderLinks(spaces);
    if (workspaceId) await refreshBoard();
    else $("#no-spaces").hidden = false;
  } catch (error) {
    message($("#board-error"), error.message);
  } finally {
    $("#board-loading").hidden = true;
  }
}

function createCard(task) {
  const card = node("li", "kanban-card", "");
  card.dataset.cardId = task.id;
  card.draggable = true;

  const title = node("button", "kanban-card-title", task.title);
  title.type = "button";
  title.addEventListener("click", () => openCard(task));
  card.append(title);
  if (task.description) card.append(node("p", "kanban-description", task.description));

  const range = formatDate(task.start_date) + " – " + formatDate(task.end_date);
  card.append(node("p", "kanban-date", range));
  const actions = node("div", "kanban-card-actions", "");
  const discussion = node("button", "text-button", "Comentarios");
  discussion.type = "button";
  discussion.setAttribute("aria-label", "Comentarios de " + task.title);
  discussion.addEventListener("click", () => {
    openCard(task);
    $("#comment-body").focus();
  });
  const move = document.createElement("select");
  move.setAttribute("aria-label", "Mover " + task.title);
  for (const [key, label] of Object.entries(statusNames)) {
    move.append(new Option(label, key, false, key === task.board_status));
  }
  move.disabled = movingCards.has(task.id);
  move.addEventListener("change", () => moveCard(task.id, move.value, true));
  actions.append(discussion, move);
  card.append(actions);

  card.addEventListener("dragstart", (event) => {
    draggedId = task.id;
    event.dataTransfer.setData("text/plain", String(task.id));
    event.dataTransfer.effectAllowed = "move";
    card.classList.add("dragging");
  });
  card.addEventListener("dragend", () => {
    draggedId = null;
    card.classList.remove("dragging");
    columns.forEach((column) => column.classList.remove("drop-target"));
  });
  return card;
}

function renderBoard() {
  for (const column of columns) {
    const cards = tasks.filter((task) => task.board_status === column.dataset.column);
    column.querySelector(".kanban-count").textContent = cards.length;
    column.querySelector(".column-empty").hidden = cards.length > 0;
    column.querySelector(".kanban-list").replaceChildren(...cards.map(createCard));
  }
}

function saveCard(task) {
  const exists = tasks.some((current) => current.id === task.id);
  tasks = exists ? tasks.map((current) => current.id === task.id ? task : current) : [task, ...tasks];
  boardRevision += 1;
  renderBoard();
}

async function moveCard(id, status, restoreFocus = false) {
  const current = tasks.find((task) => task.id === id);
  if (!current || current.board_status === status || movingCards.has(id)) return;
  movingCards.add(id);
  const control = document.querySelector('[data-card-id="' + id + '"] select');
  if (control) control.disabled = true;
  message($("#board-error"));
  try {
    const changed = await request(taskPath(id), json("PATCH", { board_status: status }));
    saveCard(changed);
  } catch (error) {
    message($("#board-error"), error.message);
    renderBoard();
  } finally {
    movingCards.delete(id);
    const updated = document.querySelector('[data-card-id="' + id + '"] select');
    if (updated) {
      updated.disabled = false;
      if (restoreFocus) updated.focus();
    }
  }
}

for (const column of columns) {
  column.querySelector(".column-add").addEventListener("click", () => openCard(null, column.dataset.column));
  column.addEventListener("dragover", (event) => {
    if (!draggedId) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    column.classList.add("drop-target");
  });
  column.addEventListener("dragleave", (event) => {
    if (!column.contains(event.relatedTarget)) column.classList.remove("drop-target");
  });
  column.addEventListener("drop", (event) => {
    if (!draggedId) return;
    event.preventDefault();
    column.classList.remove("drop-target");
    const id = draggedId;
    draggedId = null;
    document.querySelector(".kanban-card.dragging")?.classList.remove("dragging");
    moveCard(id, column.dataset.column);
  });
}

function handleForm(selector, submit) {
  const form = $(selector);
  const submitButton = form.querySelector('[type="submit"]');
  const errorBox = form.querySelector(".dialog-error");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (submitButton.disabled) return;
    message(errorBox);
    submitButton.disabled = true;
    try {
      await submit(Object.fromEntries(new FormData(form)), form);
    } catch (error) {
      message(errorBox, error.message);
    } finally {
      submitButton.disabled = false;
    }
  });
}

function dialogIsBusy(dialog) {
  return Boolean(dialog.querySelector('[type="submit"]:disabled'));
}

for (const dialog of [workspaceDialog, inviteDialog, cardDialog, deleteDialog]) {
  dialog.querySelectorAll("[data-close]").forEach((button) => {
    button.addEventListener("click", () => {
      if (!dialogIsBusy(dialog)) dialog.close();
    });
  });
  dialog.addEventListener("cancel", (event) => {
    if (dialogIsBusy(dialog)) event.preventDefault();
  });
}

function openWorkspaceDialog() {
  $("#workspace-form").reset();
  message(workspaceDialog.querySelector(".dialog-error"));
  workspaceDialog.showModal();
}

$("#create-workspace-button").addEventListener("click", openWorkspaceDialog);
$("#empty-create-workspace").addEventListener("click", openWorkspaceDialog);
$("#refresh-board").addEventListener("click", refreshBoard);

handleForm("#workspace-form", async ({ name }) => {
  const workspace = await request("/api/workspaces", json("POST", { name: name.trim() }));
  window.location.assign("/espacios/" + workspace.id);
});

$("#invite-button").addEventListener("click", () => {
  $("#invite-form").reset();
  $("#invitation-result").hidden = true;
  $("#invite-link").value = "";
  message($("#invite-copy-status"));
  message(inviteDialog.querySelector(".dialog-error"));
  inviteDialog.showModal();
});

handleForm("#invite-form", async ({ email }) => {
  $("#invitation-result").hidden = true;
  $("#invite-link").value = "";
  const invite = await request("/api/workspaces/" + workspaceId + "/invitations", json("POST", { email }));
  $("#invite-link").value = new URL("/invitacion#" + invite.token, window.location.origin).href;
  $("#invitation-result").hidden = false;
  message($("#invite-copy-status"));
});

$("#copy-invite").addEventListener("click", async () => {
  const input = $("#invite-link");
  try {
    await navigator.clipboard.writeText(input.value);
    message($("#invite-copy-status"), "Enlace copiado.");
  } catch {
    input.focus();
    input.select();
    message($("#invite-copy-status"), "Copia el enlace seleccionado.");
  }
});

function openCard(task = null, status = "todo") {
  activeCard = task;
  $("#card-dialog-title").textContent = task ? "Editar tarea" : "Nueva tarea";
  $("#card-title").value = task?.title || "";
  $("#card-description").value = task?.description || "";
  $("#card-start").value = task?.start_date || todayISO();
  $("#card-end").value = task?.end_date || todayISO();
  $("#card-status").value = task?.board_status || status;
  $("#delete-card").hidden = !task;
  $("#card-discussion").hidden = !task;
  $("#comment-form").reset();
  $("#comment-list").replaceChildren();
  $("#no-comments").hidden = true;
  cardDialog.querySelectorAll(".dialog-error").forEach((item) => message(item));
  cardDialog.showModal();
  if (task) loadComments(task.id);
}

$("#create-card").addEventListener("click", () => openCard());
handleForm("#card-form", async (details) => {
  if (details.end_date < details.start_date) {
    throw new Error("La fecha de fin no puede ser anterior a la de inicio.");
  }
  details.title = details.title.trim();
  const task = await request(taskPath(activeCard?.id ?? null), json(activeCard ? "PATCH" : "POST", details));
  saveCard(task);
  cardDialog.close();
});

function showComments(comments) {
  const elements = comments.map((comment) => {
    const item = node("li", "comment", "");
    const heading = node("div", "comment-meta", "");
    const time = node("time", "", commentFormat.format(new Date(comment.created_at)));
    time.dateTime = comment.created_at;
    heading.append(node("strong", "", comment.author_email), time);
    item.append(heading, node("p", "comment-body", comment.body));
    return item;
  });
  $("#comment-list").replaceChildren(...elements);
  $("#no-comments").hidden = comments.length !== 0;
}

async function loadComments(id) {
  const version = ++commentsRequest;
  const reload = $("#reload-comments");
  reload.disabled = true;
  try {
    const comments = await request(taskPath(id, true));
    if (version === commentsRequest && cardDialog.open && activeCard?.id === id) showComments(comments);
  } catch (error) {
    if (version === commentsRequest && cardDialog.open && activeCard?.id === id) {
      message($("#comment-form .dialog-error"), error.message);
    }
  } finally {
    if (version === commentsRequest) reload.disabled = false;
  }
}

$("#reload-comments").addEventListener("click", () => {
  message($("#comment-form .dialog-error"));
  if (activeCard) loadComments(activeCard.id);
});

handleForm("#comment-form", async ({ body }, form) => {
  const id = activeCard.id;
  await request(taskPath(id, true), json("POST", { body: body.trim() }));
  form.reset();
  await loadComments(id);
});

$("#delete-card").addEventListener("click", () => {
  if (dialogIsBusy(cardDialog)) return;
  $("#delete-card-name").textContent = activeCard.title;
  message(deleteDialog.querySelector(".dialog-error"));
  deleteDialog.showModal();
});

handleForm("#delete-card-form", async () => {
  const id = activeCard.id;
  await request(taskPath(id), { method: "DELETE" });
  tasks = tasks.filter((task) => task.id !== id);
  boardRevision += 1;
  deleteDialog.close();
  cardDialog.close();
  renderBoard();
  $("#create-card").focus();
});

window.addEventListener("pageshow", (event) => {
  if (event.persisted) window.location.reload();
});

start();
