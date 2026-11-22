const demo = document.querySelector("#ejemplo");
const days = demo.querySelectorAll("[data-demo-day]");
const panels = demo.querySelectorAll("[data-demo-tasks]");

demo.querySelector(".welcome-demo-days").addEventListener("click", (event) => {
  const day = event.target.closest("[data-demo-day]");
  if (!day) return;

  for (const button of days) {
    button.setAttribute("aria-pressed", String(button === day));
  }
  for (const panel of panels) {
    panel.hidden = panel.dataset.demoTasks !== day.dataset.demoDay;
  }
});
