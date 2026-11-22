import { request } from "./http.js";

const MONTHS = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"];
const $ = (selector) => document.querySelector(selector);
const now = new Date();
const state = {
  tasks: [], year: now.getFullYear(), month: now.getMonth(), selectedDay: null,
  filter: "all", search: "", editingId: null, loading: true, loaded: false, saving: false,
};
const taskList = $("#task-list");
const taskDialog = $("#task-dialog");
const taskForm = $("#task-form");
const deleteDialog = $("#delete-dialog");
const confirmDelete = $("#confirm-delete");
const cancelDelete = $("#cancel-delete");
let taskToDelete = null;

function calendarDate(year, month, day = 1) {
  const value = new Date(0);
  value.setHours(12, 0, 0, 0);
  value.setFullYear(year, month, day);
  return value;
}

function dateKey(value) {
  return `${String(value.getFullYear()).padStart(4, "0")}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`;
}

function parseDate(value) {
  const [year, month, day] = value.split("-").map(Number);
  return calendarDate(year, month - 1, day);
}

function formatDate(value, options = { day: "numeric", month: "short", year: "numeric" }) {
  return parseDate(value).toLocaleDateString("es-ES", options);
}

function monthBounds(year = state.year, month = state.month) {
  return [dateKey(calendarDate(year, month)), dateKey(calendarDate(year, month + 1, 0))];
}

function overlaps(task, start, end) {
  return task.start_date <= end && task.end_date >= start;
}

function tasksForMonth(year = state.year, month = state.month) {
  const [start, end] = monthBounds(year, month);
  return state.tasks.filter((task) => overlaps(task, start, end));
}

function taskStatus(task, today = dateKey(new Date())) {
  if (task.completed) return { key: "completed", label: "Completada" };
  if (task.end_date < today) return { key: "overdue", label: "Vencida" };
  if (task.start_date > today) return { key: "upcoming", label: "Por empezar" };
  return { key: "active", label: "En curso" };
}

function matchesFilters(task) {
  if (state.filter === "pending" && task.completed) return false;
  if (state.filter === "completed" && !task.completed) return false;
  return `${task.title} ${task.description}`.toLocaleLowerCase("es").includes(state.search);
}

function sortedTasks(tasks) {
  return [...tasks].sort((a, b) => Number(a.completed) - Number(b.completed)
    || a.end_date.localeCompare(b.end_date) || a.start_date.localeCompare(b.start_date) || a.id - b.id);
}

function clearError(target = $("#notice")) {
  target.hidden = true;
  target.replaceChildren();
}

function showError(error, target = $("#notice"), retry = false) {
  target.textContent = error.message || "Ha ocurrido un error inesperado.";
  if (retry) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "secondary-button";
    button.textContent = "Reintentar";
    button.addEventListener("click", loadTasks);
    target.append(button);
  }
  target.hidden = false;
}

function renderSidebar() {
  const years = new Set([state.year, now.getFullYear()]);
  for (let year = state.year - 5; year <= state.year + 5; year++) {
    if (year >= 1 && year <= 9999) years.add(year);
  }
  state.tasks.forEach((task) => {
    years.add(Number(task.start_date.slice(0, 4)));
    years.add(Number(task.end_date.slice(0, 4)));
  });
  $("#year-select").replaceChildren(...[...years].sort((a, b) => a - b).map((year) => {
    const option = document.createElement("option");
    option.value = year;
    option.textContent = year;
    option.selected = year === state.year;
    return option;
  }));
  $("#previous-year").disabled = state.year === 1;
  $("#next-year").disabled = state.year === 9999;
  $("#month-list").replaceChildren(...MONTHS.map((month, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `month-button${index === state.month ? " active" : ""}`;
    button.dataset.month = index;
    button.setAttribute("aria-pressed", String(index === state.month));
    const count = tasksForMonth(state.year, index).length;
    button.setAttribute("aria-label", `${month} de ${state.year}, ${count} ${count === 1 ? "tarea" : "tareas"}`);
    const label = document.createElement("span");
    label.textContent = month;
    const badge = document.createElement("span");
    badge.className = "month-count";
    badge.textContent = state.loaded ? count : "—";
    badge.setAttribute("aria-hidden", "true");
    button.append(label, badge);
    return button;
  }));
  const yearStart = `${String(state.year).padStart(4, "0")}-01-01`;
  const yearEnd = `${String(state.year).padStart(4, "0")}-12-31`;
  const count = state.tasks.filter((task) => overlaps(task, yearStart, yearEnd)).length;
  $("#year-task-count").textContent = state.loaded ? `${count} ${count === 1 ? "tarea" : "tareas"} en ${state.year}` : "";
}

function renderSummary() {
  const tasks = tasksForMonth();
  const done = tasks.filter((task) => task.completed).length;
  const percent = tasks.length ? Math.round(done / tasks.length * 100) : 0;
  $("#total-count").textContent = state.loaded ? tasks.length : "—";
  $("#pending-count").textContent = state.loaded ? tasks.length - done : "—";
  $("#done-count").textContent = state.loaded ? done : "—";
  $("#progress-label").textContent = `${percent}% completado`;
  $("#progress-fill").style.width = `${percent}%`;
}

function renderCalendar() {
  $("#calendar-title").textContent = `${MONTHS[state.month]} de ${state.year}`;
  $("#previous-month").disabled = state.year === 1 && state.month === 0;
  $("#next-month").disabled = state.year === 9999 && state.month === 11;
  const first = calendarDate(state.year, state.month);
  const offset = (first.getDay() + 6) % 7;
  const daysInMonth = calendarDate(state.year, state.month + 1, 0).getDate();
  const cells = Math.ceil((offset + daysInMonth) / 7) * 7;
  const tasks = sortedTasks(state.tasks.filter(matchesFilters));
  const today = dateKey(new Date());
  const days = [];
  for (let index = 0; index < cells; index++) {
    const date = calendarDate(state.year, state.month, index - offset + 1);
    const key = dateKey(date);
    const dayTasks = tasks.filter((task) => overlaps(task, key, key));
    const button = document.createElement("button");
    button.type = "button";
    button.className = `calendar-day${date.getMonth() !== state.month ? " outside-month" : ""}${key === today ? " today" : ""}${key === state.selectedDay ? " selected" : ""}`;
    button.dataset.date = key;
    button.disabled = date.getFullYear() < 1 || date.getFullYear() > 9999;
    button.setAttribute("aria-pressed", String(key === state.selectedDay));
    if (key === today) button.setAttribute("aria-current", "date");
    const description = dayTasks.map((task) => `${task.title} (${taskStatus(task, today).label})`).join(", ");
    button.setAttribute("aria-label", `${date.toLocaleDateString("es-ES", { weekday: "long", day: "numeric", month: "long", year: "numeric" })}: ${dayTasks.length} ${dayTasks.length === 1 ? "tarea" : "tareas"}${description ? `. ${description}` : ""}`);
    const number = document.createElement("span");
    number.className = "day-number";
    number.textContent = date.getDate();
    button.append(number);
    dayTasks.slice(0, 2).forEach((task) => {
      const event = document.createElement("span");
      event.className = `calendar-event status-${taskStatus(task, today).key}`;
      event.textContent = task.title;
      event.title = `${task.title} · ${taskStatus(task, today).label} · ${formatDate(task.start_date)} – ${formatDate(task.end_date)}`;
      button.append(event);
    });
    if (dayTasks.length > 2) {
      const more = document.createElement("span");
      more.className = "calendar-more";
      more.textContent = `+${dayTasks.length - 2} más`;
      button.append(more);
    }
    days.push(button);
  }
  $("#calendar-grid").replaceChildren(...days);
}

function upsertTask(task) {
  state.tasks = state.tasks.filter((item) => item.id !== task.id);
  state.tasks.push(task);
  render();
}

function createTaskItem(task) {
  const status = taskStatus(task);
  const item = document.createElement("li");
  item.className = `task-item status-${status.key}${task.completed ? " completed" : ""}`;
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.className = "task-checkbox";
  checkbox.checked = task.completed;
  checkbox.setAttribute("aria-label", `Marcar «${task.title}» como ${task.completed ? "pendiente" : "completada"}`);
  checkbox.addEventListener("change", async () => {
    checkbox.disabled = true;
    editButton.disabled = true;
    deleteButton.disabled = true;
    clearError();
    try {
      const updated = await request(`/api/tasks/${task.id}`, { method: "PATCH", body: JSON.stringify({ completed: checkbox.checked }) });
      upsertTask(updated);
    } catch (error) {
      checkbox.checked = task.completed;
      checkbox.disabled = false;
      editButton.disabled = false;
      deleteButton.disabled = false;
      showError(error);
    }
  });
  const content = document.createElement("div");
  content.className = "task-content";
  const title = document.createElement("h3");
  title.className = "task-title";
  title.textContent = task.title;
  content.append(title);
  if (task.description) {
    const description = document.createElement("p");
    description.className = "task-description";
    description.textContent = task.description;
    content.append(description);
  }
  const meta = document.createElement("div");
  meta.className = "task-meta";
  const badge = document.createElement("span");
  badge.className = "status-badge";
  badge.textContent = status.label;
  const dates = document.createElement("span");
  dates.className = "task-dates";
  const start = document.createElement("time");
  start.dateTime = task.start_date;
  start.textContent = formatDate(task.start_date);
  const end = document.createElement("time");
  end.dateTime = task.end_date;
  end.textContent = formatDate(task.end_date);
  dates.append("Inicio: ", start, " · Fin: ", end);
  meta.append(badge, dates);
  content.append(meta);
  const actions = document.createElement("div");
  actions.className = "task-actions";
  const editButton = document.createElement("button");
  editButton.type = "button";
  editButton.className = "text-button";
  editButton.textContent = "Editar";
  editButton.setAttribute("aria-label", `Editar ${task.title}`);
  editButton.addEventListener("click", () => openTaskDialog(task));
  const deleteButton = document.createElement("button");
  deleteButton.type = "button";
  deleteButton.className = "text-button danger";
  deleteButton.textContent = "Eliminar";
  deleteButton.setAttribute("aria-label", `Eliminar ${task.title}`);
  deleteButton.addEventListener("click", () => {
    taskToDelete = task;
    $("#delete-task-name").textContent = `«${task.title}»`;
    clearError($("#delete-notice"));
    deleteDialog.showModal();
    cancelDelete.focus();
  });
  actions.append(editButton, deleteButton);
  item.append(checkbox, content, actions);
  return item;
}

cancelDelete.addEventListener("click", () => deleteDialog.close());
deleteDialog.addEventListener("cancel", (event) => {
  if (confirmDelete.disabled) event.preventDefault();
});
$("#delete-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (confirmDelete.disabled || !taskToDelete) return;
  const { id } = taskToDelete;
  confirmDelete.disabled = true;
  cancelDelete.disabled = true;
  confirmDelete.textContent = "Eliminando…";
  clearError($("#delete-notice"));
  try {
    await request(`/api/tasks/${id}`, { method: "DELETE" });
    state.tasks = state.tasks.filter((task) => task.id !== id);
    deleteDialog.close();
    render();
    ($("#task-list .task-checkbox") || $("#new-task-button")).focus();
  } catch (error) {
    showError(error, $("#delete-notice"));
  } finally {
    confirmDelete.disabled = false;
    cancelDelete.disabled = false;
    confirmDelete.textContent = "Eliminar tarea";
  }
});

function renderTasks() {
  const monthTasks = tasksForMonth();
  const filtered = sortedTasks(monthTasks.filter((task) => matchesFilters(task)
    && (!state.selectedDay || overlaps(task, state.selectedDay, state.selectedDay))));
  taskList.replaceChildren(...filtered.map(createTaskItem));
  taskList.setAttribute("aria-busy", String(state.loading));
  $("#tasks-title").textContent = state.selectedDay
    ? `Tareas del ${formatDate(state.selectedDay, { day: "numeric", month: "long" })}` : "Tareas del mes";
  $("#clear-day").hidden = !state.selectedDay;
  $("#empty-state").hidden = !state.loaded || filtered.length !== 0;
  const hasFilter = state.search || state.filter !== "all";
  $("#empty-title").textContent = hasFilter ? "No hay tareas con estos filtros"
    : state.selectedDay ? "Un día con espacio para tus planes" : "Un nuevo mes para tus planes";
  $("#empty-subtitle").textContent = hasFilter ? "Prueba otra búsqueda o selecciona Todas."
    : "Crea una tarea y dale una fecha para empezar.";
  $("#empty-add").hidden = Boolean(hasFilter);
}

function render() {
  renderSidebar();
  renderSummary();
  renderCalendar();
  renderTasks();
  $("#loading-status").hidden = !state.loading;
  $("#new-task-button").disabled = !state.loaded;
}

function changeMonth(year, month, selectedDay = null) {
  const date = calendarDate(year, month);
  if (date.getFullYear() < 1 || date.getFullYear() > 9999) return;
  state.year = date.getFullYear();
  state.month = date.getMonth();
  state.selectedDay = selectedDay;
  render();
}

async function loadTasks() {
  if (state.loading && state.loaded) return;
  state.loading = true;
  clearError();
  render();
  try {
    state.tasks = await request("/api/tasks");
    state.loaded = true;
  } catch (error) {
    showError(error, $("#notice"), true);
  } finally {
    state.loading = false;
    render();
  }
}

function openTaskDialog(task = null) {
  if (state.saving) return;
  taskForm.reset();
  clearError($("#form-notice"));
  state.editingId = task ? task.id : null;
  $("#dialog-title").textContent = task ? "Editar tarea" : "Nueva tarea";
  $("#save-task").textContent = task ? "Guardar cambios" : "Guardar tarea";
  const today = new Date();
  const defaultDate = state.selectedDay || dateKey(
    today.getFullYear() === state.year && today.getMonth() === state.month
      ? today : calendarDate(state.year, state.month));
  $("#task-title-input").value = task ? task.title : "";
  $("#task-description").value = task ? task.description : "";
  $("#task-start").value = task ? task.start_date : defaultDate;
  $("#task-end").value = task ? task.end_date : defaultDate;
  $("#task-end").min = $("#task-start").value;
  $("#task-end").setCustomValidity("");
  taskDialog.showModal();
  $("#task-title-input").focus();
}

function validateDates() {
  const start = $("#task-start").value;
  const end = $("#task-end").value;
  $("#task-end").min = start || "0001-01-01";
  $("#task-end").setCustomValidity(start && end && end < start
    ? "La fecha de fin no puede ser anterior a la de inicio." : "");
}

taskForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (state.saving) return;
  validateDates();
  if (!taskForm.reportValidity()) return;
  const data = {
    title: $("#task-title-input").value.trim(), description: $("#task-description").value.trim(),
    start_date: $("#task-start").value, end_date: $("#task-end").value,
  };
  if (!data.title) {
    showError(new Error("Escribe un título para tu tarea."), $("#form-notice"));
    $("#task-title-input").focus();
    return;
  }
  const editing = state.editingId !== null;
  state.saving = true;
  ["#save-task", "#close-dialog", "#cancel-task"].forEach((selector) => { $(selector).disabled = true; });
  $("#save-task").textContent = "Guardando…";
  clearError($("#form-notice"));
  clearError();
  try {
    const saved = await request(editing ? `/api/tasks/${state.editingId}` : "/api/tasks", {
      method: editing ? "PATCH" : "POST", body: JSON.stringify(data),
    });
    const [start, end] = monthBounds();
    if (!editing || !overlaps(saved, start, end)) {
      const date = parseDate(saved.start_date);
      state.year = date.getFullYear();
      state.month = date.getMonth();
    }
    state.selectedDay = null;
    state.search = "";
    state.filter = "all";
    $("#search").value = "";
    syncFilters();
    upsertTask(saved);
    taskDialog.close();
  } catch (error) {
    showError(error, $("#form-notice"));
  } finally {
    state.saving = false;
    ["#save-task", "#close-dialog", "#cancel-task"].forEach((selector) => { $(selector).disabled = false; });
    $("#save-task").textContent = editing ? "Guardar cambios" : "Guardar tarea";
  }
});

function syncFilters() {
  document.querySelectorAll(".filter").forEach((button) => {
    const active = button.dataset.filter === state.filter;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
}

$("#task-start").addEventListener("input", validateDates);
$("#task-end").addEventListener("input", validateDates);
$("#close-dialog").addEventListener("click", () => taskDialog.close());
$("#cancel-task").addEventListener("click", () => taskDialog.close());
taskDialog.addEventListener("cancel", (event) => { if (state.saving) event.preventDefault(); });
taskDialog.addEventListener("close", () => { state.editingId = null; });
$("#new-task-button").addEventListener("click", () => openTaskDialog());
$("#empty-add").addEventListener("click", () => openTaskDialog());
$("#previous-month").addEventListener("click", () => changeMonth(state.year, state.month - 1));
$("#next-month").addEventListener("click", () => changeMonth(state.year, state.month + 1));
$("#previous-year").addEventListener("click", () => changeMonth(state.year - 1, state.month));
$("#next-year").addEventListener("click", () => changeMonth(state.year + 1, state.month));
$("#year-select").addEventListener("change", (event) => changeMonth(Number(event.target.value), state.month));
$("#today-button").addEventListener("click", () => {
  const today = new Date();
  changeMonth(today.getFullYear(), today.getMonth());
});
$("#month-list").addEventListener("click", (event) => {
  const button = event.target.closest("[data-month]");
  if (!button) return;
  const month = Number(button.dataset.month);
  changeMonth(state.year, month);
  $(`#month-list [data-month="${month}"]`).focus();
});
$("#calendar-grid").addEventListener("click", (event) => {
  const button = event.target.closest("[data-date]");
  if (!button || button.disabled) return;
  const date = parseDate(button.dataset.date);
  const selected = state.selectedDay === button.dataset.date ? null : button.dataset.date;
  changeMonth(date.getFullYear(), date.getMonth(), selected);
  $(`#calendar-grid [data-date="${button.dataset.date}"]`)?.focus();
});
$("#clear-day").addEventListener("click", () => {
  state.selectedDay = null;
  renderCalendar();
  renderTasks();
});
$("#search").addEventListener("input", (event) => {
  state.search = event.target.value.trim().toLocaleLowerCase("es");
  renderCalendar();
  renderTasks();
});
document.querySelectorAll(".filter").forEach((button) => {
  button.addEventListener("click", () => {
    state.filter = button.dataset.filter;
    syncFilters();
    renderCalendar();
    renderTasks();
  });
});

let displayedToday = dateKey(new Date());
setInterval(() => {
  const today = dateKey(new Date());
  if (today !== displayedToday) {
    displayedToday = today;
    renderCalendar();
    renderTasks();
  }
}, 60000);

window.addEventListener("pageshow", (event) => {
  if (event.persisted) window.location.reload();
});
loadTasks();
