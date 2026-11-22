import { request as apiRequest } from "./http.js";

const $ = (selector) => document.querySelector(selector);
const columns = [...document.querySelectorAll("[data-column]")];
const statusNames = { todo: "Por hacer", doing: "En curso", done: "Hecho" };
const dateFormat = new Intl.DateTimeFormat("es", { dateStyle: "medium", timeZone: "UTC" });
const commentFormat = new Intl.DateTimeFormat("es", { dateStyle: "medium", timeStyle: "short" });
const fileSizeFormat = new Intl.NumberFormat("es", { maximumFractionDigits: 1 });
const workspaceDialog = $("#workspace-dialog");
const inviteDialog = $("#invite-dialog");
const permissionsDialog = $("#permissions-dialog");
const cardDialog = $("#card-dialog");
const deleteDialog = $("#delete-card-dialog");

let workspaceId = Number($("#shared-app").dataset.workspaceId) || null;
let tasks = [];
let workspaceDetails = null;
let activeCard = null;
let draggedId = null;
let movingCard = false;
let boardRevision = 0;
let commentsRequest = 0;
let attachmentsRequest = 0;

function request(path, options) {
  const space = workspaceId ? "&espacio=" + workspaceId : "";
  return apiRequest(path, options, "/iniciar-sesion?volver=espacios" + space);
}

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

function taskPath(id = null, endpoint = "") {
  const task = id === null ? "" : "/" + id + endpoint;
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
    workspaceDetails = workspace;
    document.title = workspace.name + " · Tu block de tareas";
    renderMembers(workspace);
    $("#invite-button").hidden = !workspace.can_invite;
    $("#permissions-button").hidden = !workspace.can_invite;
    $("#board-permissions-hint").hidden = workspace.can_change_status;
    $("#board-actions").hidden = false;
    $("#board").hidden = false;
    if (revision === boardRevision) {
      tasks = cards;
    }
    renderBoard();
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

function createCard(task, index, cards) {
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
  move.disabled = !workspaceDetails.can_change_status;
  move.addEventListener("change", () => moveCard(task.id, move.value, null, "select"));
  const ordering = node("div", "kanban-order", "");
  for (const [step, symbol, direction] of [[-1, "↑", "Subir"], [1, "↓", "Bajar"]]) {
    const button = node("button", "icon-button", symbol);
    button.type = "button";
    button.setAttribute("aria-label", direction + " " + task.title);
    button.disabled = !cards[index + step];
    button.addEventListener("click", () => {
      const before = step < 0 ? cards[index - 1] : cards[index + 2];
      moveCard(task.id, task.board_status, before?.id ?? null, ".kanban-card-title");
    });
    ordering.append(button);
  }
  actions.append(discussion, move, ordering);
  card.append(actions);

  card.addEventListener("dragstart", (event) => {
    if (movingCard) {
      event.preventDefault();
      return;
    }
    draggedId = task.id;
    event.dataTransfer.setData("text/plain", String(task.id));
    event.dataTransfer.effectAllowed = "move";
    card.classList.add("dragging");
  });
  card.addEventListener("dragend", () => {
    draggedId = null;
    card.classList.remove("dragging");
    columns.forEach(clearDrop);
  });
  return card;
}

function renderBoard() {
  for (const column of columns) {
    const cards = tasks.filter((task) => task.board_status === column.dataset.column)
      .sort((first, second) => first.board_position - second.board_position || second.id - first.id);
    column.querySelector(".column-add").hidden = column.dataset.column !== "todo" && !workspaceDetails.can_change_status;
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

function canMove(id, status) {
  const current = tasks.find((task) => task.id === id);
  return current && (current.board_status === status || workspaceDetails.can_change_status);
}

async function moveCard(id, status, beforeId, focus = "") {
  if (!canMove(id, status) || movingCard) return;
  movingCard = true;
  const revision = boardRevision;
  $("#board").inert = true;
  message($("#board-error"));
  try {
    const cards = await request(taskPath(id, "/position"), json("PATCH", {
      board_status: status, before_task_id: beforeId,
    }));
    const changedDuringMove = revision !== boardRevision;
    boardRevision += 1;
    if (changedDuringMove) await refreshBoard();
    else tasks = cards;
  } catch (error) {
    message($("#board-error"), error.message);
  } finally {
    movingCard = false;
    $("#board").inert = false;
    renderBoard();
    if (focus) $(`[data-card-id="${id}"] ${focus}`)?.focus();
  }
}

function clearDrop(column) {
  column.classList.remove("drop-target", "drop-end");
  column.querySelector(".drop-before")?.classList.remove("drop-before");
}

function dropBefore(column, y) {
  return [...column.querySelectorAll(".kanban-card:not(.dragging)")].find((card) => {
    const bounds = card.getBoundingClientRect();
    return y < bounds.top + bounds.height / 2;
  });
}

for (const column of columns) {
  column.querySelector(".column-add").addEventListener("click", () => openCard(null, column.dataset.column));
  column.addEventListener("dragover", (event) => {
    if (!canMove(draggedId, column.dataset.column)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    clearDrop(column);
    const before = dropBefore(column, event.clientY);
    column.classList.add("drop-target");
    column.classList.toggle("drop-end", !before);
    before?.classList.add("drop-before");
  });
  column.addEventListener("dragleave", (event) => {
    if (!column.contains(event.relatedTarget)) clearDrop(column);
  });
  column.addEventListener("drop", (event) => {
    if (!canMove(draggedId, column.dataset.column)) return;
    event.preventDefault();
    const before = dropBefore(column, event.clientY);
    clearDrop(column);
    const id = draggedId;
    draggedId = null;
    document.querySelector(".kanban-card.dragging")?.classList.remove("dragging");
    moveCard(id, column.dataset.column, before ? Number(before.dataset.cardId) : null);
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
  return Boolean(dialog.querySelector('[type="submit"]:disabled, [type="checkbox"]:disabled'));
}

for (const dialog of [workspaceDialog, inviteDialog, permissionsDialog, cardDialog, deleteDialog]) {
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
  message($("#invitation-result"));
  message(inviteDialog.querySelector(".dialog-error"));
  inviteDialog.showModal();
});

handleForm("#invite-form", async ({ email }) => {
  message($("#invitation-result"));
  const invite = await request("/api/workspaces/" + workspaceId + "/invitations", json("POST", { email }));
  const delivery = {
    sent: "El servidor de correo aceptó el aviso.",
    not_configured: "El envío por correo aún no está configurado.",
    failed: "No se pudo enviar el aviso por correo.",
  };
  message($("#invitation-result"), "La invitación está en Mensajes. " + delivery[invite.email_delivery]);
});

function renderPermissions(workspace) {
  const members = workspace.members.filter((member) => member.id !== workspace.owner_id);
  $("#permissions-empty").hidden = members.length > 0;
  $("#member-permissions").replaceChildren(...members.map((member) => {
    const item = node("li", "", "");
    const label = node("label", "member-permission", member.email);
    const check = document.createElement("input");
    check.type = "checkbox";
    check.checked = member.can_change_status;
    check.addEventListener("change", async () => {
      check.disabled = true;
      message($("#permissions-error"));
      message($("#permissions-status"));
      try {
        const result = await request(`/api/workspaces/${workspaceId}/members/${member.id}`, json("PATCH", {
          can_change_status: check.checked,
        }));
        member.can_change_status = result.can_change_status;
        message($("#permissions-status"), "Permiso actualizado para " + member.email + ".");
      } catch (error) {
        check.checked = member.can_change_status;
        message($("#permissions-error"), error.message);
      } finally {
        check.disabled = false;
      }
    });
    label.prepend(check);
    item.append(label);
    return item;
  }));
}

$("#permissions-button").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  $("#member-permissions").replaceChildren();
  $("#permissions-empty").hidden = true;
  message($("#permissions-error"));
  message($("#permissions-status"), "Cargando miembros…");
  permissionsDialog.showModal();
  button.disabled = true;
  try {
    const workspace = await request("/api/workspaces/" + workspaceId);
    message($("#permissions-status"));
    if (permissionsDialog.open) {
      renderMembers(workspace);
      renderPermissions(workspace);
    }
  } catch (error) {
    message($("#permissions-status"));
    message($("#permissions-error"), error.message);
  } finally {
    button.disabled = false;
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
  $("#card-status").disabled = !workspaceDetails.can_change_status;
  $("#delete-card").hidden = !task;
  $("#card-discussion").hidden = !task;
  $("#card-attachments").hidden = !task;
  $("#attachment-form").reset();
  $("#attachment-list").replaceChildren();
  message($("#attachment-status"));
  message($("#attachment-error"));
  $("#comment-form").reset();
  $("#comment-list").replaceChildren();
  $("#no-comments").hidden = true;
  cardDialog.querySelectorAll(".dialog-error").forEach((item) => message(item));
  cardDialog.showModal();
  if (task) {
    loadAttachments(task);
    loadComments(task.id);
  }
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

function formatFileSize(bytes) {
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 ** 2) return fileSizeFormat.format(bytes / 1024) + " KiB";
  return fileSizeFormat.format(bytes / 1024 ** 2) + " MiB";
}

function attachmentRow(task, attachment) {
  const item = node("li", "attachment-row", "");
  const details = node("div", "", "");
  details.append(
    node("strong", "", attachment.file_name),
    node("span", "attachment-detail", formatFileSize(attachment.size_bytes) + " · " + attachment.uploader_email),
  );
  const download = node("button", "text-button", "Descargar");
  download.type = "button";
  download.setAttribute("aria-label", "Descargar " + attachment.file_name);
  download.addEventListener("click", async () => {
    download.disabled = true;
    message($("#attachment-error"));
    try {
      const blob = await request(taskPath(task.id, "/attachments/" + attachment.id), {
        responseType: "blob", timeoutMs: 60000,
      });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = attachment.file_name;
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      if (cardDialog.open && activeCard === task) message($("#attachment-error"), error.message);
    } finally {
      download.disabled = false;
    }
  });
  item.append(details, download);
  return item;
}

async function loadAttachments(task) {
  const version = ++attachmentsRequest;
  const refresh = $("#reload-attachments");
  const isCurrent = () => version === attachmentsRequest && cardDialog.open && activeCard === task;
  refresh.disabled = true;
  message($("#attachment-error"));
  message($("#attachment-status"), "Cargando archivos…");
  try {
    const files = await request(taskPath(task.id, "/attachments"));
    if (isCurrent()) {
      $("#attachment-list").replaceChildren(...files.map((file) => attachmentRow(task, file)));
      message($("#attachment-status"), files.length ? "" : "Aún no hay archivos adjuntos.");
    }
  } catch (error) {
    if (isCurrent()) {
      message($("#attachment-status"));
      message($("#attachment-error"), error.message);
    }
  } finally {
    if (version === attachmentsRequest) refresh.disabled = false;
  }
}

$("#reload-attachments").addEventListener("click", () => {
  if (activeCard) loadAttachments(activeCard);
});

handleForm("#attachment-form", async ({ file }, form) => {
  const task = activeCard;
  if (file.size > Number($("#card-attachments").dataset.limit)) {
    throw new Error($("#attachment-limit").textContent);
  }
  const input = $("#attachment-file");
  input.disabled = true;
  try {
    const path = taskPath(task.id, "/attachments") + "&file_name=" + encodeURIComponent(file.name);
    await request(path, {
      method: "POST", body: file, headers: { "Content-Type": "application/octet-stream" }, timeoutMs: 60000,
    });
    if (cardDialog.open && activeCard === task) {
      form.reset();
      await loadAttachments(task);
    }
  } finally {
    input.disabled = false;
  }
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
    const comments = await request(taskPath(id, "/comments"));
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
  await request(taskPath(id, "/comments"), json("POST", { body: body.trim() }));
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

start();
