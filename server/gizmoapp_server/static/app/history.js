import { requestJson } from "./api.js";

const runtime = window.GizmoAppRuntime;
const escapeHtml = (value) => String(value)
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll("'", "&#39;");
try {
  const config = runtime.readConfig();
  const grid = document.getElementById("history-grid");
  const payload = await requestJson(`${config.apiBase}/room/history`);
  if (!payload.creations.length) {
    grid.innerHTML = '<p class="history-empty">No creations yet. Your next room redesign will appear here.</p>';
  } else {
    grid.innerHTML = payload.creations.map((creation) => {
      const source = `data:${creation.content_type};base64,${creation.image_data}`;
      const date = new Date(`${creation.created_at.replace(" ", "T")}Z`).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
      return `<article class="history-card"><img src="${source}" alt="Saved room redesign option ${creation.option_number}"><div><p class="history-date">${date} / Option ${String(creation.option_number).padStart(2, "0")}</p><p>${escapeHtml(creation.prompt)}</p><a href="${source}" download="roomform-history-${creation.id}.png">Download image</a></div></article>`;
    }).join("");
  }
  runtime.markReady();
} catch (error) {
  window.GizmoAppRuntime?.showFatalError(error);
}
