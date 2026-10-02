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
  button.textContent = form.dataset.submittingLabel || "Creando...";
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
  function busyControl(source) {
    if (!(source instanceof Element)) {
      return null;
    }
    if (source.matches("button, a")) {
      return source;
    }
    return source.querySelector("button[type='submit'], button, a.button");
  }

  function setBusy(control) {
    if (!(control instanceof HTMLElement) || control.dataset.busy === "true") {
      return;
    }
    control.dataset.busy = "true";
    control.classList.add("is-loading");
    control.setAttribute("aria-busy", "true");
    if (control instanceof HTMLButtonElement) {
      control.disabled = true;
    }
  }

  function clearBusy(control) {
    if (!(control instanceof HTMLElement)) {
      return;
    }
    control.dataset.busy = "false";
    control.classList.remove("is-loading");
    control.removeAttribute("aria-busy");
    if (control instanceof HTMLButtonElement) {
      control.disabled = false;
    }
  }

  function stopJobPolling() {
    document.querySelectorAll("[data-job-live]").forEach((panel) => {
      panel.dataset.pollStopped = "true";
      panel.removeAttribute("hx-get");
      panel.removeAttribute("hx-trigger");
    });
  }

  document.body.addEventListener("htmx:beforeRequest", (event) => {
    const source = event.detail.elt;
    if (source instanceof HTMLElement && source.dataset.pollStopped === "true") {
      event.preventDefault();
      return;
    }
    if (source instanceof HTMLElement && source.hasAttribute("data-job-live")) {
      return;
    }
    const control = busyControl(source);
    setBusy(control);
    if (source instanceof HTMLElement && control instanceof HTMLElement) {
      source.dataset.busyControl = "true";
    }
  });

  document.body.addEventListener("htmx:afterRequest", (event) => {
    const source = event.detail.elt;
    if (!(source instanceof HTMLElement) || source.dataset.busyControl !== "true") {
      return;
    }
    clearBusy(busyControl(source));
    delete source.dataset.busyControl;
  });

  document.body.addEventListener("htmx:afterSwap", (event) => {
    const target = event.detail.target;
    if (target instanceof Element && target.querySelector("[data-job-terminal='true']")) {
      stopJobPolling();
    }
  });

  window.addEventListener("click", (event) => {
    if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
      return;
    }
    const link = event.target instanceof Element ? event.target.closest("a") : null;
    if (!(link instanceof HTMLAnchorElement) || link.target || link.hasAttribute("download")) {
      return;
    }
    const url = new URL(link.href, window.location.href);
    if (url.origin !== window.location.origin || (url.pathname === window.location.pathname && url.hash)) {
      return;
    }
    if (link.matches(".button, .nav-list a, .bottom-nav a, .mobile-fab, .brand")) {
      setBusy(link);
    }
  });
})();

(() => {
  const page = document.querySelector("[data-notifications-page]");
  if (!page) {
    return;
  }
  const wanted = window.matchMedia("(max-width: 860px)").matches ? "5" : "15";
  const params = new URLSearchParams(window.location.search);
  if ((params.get("per_page") || page.dataset.perPage) === wanted) {
    return;
  }
  params.set("per_page", wanted);
  params.set("page", "1");
  window.location.replace(`/notifications?${params.toString()}`);
})();

(() => {
  const panelSelector = "#chats-panel";
  let searchTimer = 0;
  let chatsRequestId = 0;
  let chatsRefreshRunning = false;

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

  function isRefreshUrl(url) {
    try {
      return new URL(url, window.location.origin).searchParams.get("refresh") === "true";
    } catch {
      return String(url).includes("refresh=true");
    }
  }

  function setRefreshButtons(disabled) {
    document.querySelectorAll("[data-chats-url*='refresh=true']").forEach((button) => {
      if (!(button instanceof HTMLButtonElement)) {
        return;
      }
      if (disabled) {
        button.dataset.originalText = button.textContent || "";
        button.textContent = "Actualizando...";
        button.disabled = true;
      } else {
        button.disabled = false;
        if (button.dataset.originalText) {
          button.textContent = button.dataset.originalText;
        }
      }
    });
  }

  function showChatsLoading(panel, refresh) {
    if (!refresh) {
      return;
    }
    panel.setAttribute("aria-busy", "true");
    panel.innerHTML = `
      <div class="loading-state">
        <div class="spinner" aria-hidden="true"></div>
        <strong>Actualizando cache de chats...</strong>
        <p>Espera un momento. Estoy consultando tdl y guardando el resultado en JSON.</p>
      </div>
    `;
  }

  async function loadChats(url) {
    const panel = chatsPanel();
    if (!panel) {
      return;
    }
    const refresh = isRefreshUrl(url);
    if (refresh && chatsRefreshRunning) {
      return;
    }
    if (refresh) {
      chatsRefreshRunning = true;
      setRefreshButtons(true);
      showChatsLoading(panel, true);
    }
    const requestId = ++chatsRequestId;
    try {
      const response = await fetch(url, { headers: { "X-Requested-With": "fetch" } });
      const html = await response.text();
      if (requestId === chatsRequestId) {
        panel.innerHTML = html;
      }
    } finally {
      if (refresh) {
        chatsRefreshRunning = false;
        setRefreshButtons(false);
        panel.removeAttribute("aria-busy");
      }
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
    if (!(select instanceof HTMLSelectElement) || !["per_page", "chat_type", "sort"].includes(select.name) || !select.closest(panelSelector)) {
      return;
    }
    const form = select.closest("form");
    if (form) {
      form.querySelector("input[name='page']").value = "1";
      loadChats(formUrl(form, { page: "1" }));
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
    const chatType = form.querySelector("select[name='chat_type']");
    const sort = form.querySelector("select[name='sort']");
    loadChats(formUrl(form, {
      q: query ? query.value : "",
      chat_type: chatType ? chatType.value : "",
      sort: sort ? sort.value : "json",
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
