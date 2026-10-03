(() => {
  "use strict";
  const meta = (name) => (document.querySelector(`meta[name="${name}"]`) || {}).content || "";
  const csrf = meta("csrf-token");

  async function api(path, body) {
    const opts = { headers: { "X-CSRF-Token": csrf } };
    if (body !== undefined) {
      opts.method = "POST";
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(path, opts);
    let data = {};
    try { data = await res.json(); } catch (_) { /* non-JSON error page */ }
    if (!res.ok) throw new Error(data.error || `${res.status} ${res.statusText}`);
    return data;
  }

  function toast(message, kind) {
    const el = document.createElement("div");
    el.className = "toast" + (kind ? " " + kind : "");
    el.textContent = message;
    document.getElementById("toasts").appendChild(el);
    setTimeout(() => el.remove(), kind === "error" ? 7000 : 3500);
  }

  // ---- thumbnail blur (server default, remembered per browser) ---------------------------
  function storedBlur() { try { return localStorage.getItem("ph-blur"); } catch (_) { return null; } }
  function setBlur(on, persist) {
    document.body.classList.toggle("blur", on);
    if (persist) { try { localStorage.setItem("ph-blur", on ? "1" : "0"); } catch (_) { /* private mode */ } }
  }
  const saved = storedBlur();
  setBlur(saved === null ? meta("blur-default") === "1" : saved === "1", false);

  // ---- job banner + polling ----------------------------------------------------------------
  let wasRunning = false;
  let timer = null;

  function renderJobs(snapshot) {
    const banner = document.getElementById("job-banner");
    const running = snapshot.jobs.find((j) => j.state === "running");
    const latest = snapshot.jobs[0];
    if (running) {
      banner.className = "banner";
      banner.textContent = `Running ${running.name} job…`;
      banner.hidden = false;
    } else if (wasRunning && latest) {
      banner.className = "banner " + (latest.state === "done" ? "done" : "failed");
      banner.textContent = `${latest.name} job ${latest.state}${latest.message ? ": " + latest.message : ""}`;
      banner.hidden = false;
      if (/^\/(queue|status)?$/.test(location.pathname)) setTimeout(() => location.reload(), 1500);
    }
    wasRunning = Boolean(running);

    const table = document.getElementById("jobs-table");
    if (table) {
      table.replaceChildren();
      if (!snapshot.jobs.length) {
        const tr = table.insertRow(); const td = tr.insertCell(); td.className = "muted"; td.textContent = "No jobs yet.";
      }
      for (const j of snapshot.jobs) {
        const tr = table.insertRow();
        [`#${j.id}`, j.name, j.state, j.started, j.message].forEach((text) => { tr.insertCell().textContent = text || ""; });
      }
    }
    return Boolean(running);
  }

  async function poll() {
    clearTimeout(timer);
    try {
      const running = renderJobs(await api("/api/jobs"));
      if (running) timer = setTimeout(poll, 2000);
    } catch (_) { /* transient; the next user action re-polls */ }
  }

  // ---- actions -------------------------------------------------------------------------------
  function markCard(id, status) {
    const chip = document.querySelector(`.card[data-id="${id}"] [data-status]`);
    if (chip) { chip.textContent = status; chip.className = "chip chip-" + status; }
  }

  const handlers = {
    async queue(btn) {
      const id = Number(btn.dataset.id);
      const res = await api("/api/queue", { ids: [id] });
      if (res.queued) { markCard(id, "queued"); toast("Queued"); } else { toast("Already queued or downloaded"); }
    },
    async download(btn) {
      const id = Number(btn.dataset.id);
      await api("/api/download", { ids: [id] });
      markCard(id, "queued");
      toast("Download started");
      poll();
    },
    async remove(btn) {
      await api("/api/queue/remove", { ids: [Number(btn.dataset.id)] });
      location.reload();
    },
    async retry(btn) {
      await api("/api/queue", { ids: [Number(btn.dataset.id)] });
      location.reload();
    },
    async "job-download"() { await api("/api/jobs/download", {}); toast("Download job started"); poll(); },
    async "clear-queue"() {
      if (!confirm("Remove everything from the queue?")) return;
      await api("/api/queue/remove", { all: true });
      location.reload();
    },
    async "job-index"() {
      const form = document.getElementById("index-form");
      const queries = form.elements.queries.value.split("\n").map((s) => s.trim()).filter(Boolean);
      const sites = [...form.querySelectorAll('input[name="site"]:checked')].map((el) => el.value);
      await api("/api/jobs/index", { queries, sites, pages: Number(form.elements.pages.value) || 1 });
      toast("Index job started");
      poll();
    },
    "blur-toggle"() { setBlur(!document.body.classList.contains("blur"), true); },
  };

  document.addEventListener("click", async (event) => {
    const btn = event.target.closest("[data-action]");
    if (!btn || !handlers[btn.dataset.action]) return;
    if (btn.disabled) return;
    btn.disabled = true;
    try { await handlers[btn.dataset.action](btn); }
    catch (err) { toast(err.message, "error"); }
    finally { btn.disabled = false; }
  });

  poll();
})();
