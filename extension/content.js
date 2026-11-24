/*
 * CardCropper for CardUploader — the in-page panel.
 *
 * Runs inside the user's own logged-in carduploader.com tab, so every call is
 * made with their session, from CardUploader's own origin. UNOFFICIAL: these
 * are the endpoints the web app itself uses (read 21 Nov 2026), and can change
 * without notice. Check CardUploader's terms.
 *
 * What it does:
 *  - Copy the session (cookie) for CardCropper's read-only "CardUploader jobs".
 *  - Save job data (GET /jobs/{id}/data) and batch history (GET /jobs/history)
 *    as JSON files for CardCropper to read.
 *  - Apply a CardCropper change file: read the job fresh, show the diff
 *    (listed cards apart — edits to them push to eBay), and only on a click
 *    POST /inventory/batch a few cards at a time, stopping at the first error;
 *    then wait for eBay sync on listed cards, re-read, and report mismatches.
 */
(function () {
  "use strict";
  // After the extension is reloaded, the panel from the old copy stays on the
  // page, still running the old code. If a panel is here but its extension
  // copy is gone (or it predates this check), remove it and start fresh.
  if (window.__ccPanel) {
    if (window.__ccAlive && window.__ccAlive()) return;
    window.__ccPanel.remove();
  }
  const VERSION = chrome.runtime.getManifest().version;
  const P = self.CCPush;
  const BASE = "https://carduploader.com/backend";
  const PER_REQUEST = 3;          // cards per POST
  const GAP_MS = 1200;            // between POSTs: a few cards a second
  const SYNC_TIMEOUT_MS = 180000; // eBay sync is usually 30-60 s

  // ------------------------------------------------------------ auth + API

  // CardUploader's session cookie is httpOnly, so the page cannot read it;
  // the extension's background worker can (cookies permission). The desktop
  // app needs it: the backend no longer accepts the Firebase token.
  async function sessionCookies() {
    const r = await chrome.runtime.sendMessage({ type: "cc-session-cookies" });
    if (!r || r.error) throw new Error((r && r.error) || "the extension could not read the session — reload it in chrome://extensions");
    if (!r.cookies) throw new Error("not signed in to CardUploader — sign in, then try again");
    return r.cookies;
  }

  async function api(method, path, body) {
    // Since late Nov 2026 the site signs in with its own session cookie and
    // sends X-Cu-Request: 1 on every backend call; a call without that header
    // is refused with 403 BAD_REQUEST_ORIGIN. Make the call the way the page does.
    const res = await fetch(BASE + path, {
      method,
      credentials: "include",
      headers: { "X-Cu-Request": "1", "Content-Type": "application/json", Accept: "application/json" },
      body: body ? JSON.stringify(body) : undefined
    });
    const text = await res.text();
    let data;
    try { data = text ? JSON.parse(text) : {}; } catch { data = { raw: text }; }
    if (!res.ok) throw new Error(`${method} ${path}: HTTP ${res.status} ${text.slice(0, 200)}`);
    if (data && data.success === false) throw new Error(`${method} ${path}: ${JSON.stringify(data).slice(0, 200)}`);
    return data;
  }

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function download(name, obj) {
    const blob = new Blob([JSON.stringify(obj, null, 1)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  }

  function jobIdFromPage() {
    const m = location.href.match(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i);
    return m ? m[0] : "";
  }

  // ------------------------------------------------------------ panel

  const host = document.createElement("div");
  host.style.cssText = "position:fixed;top:12px;right:12px;z-index:2147483647;display:none";
  const shadow = host.attachShadow({ mode: "open" });
  shadow.innerHTML = `
  <style>
    :host { all: initial }
    .p { font: 13px/1.4 system-ui, sans-serif; color: #111; background: #fff; width: 560px;
         max-height: 86vh; overflow: auto; border: 1px solid #999; border-radius: 8px;
         box-shadow: 0 6px 24px rgba(0,0,0,.25); padding: 12px }
    h2 { font-size: 15px; margin: 0 0 8px }
    h3 { font-size: 13px; margin: 12px 0 4px }
    input[type=text] { width: 100%; box-sizing: border-box; padding: 4px }
    button { margin: 4px 4px 0 0; padding: 4px 10px; cursor: pointer }
    button.go { background: #1a7f37; color: #fff; border: 0; border-radius: 4px }
    table { border-collapse: collapse; width: 100%; font-size: 12px }
    td, th { border-bottom: 1px solid #eee; padding: 2px 4px; text-align: left; vertical-align: top }
    .warn { color: #b3261e } .ok { color: #1a7f37 } .muted { color: #666 }
    pre { white-space: pre-wrap; font-size: 12px; background: #f6f6f6; padding: 6px; margin: 6px 0 0 }
    .x { float: right; border: 0; background: none; font-size: 16px }
  </style>
  <div class="p">
    <button class="x" id="close">×</button>
    <h2>CardCropper <span class="muted" style="font-weight:normal;font-size:12px">v${VERSION}</span></h2>
    <label class="muted">Batch (job) id</label>
    <input type="text" id="job" placeholder="job uuid">
    <div>
      <button id="saveJob">Save job data</button>
      <button id="saveHistory">Save batch history</button>
      <button id="copyToken">Copy token for CardCropper</button>
    </div>
    <h3>Apply a change file</h3>
    <input type="file" id="file" accept=".json">
    <div id="diff"></div>
    <pre id="log" class="muted"></pre>
  </div>`;
  document.documentElement.appendChild(host);
  window.__ccPanel = host;
  window.__ccAlive = () => { try { return !!chrome.runtime.id; } catch { return false; } };
  const $ = (id) => shadow.getElementById(id);
  const log = (msg, cls) => {
    const line = document.createElement("div");
    if (cls) line.className = cls;
    line.textContent = msg;
    $("log").appendChild(line);
  };
  $("job").value = jobIdFromPage();
  $("close").onclick = () => { host.style.display = "none"; };

  chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
    if (msg && msg.type === "cc-toggle") {
      host.style.display = host.style.display === "none" ? "block" : "none";
      if (!$("job").value) $("job").value = jobIdFromPage();
      reply({ ok: true });
    }
  });

  $("saveJob").onclick = async () => {
    const id = $("job").value.trim();
    if (!id) return log("enter the batch's job id (open the batch; it is in the address)", "warn");
    try {
      const data = await api("GET", `/jobs/${id}/data`);
      data.jobId = data.jobId || id;
      download(`job-${data.jobName || id}.json`, data);
      log(`saved job data: ${P.cards(data).length} card(s)`, "ok");
    } catch (e) { log(e.message, "warn"); }
  };

  // For CardCropper's "CardUploader jobs" window: it lists and fetches jobs
  // read-only with this tab's session. CardCropper keeps it in memory only;
  // signing out of CardUploader ends it.
  $("copyToken").onclick = async () => {
    try {
      await api("GET", "/jobs/history");            // check the session works first
      await navigator.clipboard.writeText(await sessionCookies());
      log("session token copied — paste it into CardCropper (Tools → CardUploader jobs). " +
          "It works until you sign out of CardUploader.", "ok");
    } catch (e) { log(e.message, "warn"); }
  };

  $("saveHistory").onclick = async () => {
    try {
      const data = await api("GET", "/jobs/history");
      download("carduploader-history.json", data);
      log("saved batch history — use it in CardCropper's Batch & price tab", "ok");
    } catch (e) { log(e.message, "warn"); }
  };

  // ------------------------------------------------------------ apply

  let pending = null;

  $("file").onchange = async (ev) => {
    $("diff").innerHTML = "";
    $("log").textContent = "";
    pending = null;
    const f = ev.target.files[0];
    if (!f) return;
    try {
      const change = JSON.parse(await f.text());
      const id = change.jobId || $("job").value.trim();
      if (!id) throw new Error("the change file has no jobId and none is entered above");
      if ($("job").value.trim() && change.jobId && change.jobId !== $("job").value.trim()) {
        throw new Error(`the file is for job ${change.jobId}, not the one entered above`);
      }
      $("job").value = id;
      log("reading the job fresh…");
      const job = await api("GET", `/jobs/${id}/data`);
      const plan = P.plan(change, job);
      pending = { id, plan, change };
      showDiff(plan, change);
    } catch (e) { log(e.message, "warn"); }
  };

  function table(rows) {
    const t = document.createElement("table");
    t.innerHTML = "<tr><th>SKU</th><th>field</th><th>old</th><th>new</th></tr>";
    for (const r of rows) {
      const tr = document.createElement("tr");
      for (const v of [r.sku, r.field, r.old, r.value]) {
        const td = document.createElement("td");
        td.textContent = v == null ? "" : String(v);
        tr.appendChild(td);
      }
      t.appendChild(tr);
    }
    return t;
  }

  function showDiff(plan, change) {
    const d = $("diff");
    const head = document.createElement("div");
    head.innerHTML = `<h3>${change.jobName || ""} — ${plan.items.length} card(s) to edit</h3>`;
    d.appendChild(head);
    const unlisted = plan.rows.filter((r) => !r.listed);
    const listed = plan.rows.filter((r) => r.listed);
    if (unlisted.length) d.appendChild(table(unlisted));
    if (listed.length) {
      const h = document.createElement("h3");
      h.className = "warn";
      h.textContent = `LISTED cards — these edits push to eBay (variant switches end/create listings)`;
      d.appendChild(h);
      d.appendChild(table(listed));
    }
    if (plan.refused.length) {
      const h = document.createElement("h3");
      h.className = "warn";
      h.textContent = `Refused — not sent (${plan.refused.length})`;
      d.appendChild(h);
      d.appendChild(table(plan.refused.map((r) => ({ sku: r.sku, field: r.field, old: "", value: r.reason }))));
    }
    if (!plan.items.length) return log("nothing to apply", "warn");
    const go = document.createElement("button");
    go.className = "go";
    go.textContent = `Apply ${plan.items.length} card(s)`;
    go.onclick = () => { go.disabled = true; apply(); };
    d.appendChild(go);
  }

  async function apply() {
    const { id, plan } = pending;
    const reqs = P.requests(id, plan.tcg, plan.items, PER_REQUEST);
    let sent = 0;
    for (const body of reqs) {
      try {
        const res = await api("POST", "/inventory/batch", body);
        if (res && Array.isArray(res.failed) && res.failed.length) {
          throw new Error(`CardUploader reported failures: ${JSON.stringify(res.failed).slice(0, 200)}`);
        }
        sent += body.cards.length;
        log(`sent ${sent} / ${plan.items.length}`);
      } catch (e) {
        log(`STOPPED at the first error: ${e.message}`, "warn");
        log(`${sent} card(s) were sent before it; nothing after it was.`, "warn");
        break;
      }
      await sleep(GAP_MS);
    }
    const listedIds = plan.items.slice(0, sent).filter((x) => x.listed).map((x) => x.inventoryId);
    if (listedIds.length) await waitForSync(listedIds);
    log("re-reading the job…");
    try {
      const job = await api("GET", `/jobs/${id}/data`);
      const bad = P.verify(plan.items.slice(0, sent), job);
      if (!bad.length) log(`verified: all ${sent} card(s) read back as sent`, "ok");
      for (const b of bad) log(`MISMATCH ${b.sku} ${b.field}: sent "${b.sent}", now "${b.now}"`, "warn");
      if (sent) offerReload();
    } catch (e) { log(`could not re-read: ${e.message}`, "warn"); }
  }

  // The page loaded the batch before these edits and does not know about
  // them: it still shows the old values until it is reloaded, and editing a
  // card from that stale view is how a correct price gets typed over.
  function offerReload() {
    log("This page still shows the OLD values — reload it before editing anything here.", "warn");
    const b = document.createElement("button");
    b.textContent = "Reload page";
    b.onclick = () => location.reload();
    $("log").appendChild(b);
  }

  async function waitForSync(ids) {
    log(`waiting for eBay sync on ${ids.length} listed card(s)…`);
    const start = Date.now();
    let last = {};
    while (Date.now() - start < SYNC_TIMEOUT_MS) {
      try {
        last = P.syncStatuses(await api("POST", "/automatic-inventory/row-sync-status", { cardIds: ids }));
      } catch (e) { log(`sync status: ${e.message}`, "warn"); return; }
      const states = ids.map((i) => last[i] || "unknown");
      if (states.every((s) => /^synced$/i.test(s))) return log("eBay: all synced", "ok");
      if (states.some((s) => /fail|error/i.test(s))) break;
      await sleep(5000);
    }
    for (const i of ids) {
      if (!/^synced$/i.test(last[i] || "")) log(`eBay sync for ${i}: ${last[i] || "unknown"}`, "warn");
    }
  }
})();
