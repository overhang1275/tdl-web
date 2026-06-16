window.addEventListener("submit", (event) => {
  const form = event.target;
  if (!(form instanceof HTMLFormElement) || !form.matches("[data-disable-on-submit]")) {
    return;
  }
  const button = form.querySelector("button[type='submit']");
  if (!button) {
    return;
  }
  if (form.dataset.submitting === "true") {
    event.preventDefault();
    return;
  }
  form.dataset.submitting = "true";
  button.dataset.originalText = button.textContent || "";
  button.textContent = "Creando...";
  button.disabled = true;
});

window.addEventListener("pageshow", () => {
  document.querySelectorAll("form[data-disable-on-submit]").forEach((form) => {
    form.dataset.submitting = "false";
    const button = form.querySelector("button[type='submit']");
    if (button) {
      button.disabled = false;
      if (button.dataset.originalText) {
        button.textContent = button.dataset.originalText;
      }
    }
  });
});

(() => {
  const panelSelector = "#chats-panel";
  let searchTimer = 0;
  let chatsRequestId = 0;

  function chatsPanel() {
    return document.querySelector(panelSelector);
  }

  function formUrl(form, overrides = {}) {
    const params = new URLSearchParams(new FormData(form));
    Object.entries(overrides).forEach(([key, value]) => {
      params.set(key, value);
    });
    return `/chats/list?${params.toString()}`;
  }

  async function loadChats(url) {
    const panel = chatsPanel();
    if (!panel) {
      return;
    }
    const requestId = ++chatsRequestId;
    const response = await fetch(url, { headers: { "X-Requested-With": "fetch" } });
    const html = await response.text();
    if (requestId === chatsRequestId) {
      panel.innerHTML = html;
    }
  }

  function initChatsPanel() {
    if (chatsPanel()) {
      loadChats("/chats/list");
    }
  }

  if (document.readyState === "loading") {
    window.addEventListener("DOMContentLoaded", initChatsPanel);
  } else {
    initChatsPanel();
  }

  window.addEventListener("input", (event) => {
    const input = event.target;
    if (!(input instanceof HTMLInputElement) || input.name !== "q" || !input.closest(panelSelector)) {
      return;
    }
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => {
      const form = input.closest("form");
      if (form) {
        form.querySelector("input[name='page']").value = "1";
        loadChats(formUrl(form, { q: input.value, page: "1" }));
      }
    }, 350);
  });

  window.addEventListener("change", (event) => {
    const select = event.target;
    if (!(select instanceof HTMLSelectElement) || select.name !== "per_page" || !select.closest(panelSelector)) {
      return;
    }
    const form = select.closest("form");
    if (form) {
      form.querySelector("input[name='page']").value = "1";
      const query = form.querySelector("input[name='q']");
      loadChats(formUrl(form, { q: query ? query.value : "", per_page: select.value, page: "1" }));
    }
  });

  window.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.closest(panelSelector)) {
      return;
    }
    event.preventDefault();
    const query = form.querySelector("input[name='q']");
    const perPage = form.querySelector("select[name='per_page']");
    loadChats(formUrl(form, {
      q: query ? query.value : "",
      per_page: perPage ? perPage.value : "25",
      page: "1",
    }));
  });

  window.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) {
      return;
    }
    const trigger = target.closest("[data-chats-url]");
    if (!trigger || trigger.matches("[disabled]")) {
      return;
    }
    event.preventDefault();
    loadChats(trigger.getAttribute("data-chats-url"));
  });
})();

window.addEventListener("click", async (event) => {
  const target = event.target;
  if (!(target instanceof Element)) {
    return;
  }
  const button = target.closest("[data-copy-text]");
  if (!(button instanceof HTMLElement)) {
    return;
  }
  const value = button.dataset.copyText || "";
  if (!value) {
    return;
  }
  try {
    await navigator.clipboard.writeText(value);
    const previous = button.textContent;
    button.textContent = "Ruta copiada";
    window.setTimeout(() => {
      button.textContent = previous;
    }, 1800);
  } catch {
    window.prompt("Copia la ruta:", value);
  }
});

(() => {
  const storageKey = "tdl-web-job-statuses";
  const terminalStatuses = new Set(["completed", "failed", "cancelled"]);

  function readKnownStatuses() {
    try {
      return JSON.parse(window.localStorage.getItem(storageKey) || "{}");
    } catch {
      return {};
    }
  }

  function writeKnownStatuses(statuses) {
    window.localStorage.setItem(storageKey, JSON.stringify(statuses));
  }

  function showToast(job) {
    const root = document.querySelector("#toast-root");
    if (!root) {
      return;
    }
    const toast = document.createElement("a");
    toast.className = `toast ${job.status === "failed" ? "error" : "ok"}`;
    toast.href = `/jobs/${job.id}`;
    const title = job.chat_title || job.chat_id || `Job #${job.id}`;
    const label = job.status === "failed" ? "falló" : job.status === "cancelled" ? "se canceló" : "terminó";
    toast.innerHTML = `<strong>Job #${job.id} ${label}</strong><span>${title}</span>`;
    root.appendChild(toast);
    window.setTimeout(() => toast.remove(), 8000);
  }

  async function pollJobNotifications() {
    try {
      const response = await fetch("/api/jobs/notifications", { headers: { "Accept": "application/json" } });
      if (!response.ok) {
        return;
      }
      const payload = await response.json();
      const known = readKnownStatuses();
      const next = { ...known };
      for (const job of payload.jobs || []) {
        const key = String(job.id);
        const previous = known[key];
        if (previous && previous !== job.status && terminalStatuses.has(job.status)) {
          showToast(job);
        }
        next[key] = job.status;
      }
      writeKnownStatuses(next);
    } catch {
      return;
    }
  }

  window.setTimeout(pollJobNotifications, 1500);
  window.setInterval(pollJobNotifications, 6000);
})();
