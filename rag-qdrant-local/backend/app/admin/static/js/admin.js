// Reineke-RAG Admin — small enhancements on top of HTMX.

(() => {
  // Stamp the "last updated" placeholder on the overview page.
  const setNow = () => {
    document.querySelectorAll(".js-now").forEach((el) => {
      const d = new Date();
      el.textContent = d.toLocaleString("de-DE");
    });
  };
  document.addEventListener("htmx:afterSwap", setNow);
  document.addEventListener("DOMContentLoaded", setNow);

  // Live-tail toggle for the Logs page (works for API and App tabs).
  const liveStreams = new Map(); // target selector -> EventSource

  const renderApiLine = (payload) => {
    const data = JSON.parse(payload);
    const cls =
      data.status >= 500 ? "err" :
      data.status >= 400 ? "warn" :
      "ok";
    const span = document.createElement("span");
    span.className = "log-line";
    span.innerHTML =
      `<span class="ts">${data.ts}</span>` +
      `<span class="method">${data.method}</span> ` +
      `<span class="${cls}">${data.status}</span> ` +
      `${data.path} ` +
      `<span class="ts">${data.duration_ms}ms</span>` +
      (data.tenant ? ` <span class="ts">[${data.tenant}/${data.project}]</span>` : "") +
      (data.error ? ` <span class="err">— ${data.error}</span>` : "");
    return span;
  };

  const renderAppLine = (payload) => {
    const data = JSON.parse(payload);
    const span = document.createElement("span");
    span.className = "log-line";
    span.textContent = data.line;
    return span;
  };

  document.addEventListener("click", (ev) => {
    const btn = ev.target.closest(".js-toggle-live");
    if (!btn) return;
    const targetSel = btn.dataset.target;
    const target = document.querySelector(targetSel);
    if (!target) return;
    const body = target.querySelector("[data-stream-body]");
    const status = target.querySelector(".js-live-status");
    const mode = btn.dataset.mode || "api";

    if (liveStreams.has(targetSel)) {
      liveStreams.get(targetSel).close();
      liveStreams.delete(targetSel);
      btn.classList.replace("btn-outline-danger", "btn-outline-success");
      btn.textContent = "Live-Tail starten";
      if (status) status.textContent = "getrennt";
      return;
    }

    target.classList.remove("d-none");
    const es = new EventSource(btn.dataset.stream);
    liveStreams.set(targetSel, es);
    btn.classList.replace("btn-outline-success", "btn-outline-danger");
    btn.textContent = "Live-Tail stoppen";
    if (status) status.textContent = "verbunden";

    const append = (node) => {
      if (!body) return;
      body.appendChild(node);
      if (mode === "app") body.appendChild(document.createTextNode("\n"));
      // Cap to 500 entries to keep memory bounded.
      while (body.childElementCount > 500) body.removeChild(body.firstChild);
      body.scrollTop = body.scrollHeight;
    };

    es.addEventListener("entry", (e) => append(renderApiLine(e.data)));
    es.addEventListener("line",  (e) => append(renderAppLine(e.data)));
    es.addEventListener("ready", (_e) => { if (status) status.textContent = "verbunden"; });
    es.addEventListener("error", (_e) => { if (status) status.textContent = "Verbindungsfehler"; });
  });

  document.addEventListener("click", (ev) => {
    const btn = ev.target.closest(".js-clear");
    if (!btn) return;
    const target = document.querySelector(btn.dataset.target);
    if (!target) return;
    const body = target.querySelector("[data-stream-body]");
    if (body) body.innerHTML = "";
  });
})();
