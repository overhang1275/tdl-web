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
