/* Neat Studio Launcher: vanilla JS, no build step. */
(function () {
  "use strict";

  const S = window.LAUNCHER.static;
  const $ = (sel, el) => (el || document).querySelector(sel);
  const $$ = (sel, el) => Array.from((el || document).querySelectorAll(sel));
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const icon = (name, cls) => `<svg${cls ? ` class="${cls}"` : ""}><use href="#i-${name}"/></svg>`;

  const state = {
    studios: {},       // id -> public fields + live snapshot
    order: [],
    system: null,
    updates: {},       // id|launcher -> check result
    route: { page: "home" },
    expanded: new Set(),
  };


  // ------------------------------------------------------------------ launch animation
  // A Modalix chip warps in through a starfield (canvas, slowing as the chip
  // lands) and bursts into the Neat palette (CSS). The markup is kept so the
  // animation can be replayed from the header button or with ?intro.
  const INTRO_EL = document.getElementById("intro");
  const INTRO_HTML = INTRO_EL ? INTRO_EL.outerHTML : "";
  let introPlaying = false;

  function firstVisit() {
    try { return localStorage.getItem("neatLauncherWelcomed") !== "1"; } catch (e) { return false; }
  }

  // stay: first visit, so the intro waits on its "Explore the Studios" button.
  function playIntro(el, stay) {
    return new Promise((resolve) => {
      if (stay) el.classList.add("stay");
      introPlaying = true;
      try { sessionStorage.setItem("neatIntroPlayed", "1"); } catch (e) { /* ignore */ }
      const cv = el.querySelector(".intro-stars");
      const ctx = cv.getContext("2d");
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      let W = 0, H = 0;
      const size = () => { W = cv.width = innerWidth * dpr; H = cv.height = innerHeight * dpr; };
      size(); addEventListener("resize", size);
      const COLORS = ["#ffffff", "#ffffff", "#ffffff", "#16c8a6", "#3a86ec", "#a9c81c", "#f2801d"];
      const stars = Array.from({ length: 520 }, () => ({
        x: (Math.random() - .5) * 2, y: (Math.random() - .5) * 2, z: Math.random(), pz: 0,
        c: COLORS[(Math.random() * COLORS.length) | 0],
      }));
      const t0 = performance.now();
      let raf = 0, finished = false;
      const frame = (now) => {
        const t = (now - t0) / 1000;
        // fast warp, easing to a drift by the time the chip lands (~2.2 s)
        const speed = t < 2.2 ? 0.045 * Math.pow(1 - t / 2.2, 1.6) + 0.0025 : 0.0025;
        ctx.fillStyle = "rgba(2,4,9,0.38)";
        ctx.fillRect(0, 0, W, H);
        const cx = W / 2, cy = H / 2, k = Math.max(W, H) * 0.5;
        for (const st of stars) {
          st.pz = st.z;
          st.z -= speed;
          if (st.z <= 0.02) { st.x = (Math.random() - .5) * 2; st.y = (Math.random() - .5) * 2; st.z = 1; st.pz = 1; continue; }
          const sx = cx + (st.x / st.z) * k, sy = cy + (st.y / st.z) * k;
          const px = cx + (st.x / st.pz) * k, py = cy + (st.y / st.pz) * k;
          ctx.strokeStyle = st.c; ctx.globalAlpha = Math.min(1, (1 - st.z) * 1.4);
          ctx.lineWidth = Math.max(0.6, (1 - st.z) * 2.6) * dpr;
          ctx.beginPath(); ctx.moveTo(px, py); ctx.lineTo(sx, sy); ctx.stroke();
        }
        ctx.globalAlpha = 1;
        if (t < 3.2 && !finished) raf = requestAnimationFrame(frame);
      };
      raf = requestAnimationFrame(frame);
      const onKey = (e) => {
        if (stay) { if (e.key === "Escape") el.querySelector(".intro-explore").click(); return; }
        if (e.key === "Escape" || e.key === "Enter" || e.key === " ") { e.preventDefault(); skip(); }
      };
      const finish = () => {
        if (finished) return;
        finished = true; introPlaying = false;
        cancelAnimationFrame(raf);
        removeEventListener("resize", size);
        removeEventListener("keydown", onKey);
        el.remove(); resolve();
      };
      const skip = () => { el.classList.add("skip"); setTimeout(finish, 260); };
      el.querySelector(".intro-skip").addEventListener("click", skip);
      el.querySelector(".intro-explore").addEventListener("click", () => {
        try { localStorage.setItem("neatLauncherWelcomed", "1"); } catch (e) { /* ignore */ }
        skip();
        setTimeout(() => { location.hash = "#studios"; }, 300);
      });
      addEventListener("keydown", onKey);
      if (stay) setTimeout(() => { const b = el.querySelector(".intro-explore"); if (b && b.isConnected) b.focus({ preventScroll: true }); }, 4500);
      if (!stay) {
        el.addEventListener("dblclick", skip);
        setTimeout(finish, 6850);
      }
    });
  }

  function replayIntro() {
    if (introPlaying || !INTRO_HTML) return Promise.resolve();
    const wrap = document.createElement("div");
    wrap.innerHTML = INTRO_HTML;
    const el = wrap.firstElementChild;
    el.removeAttribute("id");
    el.classList.add("replay");                // shows even under :root.no-intro
    document.body.prepend(el);
    return playIntro(el);
  }

  const introDone = (() => {
    if (!INTRO_EL) return Promise.resolve();
    const forced = /[?&]intro\b/.test(location.search);
    if (document.documentElement.classList.contains("no-intro") && !forced) { INTRO_EL.remove(); return Promise.resolve(); }
    if (forced) { INTRO_EL.classList.add("replay"); }
    return playIntro(INTRO_EL, firstVisit());
  })();

  // ------------------------------------------------------------------ utils
  function fmtBytes(n) {
    if (n == null) return "–";
    const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return `${n.toFixed(n >= 100 || i === 0 ? 0 : 1)} ${u[i]}`;
  }
  function fmtDur(s) {
    if (!s) return "–";
    s = Math.floor(s);
    const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
    if (d) return `${d}d ${h}h`;
    if (h) return `${h}h ${m}m`;
    if (m) return `${m}m ${s % 60}s`;
    return `${s}s`;
  }
  function ago(iso) {
    const t = typeof iso === "number" ? iso * 1000 : Date.parse(iso);
    const s = (Date.now() - t) / 1000;
    if (s < 3600) return `${Math.max(1, Math.round(s / 60))} min ago`;
    if (s < 86400) return `${Math.round(s / 3600)} h ago`;
    return `${Math.round(s / 86400)} days ago`;
  }
  const STATE_LABEL = {
    not_installed: "Not Installed", installing: "Installing", updating: "Updating", uninstalling: "Removing",
    installed: "Ready", starting: "Starting", running: "Running", stopping: "Stopping", error: "Needs Attention",
  };
  const BUSY = new Set(["installing", "updating", "uninstalling", "starting", "stopping"]);
  function studioUrl(st) { return `${st.scheme}://${location.hostname}:${st.port}/`; }
  function running() { return state.order.filter((id) => ["running", "starting"].includes(state.studios[id].state)); }

  async function api(path, opts) {
    const o = Object.assign({ method: "GET", headers: {} }, opts || {});
    if (o.body && typeof o.body !== "string") { o.body = JSON.stringify(o.body); o.headers["Content-Type"] = "application/json"; }
    const r = await fetch(path, o);
    let data = null;
    try { data = await r.json(); } catch (e) { /* not json */ }
    if (!r.ok || (data && data.ok === false)) throw new Error((data && data.error) || `HTTP ${r.status}`);
    return data;
  }

  function toast(msg, kind) {
    const el = document.createElement("div");
    el.className = `toast ${kind || "info"}`;
    el.innerHTML = `${icon(kind === "err" ? "alert" : kind === "ok" ? "check" : "sparkle")}<span>${esc(msg)}</span>`;
    el.querySelector("svg").setAttribute("width", "16"); el.querySelector("svg").setAttribute("height", "16");
    $("#toasts").appendChild(el);
    setTimeout(() => { el.style.opacity = "0"; el.style.transition = "opacity .3s"; }, 4200);
    setTimeout(() => el.remove(), 4600);
  }

  function modal({ title, body, actions, input, onOpen }) {
    return new Promise((resolve) => {
      const back = document.createElement("div");
      back.className = "modal-back";
      back.innerHTML = `<div class="modal" role="dialog" aria-modal="true"><h3>${esc(title)}</h3>${body || ""}
        ${input ? `<input id="modalInput" placeholder="${esc(input.placeholder || "")}" autocomplete="off">` : ""}
        <div class="btn-row"></div></div>`;
      const row = back.querySelector(".btn-row");
      const close = (v) => { back.remove(); document.removeEventListener("keydown", onKey); resolve(v); };
      (actions || [{ label: "OK", value: true, cls: "primary" }]).forEach((a) => {
        const b = document.createElement("button");
        b.className = `btn ${a.cls || ""}`; b.textContent = a.label;
        b.onclick = () => close(input && a.value === true ? back.querySelector("#modalInput").value : a.value);
        row.appendChild(b);
      });
      const onKey = (e) => {
        if (e.key === "Escape") close(null);
        else if (e.key === "Enter" && input && e.target === back.querySelector("#modalInput")) { e.preventDefault(); close(e.target.value); }
      };
      document.addEventListener("keydown", onKey);
      back.addEventListener("click", (e) => { if (e.target === back) close(null); });
      document.body.appendChild(back);
      const inp = back.querySelector("#modalInput"); if (inp) inp.focus();
      if (typeof onOpen === "function") onOpen(back);
    });
  }

  // ------------------------------------------------------------------ theme
  function applyThemeIcon() {
    const dark = document.documentElement.getAttribute("data-theme") === "dark";
    $("#themeToggle use").setAttribute("href", dark ? "#i-sun" : "#i-moon");
  }
  $("#themeToggle").addEventListener("click", () => {
    const root = document.documentElement;
    const next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
    root.classList.add("theme-anim");
    root.setAttribute("data-theme", next);
    try { localStorage.setItem("theme", next); } catch (e) { /* private mode */ }
    applyThemeIcon();
    setTimeout(() => root.classList.remove("theme-anim"), 380);
  });
  applyThemeIcon();

  // Full screen: the whole launcher (a studio open inside it fills the screen too).
  (() => {
    const btn = $("#fullscreenBtn");
    const root = document.documentElement;
    const can = root.requestFullscreen || root.webkitRequestFullscreen;
    if (!can || document.fullscreenEnabled === false) { btn.classList.add("hidden"); return; }
    const isFs = () => !!(document.fullscreenElement || document.webkitFullscreenElement);
    const sync = () => {
      const on = isFs();
      btn.querySelector("use").setAttribute("href", on ? "#i-fs-exit" : "#i-fs-enter");
      btn.title = on ? "Exit Full Screen" : "Full Screen";
      btn.setAttribute("aria-label", btn.title);
      btn.setAttribute("aria-pressed", String(on));
    };
    btn.addEventListener("click", async () => {
      try {
        if (isFs()) await (document.exitFullscreen || document.webkitExitFullscreen).call(document);
        else await (root.requestFullscreen || root.webkitRequestFullscreen).call(root);
      } catch (e) { toast("Full Screen Isn't Available Here", "err"); }
    });
    document.addEventListener("fullscreenchange", sync);
    document.addEventListener("webkitfullscreenchange", sync);
    sync();
  })();

  // ------------------------------------------------------------------ terminals
  const XTERM_THEME = {
    background: "#0d1117", foreground: "#e9edf2", cursor: "#2fd4c0", cursorAccent: "#0d1117",
    selectionBackground: "rgba(47,212,192,.3)",
    black: "#151a21", red: "#ef5b62", green: "#35c489", yellow: "#e0ad4a", blue: "#3a86ec",
    magenta: "#c678dd", cyan: "#2fd4c0", white: "#e9edf2",
    brightBlack: "#727d8b", brightRed: "#ff7b82", brightGreen: "#4fe0a3", brightYellow: "#f2c66d",
    brightBlue: "#69a7ff", brightMagenta: "#d99cf0", brightCyan: "#5ff0dd", brightWhite: "#ffffff",
  };
  function makeTerm(host, opts) {
    const term = new window.Terminal(Object.assign({
      fontFamily: "'JetBrains Mono', ui-monospace, monospace", fontSize: 12.5, lineHeight: 1.2,
      theme: XTERM_THEME, scrollback: 8000, convertEol: false, cursorBlink: false, allowProposedApi: true,
    }, opts || {}));
    const fit = new window.FitAddon.FitAddon();
    term.loadAddon(fit);
    term.open(host);
    term._fit = () => { try { if (host.offsetParent !== null) fit.fit(); } catch (e) { /* hidden */ } };
    const ro = new ResizeObserver(() => term._fit());
    ro.observe(host);
    term._ro = ro;
    setTimeout(term._fit, 30);
    return term;
  }

  // Log viewers: several xterms can follow one studio's log.
  const logViewers = [];
  const logSubs = {};
  function attachLog(id, host) {
    const term = makeTerm(host, { disableStdin: true });
    const v = { id, term, ready: false };
    logViewers.push(v);
    logSubs[id] = (logSubs[id] || 0) + 1;
    socket.emit("subscribe_log", { id });
    return {
      term,
      dispose() {
        const i = logViewers.indexOf(v); if (i >= 0) logViewers.splice(i, 1);
        logSubs[id]--; if (!logSubs[id]) socket.emit("unsubscribe_log", { id });
        term._ro.disconnect(); term.dispose();
      },
    };
  }

  // ------------------------------------------------------------------ socket
  const socket = io({ transports: ["websocket", "polling"] });
  // After an update the server's script stamp changes: reload so this page
  // never keeps running old code (one reload per stamp, so it can't loop).
  const MY_STAMP = ((document.querySelector('script[src*="launcher.js"]') || {}).src || "").split("v=")[1] || "";
  socket.on("hello", ({ asset }) => {
    if (!asset || !MY_STAMP || asset === MY_STAMP) return;
    let tried = "";
    try { tried = sessionStorage.getItem("neatReloadedFor") || ""; } catch (e) { /* ignore */ }
    if (tried === asset) return;
    try { sessionStorage.setItem("neatReloadedFor", asset); sessionStorage.setItem("neatIntroPlayed", "1"); } catch (e) { /* ignore */ }
    toast("Launcher Updated: Reloading…", "info");
    setTimeout(() => location.reload(), 900);
  });
  let connectedOnce = false;
  socket.on("connect", () => {
    if (connectedOnce) checkUpdates(false);      // the launcher restarted: results may have changed
    connectedOnce = true;
    Object.keys(logSubs).forEach((id) => { if (logSubs[id]) { logViewers.filter((v) => v.id === id).forEach((v) => { v.ready = false; v.term.reset(); }); socket.emit("subscribe_log", { id }); } });
  });
  socket.on("disconnect", () => { $("#statusText").textContent = "Reconnecting…"; $("#statusItems").dataset.k = ""; });
  socket.on("studios", (snaps) => {
    snaps.forEach((s) => { if (state.studios[s.id]) Object.assign(state.studios[s.id], s); });
    renderAll();
  });
  socket.on("system", (sys) => { state.system = sys; renderSystem(); });
  socket.on("log_init", ({ id, data }) => {
    logViewers.filter((v) => v.id === id && !v.ready).forEach((v) => { v.term.reset(); v.term.write(data); v.ready = true; });
  });
  socket.on("log_reset", ({ id }) => {
    logViewers.filter((v) => v.id === id).forEach((v) => v.term.reset());
  });
  socket.on("log", ({ id, data }) => {
    logViewers.filter((v) => v.id === id && v.ready).forEach((v) => v.term.write(data));
  });
  socket.on("launcher_updated", ({ code }) => {
    if (code === 0) toast("Launcher updated. Restart it (neat-studio stop, then neat-studio) to apply.", "ok");
    else toast("Launcher update failed: see the terminal output of run.sh update.", "err");
    checkUpdates(true);
  });

  // ------------------------------------------------------------------ actions
  async function act(id, action, body) {
    try {
      await api(`/api/studios/${id}/${action}`, { method: "POST", body: body || {} });
      return true;
    } catch (e) {
      toast(e.message, "err");
      return false;
    }
  }

  async function install(id) {
    const st = state.studios[id];
    const ok = await act(id, "install");
    if (ok) toast(`Installing ${st.name}. This can take a while: models are downloaded.`, "info");
  }

  async function launch(id, opts) {
    const st = state.studios[id];
    const others = running().filter((o) => o !== id && state.studios[o].mla);
    let mode = "exclusive";
    if (others.length && st.mla) {
      const names = others.map((o) => `<b>${esc(state.studios[o].name)}</b>`).join(", ");
      const choice = await modal({
        title: `Launch ${st.name}?`,
        body: `<p>${names} ${others.length > 1 ? "are" : "is"} using the Modalix MLA. Studios run best one at a time.</p>
          ${state.system && state.system.cma_free != null ? `<p class="muted" style="font-size:13px">MLA memory free now: <b>${fmtBytes(state.system.cma_free)}</b>. Running alongside works only if ${esc(st.short)}'s models fit.</p>` : ""}`,
        actions: [
          { label: "Cancel", value: null, cls: "ghost" },
          { label: "Run Alongside", value: "alongside" },
          { label: `Stop & Launch`, value: "exclusive", cls: "primary" },
        ],
      });
      if (!choice) return;
      mode = choice;
    }
    if (await act(id, "launch", { mode })) {
      if (!(opts && opts.stay)) location.hash = `#/studio/${id}/open`;
    }
  }

  // Install from (or switch an installed studio to) another branch.
  async function pickBranch(id) {
    const st = state.studios[id];
    let data;
    try { data = await api(`/api/studios/${id}/branches`); }
    catch (e) { toast(e.message, "err"); return; }
    const current = st.installed ? (data.installed || st.branch) : data.default;
    let chosen = current;
    const options = data.branches.map((b) => `<option value="${esc(b.name)}" ${b.name === current ? "selected" : ""}>${esc(b.name)}${b.name === data.default ? " (Default)" : ""}${st.installed && b.name === data.installed ? " (Installed)" : ""} · ${short(b.sha)}</option>`).join("");
    const switching = st.installed;
    const go = await modal({
      title: switching ? `Switch ${st.name} to Another Branch` : `Install ${st.name} From a Branch`,
      body: `<p>${switching
          ? `Installed from <b class="mono">${esc(data.installed || st.branch)}</b>. The chosen branch's files replace the installed ones (models and settings stay), then setup runs again.`
          : `Install uses <b class="mono">${esc(data.default)}</b> unless you pick another branch.`}</p>
        <label class="field"><span>Branch</span><select id="branchSelect">${options}</select></label>
        <p class="muted" style="font-size:12.5px">${data.branches.length} ${data.branches.length === 1 ? "Branch" : "Branches"} on <span class="mono">${esc(st.repo)}</span>. Update checks follow the branch you install.</p>`,
      actions: [{ label: "Cancel", value: false, cls: "ghost" }, { label: switching ? "Switch Branch" : "Install", value: true, cls: "primary" }],
      onOpen: (el) => { const sel = el.querySelector("#branchSelect"); sel.focus(); sel.addEventListener("change", () => { chosen = sel.value; }); },
    });
    if (!go) return;
    if (switching && chosen === (data.installed || st.branch)) {
      const again = await modal({ title: "Reinstall the Same Branch?", body: `<p><b class="mono">${esc(chosen)}</b> is already installed. Download it again and re-run setup?</p>`,
        actions: [{ label: "Cancel", value: false, cls: "ghost" }, { label: "Reinstall", value: true, cls: "primary" }] });
      if (!again) return;
    }
    if (await act(id, "install", { branch: chosen })) toast(`${switching ? "Switching" : "Installing"} ${st.name} to ${chosen}`, "info");
  }

  async function stop(id, force) {
    const st = state.studios[id];
    if (force) {
      const ok = await modal({
        title: `Force Kill ${st.name}?`,
        body: `<p>Sends SIGKILL to every ${esc(st.short)} process at once. Unsaved work in the studio is lost; use Stop for a clean shutdown.</p>`,
        actions: [{ label: "Cancel", value: false, cls: "ghost" }, { label: "Kill Now", value: true, cls: "danger solid" }],
      });
      if (!ok) return;
    }
    if (await act(id, force ? "kill" : "stop")) toast(`${force ? "Killing" : "Stopping"} ${st.name}`, "info");
  }

  async function uninstall(id) {
    const st = state.studios[id];
    const typed = await modal({
      title: `Uninstall ${st.name}?`,
      body: `<p>This deletes <span class="mono">${esc(st.dir)}</span>, including downloaded models. Type <b class="mono">${esc(id)}</b> to confirm.</p>`,
      input: { placeholder: id },
      actions: [{ label: "Cancel", value: null, cls: "ghost" }, { label: "Uninstall", value: true, cls: "danger solid" }],
    });
    if (typed == null) return;
    if (typed !== id) { toast("The name did not match. Nothing was removed.", "err"); return; }
    if (await act(id, "uninstall", { confirm: id })) toast(`Removing ${st.name}`, "info");
  }

  async function update(id) {
    if (id === "launcher") {
      try { await api("/api/launcher/update", { method: "POST" }); toast("Updating the launcher…", "info"); }
      catch (e) { toast(e.message, "err"); }
      return;
    }
    const st = state.studios[id];
    if (st.state === "running") {
      const ok = await modal({ title: `Update ${st.name}?`, body: "<p>The studio is running. It keeps running the old version until you restart it.</p>",
        actions: [{ label: "Cancel", value: false, cls: "ghost" }, { label: "Update", value: true, cls: "primary" }] });
      if (!ok) return;
    }
    if (await act(id, "update")) toast(`Updating ${st.name}`, "info");
  }

  let lastUpdateCheck = 0;
  async function checkUpdates(force) {
    lastUpdateCheck = Date.now();
    $$("#checkUpdatesBtn, #checkUpdatesBtn2").forEach((b) => { b.disabled = true; b.innerHTML = `<span class="spin"></span>Checking…`; });
    try {
      state.updates = await api(`/api/updates${force ? "?force=1" : ""}`);
      if (force) {
        const all = Object.values(state.updates);
        const n = all.filter((u) => ["available", "diverged"].includes(u.status)).length;
        const failed = all.filter((u) => u.status === "error" || u.stale);
        if (failed.length) toast(`Could Not Check ${failed.length} of ${all.length}: ${(failed[0].note || failed[0].error || "").split(";")[0]}`, "err");
        else toast(n ? `${n} Update${n > 1 ? "s" : ""} Available` : "Everything Is Up to Date", n ? "info" : "ok");
      }
    } catch (e) {
      if (force) toast(`Update check failed: ${e.message}`, "err");
    }
    $$("#checkUpdatesBtn, #checkUpdatesBtn2").forEach((b) => { b.disabled = false; b.innerHTML = `${icon("refresh")}Check for Updates`; });
    renderAll();
    renderUpdates();
  }

  // Delegated clicks: data-act="action:id"
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-act]");
    if (!b) return;
    e.preventDefault();
    const [a, id, extra] = b.dataset.act.split(":");
    ({
      install: () => install(id),
      launch: () => launch(id),
      open: () => { location.hash = ["running", "starting"].includes(state.studios[id].state) ? `#/studio/${id}/open` : `#/studio/${id}`; },
      stop: () => stop(id, false),
      kill: () => stop(id, true),
      restart: () => act(id, "restart").then((ok) => ok && toast(`Restarting ${state.studios[id].name}`, "info")),
      update: () => update(id),
      uninstall: () => uninstall(id),
      cancel: () => act(id, "cancel"),
      branch: () => pickBranch(id),
      checkone: () => checkOne(id),
      dismiss: () => act(id, "dismiss"),
      popout: () => popOut(id),
      logs: () => { location.hash = "#/manager"; setTimeout(() => selectConsole(`log:${id}`, true), 50); },
      killpid: () => killPid(extra),
      menu: () => openCardMenu(id, b),
      expand: () => { state.expanded.has(id) ? state.expanded.delete(id) : state.expanded.add(id); renderProcTable(); },
    }[a] || (() => {}))();
  });

  // Pop a studio out into its own tab; this tab goes to the Manager so the
  // studio isn't open twice (the embedded frame is closed by the route change).
  function popOut(id) {
    const st = state.studios[id];
    const w = window.open(studioUrl(st), "_blank", "noopener");
    toast(`${st.name} Opened in a New Tab`, "ok");
    location.hash = "#/manager";
    return w;
  }

  async function killPid(pid) {
    const ok = await modal({ title: `Kill Process ${pid}?`, body: "<p>Sends SIGKILL to this one process.</p>",
      actions: [{ label: "Cancel", value: false, cls: "ghost" }, { label: "Kill", value: true, cls: "danger solid" }] });
    if (!ok) return;
    try { await api(`/api/procs/${pid}/kill`, { method: "POST" }); toast(`Killed ${pid}`, "ok"); }
    catch (e) { toast(e.message, "err"); }
  }

  // ------------------------------------------------------------------ showcase
  // GIFs: the poster (first frame) shows until the GIF is on screen, then the
  // GIF plays; it goes back to the poster off screen so a page full of GIFs
  // doesn't decode them all at once. With reduced motion the GIF plays only on
  // hover or focus.
  const REDUCED = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const gifIO = "IntersectionObserver" in window ? new IntersectionObserver((entries) => {
    entries.forEach((e) => { e.target.dataset.visible = e.isIntersecting ? "1" : ""; setGifPlaying(e.target, e.isIntersecting && !REDUCED); });
  }, { threshold: 0.25 }) : null;
  function setGifPlaying(img, on) {
    const want = on ? img.dataset.gif : (img.dataset.poster || img.dataset.gif);
    if (want && img.getAttribute("src") !== want) img.setAttribute("src", want);
  }
  function watchGifs(root) {
    $$("img[data-gif]", root).forEach((img) => {
      if (img.dataset.watched) return;
      img.dataset.watched = "1";
      if (gifIO) gifIO.observe(img); else setGifPlaying(img, !REDUCED);
      if (REDUCED) {
        const host = img.closest("[data-gif-host]") || img;
        host.addEventListener("mouseenter", () => setGifPlaying(img, true));
        host.addEventListener("mouseleave", () => setGifPlaying(img, false));
      }
    });
  }
  function gifImg(src, poster, alt, cls) {
    return `<img class="${cls || ""}" data-gif="${S}${src}" ${poster ? `data-poster="${S}${poster}"` : ""}
      src="${S}${poster || src}" alt="${esc(alt || "")}" decoding="async">`;
  }
  function galleryOf(st) {
    return (st.gallery || []).map((g) => ({ ...g, src: `studios/${st.id}/gallery/${g.file}`, posterSrc: g.poster ? `studios/${st.id}/gallery/${g.poster}` : "" }));
  }
  function mediaHtml(st, cls) {
    const gal = galleryOf(st);
    if (gal.length) {
      const hero = gal.find((g) => g.file === st.media.hero) || gal[0];
      return gifImg(hero.src, hero.posterSrc, `${st.name}: ${hero.caption}`, cls);
    }
    const m = st.media || {};
    if (m.type === "video") {
      return `<video class="${cls || ""}" src="${S}${m.src}" ${m.poster ? `poster="${S}${m.poster}"` : ""} autoplay muted loop playsinline preload="metadata"></video>`;
    }
    return `<img class="${cls || ""}" src="${S}${m.src}" alt="${esc(st.name)}" loading="lazy">`;
  }

  // Studio page gallery: a big stage, captions, and thumbnails grouped by kind.
  function galleryHtml(st, index) {
    const gal = galleryOf(st);
    if (!gal.length) return `<div class="detail-media">${mediaHtml(st)}</div>`;
    const i = Math.min(Math.max(index || 0, 0), gal.length - 1);
    const groups = [...new Set(gal.map((g) => g.group || "Features"))];
    return `<div class="gallery" data-gallery="${st.id}" data-index="${i}">
      <div class="g-stage" data-gif-host>
        ${gifImg(gal[i].src, gal[i].posterSrc, gal[i].caption, "g-main")}
        ${gal.length > 1 ? `<button class="g-nav prev" data-g="prev" aria-label="Previous">${icon("back")}</button>
        <button class="g-nav next" data-g="next" aria-label="Next">${icon("chevron")}</button>` : ""}
        <button class="g-expand" data-g="expand" aria-label="Full Screen" title="Full Screen">${icon("expand")}</button>
        <span class="g-count">${i + 1} / ${gal.length}</span>
      </div>
      <div class="g-caption"><b>${esc(gal[i].title)}</b><span>${esc(gal[i].caption)}</span></div>
      ${gal.length > 1 ? groups.map((grp) => `
        ${groups.length > 1 ? `<div class="g-group">${esc(grp)}</div>` : ""}
        <div class="g-thumbs">${gal.map((g, j) => (g.group || "Features") !== grp ? "" : `
          <button class="g-thumb ${j === i ? "active" : ""}" data-g="${j}" title="${esc(g.title)}">
            <img src="${S}${g.posterSrc || g.src}" alt="" loading="lazy"><span>${esc(g.title)}</span>
          </button>`).join("")}</div>`).join("") : ""}
    </div>`;
  }

  function showGallery(box, i) {
    const st = state.studios[box.dataset.gallery];
    const gal = galleryOf(st);
    i = (i + gal.length) % gal.length;
    box.dataset.index = i;
    const g = gal[i];
    const img = box.querySelector(".g-main");
    img.dataset.gif = `${S}${g.src}`;
    img.dataset.poster = g.posterSrc ? `${S}${g.posterSrc}` : "";
    img.alt = g.caption;
    setGifPlaying(img, !REDUCED && (!gifIO || img.dataset.visible === "1"));
    if (state.route.page === "studio" && state.route.id === box.dataset.gallery) state.route.g = i;
    box.querySelector(".g-count").textContent = `${i + 1} / ${gal.length}`;
    box.querySelector(".g-caption").innerHTML = `<b>${esc(g.title)}</b><span>${esc(g.caption)}</span>`;
    $$(".g-thumb", box).forEach((t) => t.classList.toggle("active", +t.dataset.g === i));
    const active = box.querySelector(".g-thumb.active");
    if (active) active.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "smooth" });
  }

  function openLightbox(sid, i) {
    const st = state.studios[sid];
    const gal = galleryOf(st);
    if (closeLightbox) closeLightbox();
    const opener = document.activeElement;
    const back = document.createElement("div");
    back.className = "lightbox";
    back.setAttribute("role", "dialog");
    back.setAttribute("aria-modal", "true");
    back.setAttribute("aria-label", `${st.name} Demos`);
    back.innerHTML = `<div class="lb-inner">
      <img class="lb-img" alt="">
      <div class="lb-cap"><b></b><span></span><em></em></div>
      <button class="lb-btn lb-close" aria-label="Close">${icon("x")}</button>
      ${gal.length > 1 ? `<button class="lb-btn lb-prev" aria-label="Previous">${icon("back")}</button><button class="lb-btn lb-next" aria-label="Next">${icon("chevron")}</button>` : ""}
    </div>`;
    const set = (j) => {
      i = (j + gal.length) % gal.length;
      const g = gal[i];
      back.querySelector(".lb-img").src = `${S}${g.src}`;
      back.querySelector(".lb-img").alt = g.caption;
      back.querySelector(".lb-cap b").textContent = `${st.short} · ${g.title}`;
      back.querySelector(".lb-cap span").textContent = g.caption;
      back.querySelector(".lb-cap em").textContent = `${i + 1} / ${gal.length}`;
    };
    const close = () => {
      back.remove(); removeEventListener("keydown", onKey); closeLightbox = null;
      if (opener && opener.isConnected) opener.focus();
    };
    const onKey = (e) => {
      if (e.key === "Tab") {           // keep focus inside the viewer
        const f = $$("button", back);
        const k = f.indexOf(document.activeElement);
        e.preventDefault();
        f[(k + (e.shiftKey ? -1 : 1) + f.length) % f.length].focus();
      } else if (e.key === "Escape") close();
      else if (e.key === "ArrowRight") set(i + 1);
      else if (e.key === "ArrowLeft") set(i - 1);
    };
    back.addEventListener("click", (e) => {
      if (e.target.closest(".lb-close") || e.target === back) close();
      else if (e.target.closest(".lb-next")) set(i + 1);
      else if (e.target.closest(".lb-prev")) set(i - 1);
    });
    addEventListener("keydown", onKey);
    document.body.appendChild(back);
    closeLightbox = close;
    set(i);
    back.querySelector(".lb-close").focus();
  }
  let closeLightbox = null;

  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-g]");
    const box = b && b.closest(".gallery");
    if (!box) return;
    e.preventDefault();
    const i = +box.dataset.index;
    const v = b.dataset.g;
    if (v === "prev") showGallery(box, i - 1);
    else if (v === "next") showGallery(box, i + 1);
    else if (v === "expand") openLightbox(box.dataset.gallery, i);
    else showGallery(box, +v);
  });
  document.addEventListener("keydown", (e) => {
    if (document.querySelector(".lightbox, .modal-back, .intro")) return;
    const box = state.route.page === "studio" && document.querySelector(".gallery");
    if (!box || /INPUT|TEXTAREA/.test((document.activeElement || {}).tagName)) return;
    if (e.key === "ArrowRight") showGallery(box, +box.dataset.index + 1);
    if (e.key === "ArrowLeft") showGallery(box, +box.dataset.index - 1);
  });

  // Home page reel: every studio's GIFs in one scrolling strip.
  function renderReel() {
    const reel = $("#reel");
    if (!reel || reel.dataset.built) return;
    const items = state.order.flatMap((id) => galleryOf(state.studios[id]).map((g, i) => ({ id, i, g })));
    if (!items.length) { $("#reelSection").classList.add("hidden"); return; }
    reel.dataset.built = "1";
    reel.innerHTML = items.map(({ id, i, g }) => `
      <a class="reel-item" href="#/studio/${id}/${i}" data-gif-host title="${esc(g.caption)}">
        <div class="reel-media">${gifImg(g.src, g.posterSrc, g.caption)}</div>
        <div class="reel-meta"><img src="${S}${state.studios[id].icon_small}" alt=""><span><b>${esc(g.title)}</b><em>${esc(state.studios[id].name)}</em></span></div>
      </a>`).join("");
    watchGifs(reel);
    $("#reelCount").textContent = `${items.length} Demos`;
  }
  $("#reelPrev") && $("#reelPrev").addEventListener("click", () => $("#reel").scrollBy({ left: -$("#reel").clientWidth * 0.8, behavior: "smooth" }));
  $("#reelNext") && $("#reelNext").addEventListener("click", () => $("#reel").scrollBy({ left: $("#reel").clientWidth * 0.8, behavior: "smooth" }));

  function updateBadge(id) {
    const u = state.updates[id];
    if (!u || u.status !== "available") return "";
    return `<span class="pill update" data-act="update:${id}" title="${esc(u.latest.message)}">${icon("download")}Update</span>`;
  }

  function primaryActions(st) {
    const id = st.id;
    switch (st.state) {
      case "not_installed":
        return `<button class="btn primary" data-act="install:${id}">${icon("download")}Install</button>`;
      case "installing": case "updating": case "uninstalling":
        return `<button class="btn" data-act="logs:${id}">${icon("terminal")}Logs</button>`;
      case "starting":
        return `<button class="btn primary" data-act="open:${id}"><span class="spin"></span>Starting…</button>`;
      case "running":
        return `<button class="btn primary" data-act="open:${id}">${icon("play")}Open</button>
                <button class="btn ghost sm" data-act="popout:${id}" title="Open in a new tab">${icon("external")}</button>`;
      case "stopping":
        return `<button class="btn" disabled><span class="spin"></span>Stopping…</button>`;
      default:
        return `<button class="btn primary" data-act="launch:${id}">${icon("play")}Launch</button>`;
    }
  }

  function jobHtml(st) {
    if (!["installing", "updating", "uninstalling"].includes(st.state)) return "";
    const step = (st.job && st.job.step) || "Working…";
    return `<div class="job"><div class="job-bar"></div><div class="job-step">${esc(step)} · ${fmtDur(st.job && st.job.elapsed)}</div></div>`;
  }

  function errorHtml(st) {
    if (st.state === "running" && st.app_error) {
      return `<div class="job-step" style="color:var(--warn);white-space:normal">${icon("alert")} The studio reports: ${esc(st.app_error)}
        <a href="#" data-act="logs:${st.id}">Logs</a></div>`;
    }
    if (st.state !== "error") return "";
    return `<div class="job-step" style="color:var(--err);white-space:normal">${icon("alert")} ${esc(st.error)}
      <a href="#" data-act="logs:${st.id}">Logs</a> · <a href="#" data-act="dismiss:${st.id}">Dismiss</a></div>`;
  }

  function renderGrid() {
    const grid = $("#studioGrid");
    state.order.forEach((id) => {
      const st = state.studios[id];
      let card = grid.querySelector(`[data-card="${id}"]`);
      if (!card) {
        card = document.createElement("article");
        card.className = "card";
        card.dataset.card = id;
        card.innerHTML = `
          <div class="card-media" data-act="open:${id}" data-gif-host>${mediaHtml(st)}
            ${(st.gallery || []).length > 1 ? `<span class="demo-chip">${icon("play")}${st.gallery.length} Demos</span>` : ""}
            <div class="card-badges"><span class="pill js-state"></span><span class="js-update"></span></div>
          </div>
          <div class="card-body">
            <div class="card-title">
              <div class="studio-icon"><img src="${S}${st.icon}" alt=""></div>
              <div><h3>${esc(st.name)}</h3><div class="cat">${esc(st.category)}</div></div>
            </div>
            <p class="card-tagline">${esc(st.tagline)}</p>
            <ul class="bullets">${(st.bullets || []).map((b) => `<li>${esc(typeof b === "string" ? b : JSON.stringify(b))}</li>`).join("")}</ul>
            <div class="js-job"></div>
            <div class="card-foot">
              <div class="metric"><b>${esc(st.metric.value || "")}</b><span>${esc(st.metric.label || "")}</span></div>
              <div class="btn-row"><a class="btn ghost sm" href="#/studio/${id}">Details</a>
                <span class="card-menu-wrap"><button class="btn ghost sm icon-only" data-act="menu:${id}" title="More" aria-label="More Actions">${icon("dots")}</button></span>
                <span class="btn-row js-actions"></span></div>
            </div>
          </div>`;
        grid.appendChild(card);
        watchGifs(card);
      }
      const key = `${st.state}|${st.branch}|${st.app_error}|${st.job && st.job.step}|${Math.floor((st.job && st.job.elapsed) || 0)}|${(state.updates[id] || {}).status}|${st.error}`;
      if (card.dataset.key === key) return;
      card.dataset.key = key;
      const sk = `${st.state}|${st.installed}`;
      if (card.dataset.sk !== sk) { card.dataset.sk = sk; if (card.querySelector(".menu-pop")) closeCardMenus(); }
      card.classList.toggle("is-running", st.state === "running");
      const pill = card.querySelector(".js-state");
      pill.className = `pill js-state ${st.state}`;
      pill.innerHTML = `<i class="dot ${st.state === "running" ? "ok" : BUSY.has(st.state) ? "warn" : st.state === "error" ? "err" : ""}"></i>${STATE_LABEL[st.state] || st.state}`;
      card.querySelector(".js-update").innerHTML = updateBadge(id);
      const cat = card.querySelector(".card-title .cat");
      if (cat) cat.innerHTML = esc(st.category) + (st.installed && st.branch && st.branch !== st.default_branch ? ` · <span class="branch-chip">${icon("branch")}${esc(st.branch)}</span>` : "");
      card.querySelector(".js-job").innerHTML = jobHtml(st) + errorHtml(st);
      const actions = card.querySelector(".js-actions");
      const actKey = st.state;
      if (actions.dataset.k !== actKey) { actions.dataset.k = actKey; actions.innerHTML = primaryActions(st); }
    });
  }

  function renderSystem() {
    const s = state.system;
    if (!s) return;
    const ip = (s.ips && s.ips[0]) || location.hostname;
    const cmaUsed = s.cma_total ? s.cma_total - s.cma_free : null;
    $("#boardChips").innerHTML = [
      `<span class="chip">${icon("cpu")}<b>${esc(s.board)}</b></span>`,
      `<span class="chip">${icon("wifi")}<b>${esc(ip)}</b> · ${esc(s.hostname)}</span>`,
      `<span class="chip">${icon("zap")}CPU <b>${Math.round(s.cpu)}%</b></span>`,
      `<span class="chip">${icon("server")}RAM <b>${fmtBytes(s.mem_used)}</b> / ${fmtBytes(s.mem_total)}</span>`,
      s.temp != null ? `<span class="chip">${icon("temp")}<b>${s.temp}°C</b></span>` : "",
    ].join("");
    const host = `${esc(s.hostname)} · ${esc(ip)} · Up ${fmtDur(s.uptime)}`;
    $("#footerHost").innerHTML = host; $("#footerHost2").innerHTML = host;
    if (state.route.page === "manager") renderStats();
  }

  function meter(pct) {
    const cls = pct > 90 ? "crit" : pct > 75 ? "hot" : "";
    return `<div class="meter ${cls}"><i style="width:${Math.min(100, Math.max(0, pct)).toFixed(1)}%"></i></div>`;
  }

  function renderStats() {
    const s = state.system;
    if (!s) return;
    const cmaUsed = s.cma_total ? s.cma_total - s.cma_free : null;
    $("#stats").innerHTML = [
      `<div class="stat"><div class="s-label">CPU</div><div class="s-val">${Math.round(s.cpu)}%</div><div class="s-sub">${s.cores} Cores · Load ${s.load[0]}</div>${meter(s.cpu)}</div>`,
      `<div class="stat"><div class="s-label">Memory</div><div class="s-val">${fmtBytes(s.mem_used)}</div><div class="s-sub">of ${fmtBytes(s.mem_total)} Total</div>${meter(100 * s.mem_used / s.mem_total)}</div>`,
      `<div class="stat"><div class="s-label">MLA Memory (CMA)</div><div class="s-val">${cmaUsed != null ? fmtBytes(cmaUsed) : "–"}</div><div class="s-sub">${s.cma_total ? `${fmtBytes(s.cma_free)} Free` : "Not Reported"}</div>${s.cma_total ? meter(100 * cmaUsed / s.cma_total) : ""}</div>`,
      `<div class="stat"><div class="s-label">Temperature</div><div class="s-val">${s.temp != null ? `${s.temp}°C` : "–"}</div><div class="s-sub">Hottest Zone</div>${s.temp != null ? meter(s.temp) : ""}</div>`,
      `<div class="stat"><div class="s-label">Storage</div><div class="s-val">${fmtBytes(s.disk_total - s.disk_used)}</div><div class="s-sub">Free on ${esc(s.disk_path)}</div>${meter(100 * s.disk_used / s.disk_total)}</div>`,
      `<div class="stat"><div class="s-label">Board</div><div class="s-val" title="${esc(s.board)}">${esc(s.hostname)}</div><div class="s-sub">${esc((s.ips || []).join(", "))} · Up ${fmtDur(s.uptime)}</div></div>`,
    ].join("");
  }

  // Header: one status dot per studio, plus a menu listing every studio's state
  // with its next action.
  function dotClass(state) {
    return state === "running" ? "ok" : BUSY.has(state) ? "warn" : state === "error" ? "err" : state === "not_installed" ? "off" : "";
  }
  function quickAction(st) {
    const id = st.id;
    switch (st.state) {
      case "not_installed": return `<button class="btn sm primary" data-act="install:${id}">${icon("download")}Install</button>`;
      case "running": return `<button class="btn sm primary" data-act="open:${id}">${icon("play")}Open</button><button class="btn sm" data-act="stop:${id}" title="Stop">${icon("stop")}</button>`;
      case "starting": return `<button class="btn sm" data-act="open:${id}"><span class="spin"></span>View</button><button class="btn sm" data-act="stop:${id}" title="Stop">${icon("stop")}</button>`;
      case "installing": case "updating": case "uninstalling": case "stopping":
        return `<button class="btn sm" data-act="logs:${id}">${icon("terminal")}Logs</button>`;
      default: return `<button class="btn sm primary" data-act="launch:${id}">${icon("play")}Launch</button>`;
    }
  }
  function renderHeader() {
    const ids = state.order;
    const r = running().length;
    const items = ids.map((id) => {
      const st = state.studios[id];
      return `<span class="ss-item ${dotClass(st.state)}" title="${esc(st.name)}: ${STATE_LABEL[st.state] || st.state}">
        <img src="${S}${st.icon_small}" alt=""><i class="dot ${dotClass(st.state)}"></i></span>`;
    }).join("");
    const key = ids.map((id) => state.studios[id].state + (state.studios[id].app_error ? "!" : "")).join("|");
    const strip = $("#statusItems");
    if (strip.dataset.k !== key) { strip.dataset.k = key; strip.innerHTML = items; }
    const busy = ids.filter((id) => BUSY.has(state.studios[id].state)).length;
    $("#statusText").textContent = r ? `${r} of ${ids.length} Running` : busy ? "Working…" : "None Running";
    if (!$("#statusMenu").classList.contains("hidden")) renderStatusMenu();
    const n = Object.values(state.updates).filter((u) => ["available", "diverged"].includes(u.status)).length;
    $("#navUpdateDot").classList.toggle("hidden", !n);
  }
  function renderStatusMenu() {
    const menu = $("#statusMenu");
    const html = state.order.map((id) => {
      const st = state.studios[id];
      const extra = st.state === "running" && st.procs.length
        ? `${fmtBytes(st.rss)} · ${st.cpu}% CPU · Up ${fmtDur(st.uptime)}`
        : st.state === "running" || st.state === "starting" ? "" : st.installed ? "Installed" : "Not Installed";
      return `<div class="sm-row">
        <img src="${S}${st.icon_small}" alt="">
        <div class="sm-main"><a href="#/studio/${id}"><b>${esc(st.name)}</b></a>
          <span><span class="pill ${st.state}">${st.app_error ? `<i class="dot warn"></i>` : ""}${STATE_LABEL[st.state]}</span> <em>${esc(extra)}</em></span></div>
        <div class="btn-row">${quickAction(st)}</div>
      </div>`;
    }).join("") + `<div class="sm-foot"><a class="btn sm ghost" href="#/manager">${icon("sliders")}Open Manager</a></div>`;
    // Rebuild only when a studio's state or actions change; otherwise update the
    // stats text in place so hover and keyboard focus survive the 2 s pushes.
    const skey = state.order.map((id) => { const st = state.studios[id]; return `${id}:${st.state}:${st.installed}:${!!st.app_error}`; }).join("|");
    if (menu.dataset.k !== skey) { menu.dataset.k = skey; menu.innerHTML = html; return; }
    const tmp = document.createElement("div"); tmp.innerHTML = html;
    const fresh = $$(".sm-main em", tmp);
    $$(".sm-main em", menu).forEach((em, j) => { if (fresh[j] && em.textContent !== fresh[j].textContent) em.textContent = fresh[j].textContent; });
  }
  function toggleStatusMenu(open) {
    const menu = $("#statusMenu");
    const want = open == null ? menu.classList.contains("hidden") : open;
    menu.classList.toggle("hidden", !want);
    $("#statusStrip").setAttribute("aria-expanded", String(want));
    if (want) renderStatusMenu();
  }
  $("#statusStrip").addEventListener("click", (e) => { e.stopPropagation(); toggleStatusMenu(); });
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".status-wrap") || e.target.closest("[data-act], a")) toggleStatusMenu(false);
    if (!e.target.closest(".card-menu-wrap") || e.target.closest(".menu-pop [data-act], .menu-pop a")) closeCardMenus();
  });
  addEventListener("keydown", (e) => { if (e.key === "Escape") { toggleStatusMenu(false); closeCardMenus(); } });

  // Card "⋯" menu: details, logs, updates and uninstall.
  function closeCardMenus() { $$(".menu-pop").forEach((m) => m.remove()); }
  function openCardMenu(id, anchor) {
    closeCardMenus();
    const st = state.studios[id];
    const busy = BUSY.has(st.state);
    const pop = document.createElement("div");
    pop.className = "menu-pop";
    pop.innerHTML = [
      `<a href="#/studio/${id}">${icon("book")}Details &amp; Demos</a>`,
      `<a href="#" data-act="logs:${id}">${icon("terminal")}Live Log</a>`,
      `<a href="#" data-act="checkone:${id}">${icon("refresh")}Check for Updates</a>`,
      st.installed && !busy ? `<a href="#" data-act="update:${id}">${icon("download")}Update</a>` : "",
      !busy ? `<a href="#" data-act="branch:${id}">${icon("branch")}${st.installed ? "Switch Branch…" : "Install From Branch…"}</a>` : "",
      ["running", "starting"].includes(st.state) ? `<a href="#" data-act="stop:${id}">${icon("stop")}Stop</a>` : "",
      `<a href="${esc(st.docs)}" target="_blank" rel="noopener">${icon("external")}GitHub</a>`,
      st.installed && !busy ? `<hr><a href="#" class="danger" data-act="uninstall:${id}">${icon("trash")}Uninstall…</a>` : "",
    ].join("");
    anchor.closest(".card-menu-wrap").appendChild(pop);
  }

  $$("[data-replay-intro]").forEach((b) => b.addEventListener("click", (e) => { e.preventDefault(); replayIntro(); }));

  // ------------------------------------------------------------------ studio page
  let studioPage = { id: null, mode: null, log: null, trust: null };

  function disposeStudioPage() {
    if (studioPage.log) studioPage.log.dispose();
    studioPage = { id: null, mode: null, log: null, trust: null };
  }

  // The details page is always reachable; the studio itself shows only on
  // #/studio/<id>/open while it is starting or running.
  function studioMode(st) {
    if (state.route.view !== "open") return "detail";
    if (st.state === "running") return "frame";
    if (st.state === "starting") return "boot";
    return "detail";
  }

  function renderStudioPage(force) {
    const id = state.route.id;
    const st = state.studios[id];
    const view = $("#studioView");
    if (!st) { view.innerHTML = `<div class="wrap section"><h2>Unknown studio</h2><p><a href="#/">Back to the showcase</a></p></div>`; return; }
    const mode = studioMode(st);
    if (!force && studioPage.id === id && studioPage.mode === mode) { updateStudioPage(st); return; }
    disposeStudioPage();
    studioPage.id = id; studioPage.mode = mode;
    const toolbar = `
      <div class="studio-toolbar">
        <a class="btn ghost sm" href="#/">${icon("back")}<span class="hide-sm">Studios</span></a>
        <div class="t-title"><img src="${S}${st.icon_small}" alt=""><b>${esc(st.name)}</b><span class="pill js-state"></span></div>
        <span class="t-url mono">${esc(studioUrl(st))}</span>
        <span class="spacer"></span>
        <span class="btn-row js-tools"></span>
      </div>`;
    if (mode === "frame") {
      view.innerHTML = `${toolbar}
        <div class="studio-split">
          <iframe class="studio-frame" id="studioFrame" title="${esc(st.name)}" allow="camera; microphone; clipboard-read; clipboard-write; fullscreen; autoplay" allowfullscreen></iframe>
          <div class="drawer" id="logDrawer"><div class="drawer-head"><span>${icon("terminal")} ${esc(st.name)} log</span><button class="btn sm ghost" id="drawerClose">${icon("x")}</button></div><div class="xterm-host" id="drawerHost"></div></div>
        </div>`;
      $("#drawerClose").onclick = () => toggleDrawer(false);
      loadFrame(st);
    } else if (mode === "boot") {
      view.innerHTML = `${toolbar}
        <div class="studio-split"><div class="wrap section" style="max-width:900px">
          <div class="panel" style="width:100%">
            <div style="display:flex;gap:16px;align-items:center"><div class="boot-anim"><img src="${S}icons/neat-mark.png" alt=""></div>
            <div><h3>Starting ${esc(st.name)}…</h3><p class="js-boot-sub">Loading Models Onto the Modalix MLA. The Studio Opens Here When It Is Ready.</p></div></div>
            <div class="log-box" id="bootLog"></div>
          </div></div></div>`;
      studioPage.log = attachLog(id, $("#bootLog"));
    } else {
      view.innerHTML = `${toolbar}
        <div class="wrap"><div class="detail">
          ${galleryHtml(st, state.route.g)}
          <div class="detail-side">
            <div><div class="eyebrow">${esc(st.category)}</div><h2 style="margin-top:4px">${esc(st.tagline)}</h2><div class="spectrum-rule"></div></div>
            <p class="pitch">${esc(st.pitch)}</p>
            <ul class="bullets">${st.bullets.map((b) => `<li>${esc(b)}</li>`).join("")}</ul>
            <div class="js-job"></div>
            <div class="btn-row js-main"></div>
            <div class="box update-box js-updatebox"></div>
            <dl class="kv box">
              <dt>Install</dt><dd class="mono">${esc(st.dir)}</dd>
              <dt>Branch</dt><dd class="mono js-branch">${esc(st.installed ? st.branch : st.default_branch)}${(st.installed ? st.branch : st.default_branch) === st.default_branch ? ' <span class="muted">(Default)</span>' : ""}</dd>
              <dt>Address</dt><dd class="mono">${esc(studioUrl(st))}</dd>
              <dt>Source</dt><dd><a href="${esc(st.docs)}" target="_blank" rel="noopener">${esc(st.repo)}</a></dd>
            </dl>
          </div>
        </div>
        <div class="section" style="padding-top:0"><div class="section-head"><h3>Live Activity</h3><span class="btn-row"><a class="btn sm" href="/api/studios/${id}/log">${icon("download")}Download Log</a></span></div>
          <div class="log-box" id="detailLog"></div></div>
        </div>`;
      studioPage.log = attachLog(id, $("#detailLog"));
      watchGifs(view);
    }
    updateStudioPage(st);
  }

  function updateStudioPage(st) {
    const view = $("#studioView");
    const pill = view.querySelector(".js-state");
    if (pill) { pill.className = `pill js-state ${st.state}`; pill.textContent = STATE_LABEL[st.state] || st.state; }
    const tools = view.querySelector(".js-tools");
    if (tools) {
      const k = st.state + studioPage.mode;
      if (tools.dataset.k !== k) {
        tools.dataset.k = k;
        const isUp = ["running", "starting"].includes(st.state);
        tools.innerHTML = [
          studioPage.mode !== "detail" ? `<a class="btn sm" href="#/studio/${st.id}" title="Details, demos, updates and branch">${icon("book")}<span class="hide-sm">Details</span></a>` : "",
          studioPage.mode === "frame" ? `<button class="btn sm" id="logToggle">${icon("terminal")}<span class="hide-sm">Log</span></button>` : "",
          isUp ? `<button class="btn sm" data-act="popout:${st.id}">${icon("external")}<span class="hide-sm">Pop Out</span></button>` : "",
          isUp ? `<button class="btn sm" data-act="restart:${st.id}">${icon("refresh")}<span class="hide-sm">Restart</span></button>` : "",
          isUp ? `<button class="btn sm danger" data-act="stop:${st.id}">${icon("stop")}<span class="hide-sm">Stop</span></button>` : "",
        ].join("");
        const lt = $("#logToggle"); if (lt) lt.onclick = () => toggleDrawer();
      }
    }
    const main = view.querySelector(".js-main");
    if (main) {
      const k = st.state;
      if (main.dataset.k !== k) {
        main.dataset.k = k;
        const extra = [];
        if (st.installed && !BUSY.has(st.state)) extra.push(`<button class="btn" data-act="update:${st.id}">${icon("download")}Update</button>`);
        if (!BUSY.has(st.state)) extra.push(`<button class="btn ghost" data-act="branch:${st.id}">${icon("branch")}${st.installed ? "Switch Branch…" : "Install From Branch…"}</button>`);
        if (BUSY.has(st.state) && st.state !== "starting" && st.state !== "stopping") extra.push(`<button class="btn danger" data-act="cancel:${st.id}">${icon("x")}Cancel</button>`);
        if (st.installed && !BUSY.has(st.state)) extra.push(`<button class="btn ghost danger" data-act="uninstall:${st.id}">${icon("trash")}Uninstall</button>`);
        main.innerHTML = primaryActions(st).replace("btn primary", "btn primary lg") + extra.join("");
      }
    }
    const job = view.querySelector(".js-job");
    if (job) job.innerHTML = jobHtml(st) + errorHtml(st);
    const ub = view.querySelector(".js-updatebox");
    if (ub) {
      const u = state.updates[st.id];
      let html;
      if (!u) html = `<div class="u-text">Updates Not Checked Yet</div><button class="btn sm" data-act="checkone:${st.id}">${icon("refresh")}Check for Updates</button>`;
      else {
        const d = updateDesc(u);
        html = `<div class="u-text">${d.ok ? icon("check") + " " : ""}<b>${d.title}</b> · ${d.detail}</div>`
          + (d.update ? `<button class="btn sm primary" data-act="update:${st.id}">${icon("download")}Update Now</button>`
            : `<button class="btn sm" data-act="checkone:${st.id}">${icon("refresh")}${d.error ? "Retry" : "Check Again"}</button>`);
      }
      if (ub.dataset.k !== html) { ub.dataset.k = html; ub.innerHTML = html; $$("svg", ub).forEach((s) => { if (!s.closest(".btn")) { s.setAttribute("width", 14); s.setAttribute("height", 14); s.style.verticalAlign = "-2px"; s.style.color = "var(--ok)"; } }); }
    }
  }

  async function checkOne(id) {
    $$(`button[data-act='checkone:${id}']`).forEach((b) => { b.disabled = true; b.innerHTML = `<span class="spin"></span>Checking…`; });
    try {
      const u = state.updates[id] = await api(`/api/studios/${id}/updates?force=1`);
      const name = state.studios[id].name;
      if (u.status === "available") toast(`${name}: ${updateDesc(u).title}`, "info");
      else if (u.status === "up_to_date") toast(`${name} Is Up to Date`, "ok");
      else if (u.status === "error") toast(u.error, "err");
    } catch (err) { toast(err.message, "err"); }
    renderAll(); renderUpdates();
  }

  function toggleDrawer(open) {
    const d = $("#logDrawer");
    if (!d) return;
    const want = open == null ? !d.classList.contains("open") : open;
    d.classList.toggle("open", want);
    if (want && !studioPage.log) studioPage.log = attachLog(studioPage.id, $("#drawerHost"));
    if (want) setTimeout(() => studioPage.log && studioPage.log.term._fit(), 280);
  }

  // A studio uses its own self-signed certificate: the browser refuses to frame
  // it until the certificate is accepted once in a tab of its own.
  // A studio uses its own self-signed certificate. Browsers never show their
  // certificate warning inside a frame, so the warning opens in a small
  // pop-up; the launcher watches for the certificate to become trusted, closes
  // the pop-up itself and loads the studio here.
  async function reachable(url) {
    try { await fetch(url, { mode: "no-cors", cache: "no-store" }); return true; } catch (e) { return false; }
  }

  async function loadFrame(st) {
    const url = studioUrl(st);
    const frame = $("#studioFrame");
    const ok = await reachable(url);
    if (studioPage.id !== st.id || studioPage.mode !== "frame") return;
    if (ok) { frame.src = url; return; }
    const split = $(".studio-split");
    const ov = document.createElement("div");
    ov.className = "overlay-panel";
    ov.innerHTML = `<div class="panel trust-panel">
      <div style="display:flex;gap:12px;align-items:center"><div class="trust-icon">${icon("shield")}</div>
      <h3>One More Step: Trust ${esc(st.short)}</h3></div>
      <p>${esc(st.name)} uses its own self-signed certificate. Your browser asks about it once, and only in a window of its own.</p>
      <ol class="trust-steps">
        <li><b>Click Trust ${esc(st.short)}.</b> A small window opens with the browser's warning.</li>
        <li><b>Choose Advanced → Proceed</b> (Chrome: "Proceed to …"; Firefox: "Accept the Risk and Continue").</li>
        <li><b>That's it.</b> The window closes by itself and ${esc(st.short)} opens right here.</li>
      </ol>
      <div class="trust-status muted" aria-live="polite"></div>
      <div class="btn-row"><button class="btn primary" id="trustOpen">${icon("shield")}Trust ${esc(st.short)}</button><button class="btn ghost" data-act="popout:${st.id}">${icon("external")}Use a New Tab Instead</button></div>
    </div>`;
    $$("svg", ov).forEach((g) => { g.setAttribute("width", 18); g.setAttribute("height", 18); });
    split.appendChild(ov);
    const status = ov.querySelector(".trust-status");
    let timer = null, win = null;
    const stillHere = () => studioPage.id === st.id && studioPage.mode === "frame" && ov.isConnected;
    const done = () => { if (timer) clearInterval(timer); timer = null; };
    const trusted = () => {
      done();
      try { if (win && !win.closed) win.close(); } catch (e) { /* already gone */ }
      status.innerHTML = `${icon("check")} Certificate trusted. Opening ${esc(st.short)}…`;
      status.classList.remove("muted"); status.classList.add("ok");
      setTimeout(() => { if (studioPage.id === st.id && studioPage.mode === "frame") { ov.remove(); frame.src = url; } }, 600);
      toast(`${st.name} Trusted`, "ok");
    };
    $("#trustOpen").onclick = () => {
      const w = 860, h = 660;
      const left = Math.max(0, (screen.availWidth - w) / 2), top = Math.max(0, (screen.availHeight - h) / 2);
      win = window.open(url, `neat-trust-${st.id}`, `popup=yes,width=${w},height=${h},left=${left},top=${top}`);
      if (!win) win = window.open(url, "_blank");          // pop-ups blocked: a tab works the same way
      if (!win) { status.textContent = "Your browser blocked the window. Allow pop-ups for the launcher, or use a new tab."; return; }
      status.textContent = "Waiting for you to choose Advanced → Proceed in the small window…";
      done();
      const started = Date.now();
      timer = setInterval(async () => {
        if (!stillHere()) { done(); return; }
        if (await reachable(url)) { trusted(); return; }
        if (win.closed) { done(); status.textContent = "The window closed before the certificate was accepted. Click Trust to try again."; return; }
        if (Date.now() - started > 10 * 60 * 1000) { done(); status.textContent = "Still waiting? Click Trust to open the window again."; }
      }, 1000);
    };
  }

  // ------------------------------------------------------------------ manager
  function renderProcTable() {
    const rows = [];
    state.order.forEach((id) => {
      const st = state.studios[id];
      const up = ["running", "starting", "stopping"].includes(st.state);
      const first = st.procs && st.procs[0];
      const open = state.expanded.has(id);
      const acts = [];
      if (st.state === "not_installed") acts.push(`<button class="btn sm primary" data-act="install:${id}">${icon("download")}Install</button>`);
      if (!BUSY.has(st.state)) acts.push(`<button class="btn sm" data-act="branch:${id}" title="${st.installed ? "Switch Branch" : "Install From Branch"}" aria-label="${st.installed ? "Switch Branch" : "Install From Branch"}">${icon("branch")}</button>`);
      if (["installed", "error"].includes(st.state)) acts.push(`<button class="btn sm primary" data-act="launch:${id}">${icon("play")}Launch</button>`);
      if (up) acts.push(`<button class="btn sm" data-act="stop:${id}" ${st.state === "stopping" ? "disabled" : ""}>${icon("stop")}Stop</button>`,
        `<button class="btn sm danger" data-act="kill:${id}" title="Force kill (SIGKILL)">${icon("skull")}Kill</button>`,
        `<button class="btn sm" data-act="restart:${id}" title="Restart" aria-label="Restart">${icon("refresh")}</button>`);
      if (["installing", "updating", "uninstalling"].includes(st.state)) acts.push(`<button class="btn sm danger" data-act="cancel:${id}">${icon("x")}Cancel</button>`);
      acts.push(`<button class="btn sm" data-act="logs:${id}" title="Live Log" aria-label="Live Log">${icon("terminal")}</button>`);
      if (st.installed && !BUSY.has(st.state)) acts.push(`<button class="btn sm" data-act="update:${id}" title="Update" aria-label="Update">${icon("download")}</button>`,
        `<button class="btn sm danger" data-act="uninstall:${id}" title="Uninstall" aria-label="Uninstall">${icon("trash")}</button>`);
      rows.push(`<tr class="studio-row">
        <td class="c-exp">${st.procs.length ? `<button class="expander ${open ? "open" : ""}" data-act="expand:${id}" title="Processes">${icon("chevron")}</button>` : ""}</td>
        <td class="c-studio"><div class="row-studio"><img src="${S}${st.icon_small}" alt=""><div><a href="#/studio/${id}" title="Details, update, uninstall"><b>${esc(st.name)}</b></a><div class="muted mono path" title="${esc(st.dir)}">${esc(st.dir)}${st.installed ? ` · ${esc(st.branch)}` : ""}${st.owned ? "" : st.procs.length ? " · Started Outside the Launcher" : ""}</div></div></div></td>
        <td class="c-state" data-label="State"><span class="pill ${st.state}" ${st.app_error ? `title="${esc(st.app_error)}"` : ""}>${st.app_error ? `<i class="dot warn"></i>` : ""}${STATE_LABEL[st.state]}</span></td>
        <td class="num mono" data-label="PID">${first ? first.pid : "–"}${st.procs.length > 1 ? ` <span class="muted">+${st.procs.length - 1}</span>` : ""}</td>
        <td class="num" data-label="CPU">${up ? `${st.cpu}%` : "–"}</td>
        <td class="num" data-label="Memory">${up ? fmtBytes(st.rss) : "–"}</td>
        <td class="num" data-label="Uptime">${up ? fmtDur(st.uptime) : "–"}</td>
        <td class="num mono" data-label="Port"><a href="${esc(studioUrl(st))}" target="_blank" rel="noopener">${st.port}</a></td>
        <td class="c-actions"><div class="btn-row row-actions">${acts.join("")}</div></td>
      </tr>`);
      if (open) {
        st.procs.forEach((p) => rows.push(`<tr class="proc-row"><td></td>
          <td colspan="2"><div class="cmd mono" title="${esc(p.cmd)}">${esc(p.cmd)}</div></td>
          <td class="num mono">${p.pid}</td><td class="num">${p.cpu}%</td><td class="num">${fmtBytes(p.rss)}</td>
          <td class="num">${fmtDur(Date.now() / 1000 - p.started)}</td><td class="mono">${esc(p.status)}</td>
          <td><div class="btn-row" style="justify-content:flex-end"><button class="btn sm danger" data-act="killpid:${id}:${p.pid}">${icon("skull")}Kill PID</button></div></td></tr>`));
      }
    });
    $("#procTable").innerHTML = rows.join("");
  }

  // One description of an update check, shared by the Manager list and the studio page.
  const short = (sha) => (sha ? String(sha).slice(0, 7) : "");
  const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
  function updateDesc(u) {
    const inst = u.installed || {};
    const L = u.latest || {};
    const latest = L.sha ? `<span class="mono">${short(L.sha)}</span> ${esc(L.message || "")} <span class="muted">(${ago(L.date)})</span>` : "";
    const have = inst.sha ? `<span class="mono">${short(inst.sha)}</span>` : "";
    const where = [inst.branch ? `<span class="mono">${esc(inst.branch)}</span>` : "", inst.version ? `v${esc(inst.version)}` : ""].filter(Boolean).join(" · ");
    const how = u.method === "git" ? "Git Checkout" : u.method === "files" ? "Checked File by File" : "";
    const tail = [where, how, u.stale ? `<span title="${esc(u.note || "")}">Last Known: GitHub Unreachable</span>` : ""].filter(Boolean).join(" · ");
    switch (u.status) {
      case "up_to_date":
        return { pill: `<span class="pill running">Up to Date</span>`, title: "Up to Date",
          detail: `${have || latest}${have ? ` · ${esc(L.message || "")}` : ""}${tail ? ` · ${tail}` : ""}${inst.dirty ? " · Local Edits" : ""}`, update: false, ok: true };
      case "available": {
        const what = u.behind_by != null ? plural(u.behind_by, "Commit") + " Behind"
          : u.changed_count != null ? `Differs From Latest in ${plural(u.changed_count, "File")}` : "Update Available";
        const files = u.changed_files && u.changed_files.length ? ` title="${esc(u.changed_files.join("\n"))}"` : "";
        return { pill: `<span class="pill update"${files}>${what}</span>`, title: what,
          detail: `${have ? `${have} → ` : "Latest: "}${latest}${tail ? ` · ${tail}` : ""}`, update: true };
      }
      case "modified": {
        const files = u.changed_files && u.changed_files.length ? ` title="${esc(u.changed_files.join("\n"))}"` : "";
        return { pill: `<span class="pill"${files}>Local Changes</span>`, title: `Latest, With Local Changes in ${plural(u.changed_count, "File")}`,
          detail: `${have} · ${esc(L.message || "")}${tail ? ` · ${tail}` : ""}`, update: false };
      }
      case "ahead":
        return { pill: `<span class="pill">Ahead of GitHub</span>`, title: `${plural(u.ahead_by, "Local Commit")} Not on GitHub Yet`,
          detail: `${have} is ${plural(u.ahead_by, "commit")} ahead of ${latest}${tail ? ` · ${tail}` : ""}`, update: false };
      case "diverged":
        return { pill: `<span class="pill update">Diverged</span>`, title: `${u.behind_by} Behind, ${u.ahead_by} Ahead`,
          detail: `${have} vs ${latest}${tail ? ` · ${tail}` : ""}`, update: true };
      case "not_on_github":
        return { pill: `<span class="pill">Local Commit</span>`, title: "Installed Commit Isn't on GitHub",
          detail: `${have} · Latest: ${latest}${tail ? ` · ${tail}` : ""}`, update: false };
      case "not_installed":
        return { pill: `<span class="pill">Not Installed</span>`, title: "Not Installed", detail: `Latest: ${latest}`, update: false };
      default:
        return { pill: `<span class="pill error">Unknown</span>`, title: "Could Not Check", detail: esc(u.error || "Could Not Check for Updates"), update: false, error: true };
    }
  }

  function renderUpdates() {
    const list = $("#updatesList");
    if (!Object.keys(state.updates).length) return;
    const ids = [...state.order, "launcher"];
    list.innerHTML = ids.map((id) => {
      const u = state.updates[id];
      if (!u) return "";
      const st = state.studios[id];
      const name = st ? st.name : "Neat Studio Launcher";
      const img = st ? `${S}${st.icon}` : `${S}icons/neat-mark.png`;
      const d = updateDesc(u);
      const pill = d.pill, sub = d.detail;
      const btn = d.update ? `<button class="btn sm primary" data-act="update:${id}">${icon("download")}Update</button>` : "";
      return `<div class="update-row"><img src="${img}" width="26" height="26" alt=""><div class="u-main"><div class="u-name">${esc(name)}</div><div class="u-sub">${sub}</div></div>${pill}${btn}</div>`;
    }).join("");
  }

  // Console: one shell + one log view per studio, kept alive while switching tabs.
  const consoles = {};
  let currentConsole = null;
  let shellSock = null;
  let shellSeq = 0;

  function buildConsoleTabs() {
    const tabs = $("#consoleTabs");
    tabs.innerHTML = [`<button data-console="shell">${icon("terminal")}Shell</button>`,
      ...state.order.map((id) => `<button data-console="log:${id}"><img src="${S}${state.studios[id].icon_small}" width="14" height="14" alt="">${esc(state.studios[id].short)} Log</button>`)].join("");
    $$("svg", tabs).forEach((s) => { s.setAttribute("width", 14); s.setAttribute("height", 14); });
    tabs.onclick = (e) => { const b = e.target.closest("[data-console]"); if (b) selectConsole(b.dataset.console); };
  }

  function openShell(c) {
    if (shellSock) shellSock.disconnect();
    shellSock = io("/term", { transports: ["websocket", "polling"], forceNew: true });
    shellSeq = 0;
    c.term.reset();
    shellSock.on("connect", () => shellSock.emit("open", { cols: c.term.cols, rows: c.term.rows }));
    shellSock.on("output", (d) => c.term.write(d));
    shellSock.on("exit", () => c.term.write("\r\n\x1b[38;2;114;125;139m[shell exited: press Restart for a new one]\x1b[0m\r\n"));
  }

  function selectConsole(key, scroll) {
    currentConsole = key;
    $$("#consoleTabs button").forEach((b) => b.classList.toggle("active", b.dataset.console === key));
    Object.values(consoles).forEach((c) => { c.host.style.display = "none"; });
    let c = consoles[key];
    if (!c) {
      const host = document.createElement("div");
      host.style.height = "100%";
      $("#consoleHost").appendChild(host);
      if (key === "shell") {
        const term = makeTerm(host, { cursorBlink: true });
        c = { host, term };
        let pending = "", timer = null;
        term.onData((d) => {
          pending += d;
          if (!timer) timer = setTimeout(() => {
            if (shellSock) shellSock.emit("input", { s: shellSeq++, d: pending });
            pending = ""; timer = null;
          }, 8);
        });
        term.onResize(({ cols, rows }) => shellSock && shellSock.emit("resize", { cols, rows }));
        consoles[key] = c;
        setTimeout(() => { term._fit(); openShell(c); }, 60);
      } else {
        const id = key.slice(4);
        const lv = attachLog(id, host);
        c = { host, term: lv.term, lv };
        consoles[key] = c;
      }
    }
    c.host.style.display = "block";
    setTimeout(() => { c.term._fit(); if (key === "shell") c.term.focus(); }, 30);
    const actions = $("#consoleActions");
    if (key === "shell") {
      $("#consoleLabel").textContent = `Shell · ${state.system ? state.system.hostname : "Board"} (Runs as the Launcher's User)`;
      actions.innerHTML = `<button class="btn sm" id="shellRestart">${icon("refresh")}Restart</button>`;
      $("#shellRestart").onclick = () => openShell(consoles.shell);
    } else {
      const id = key.slice(4);
      $("#consoleLabel").textContent = `${state.studios[id].name} · Live Log`;
      actions.innerHTML = `<a class="btn sm" href="/api/studios/${id}/log">${icon("download")}Download</a><button class="btn sm" id="logClear">${icon("trash")}Clear</button>`;
      $("#logClear").onclick = async () => { await api(`/api/studios/${id}/log/clear`, { method: "POST" }); c.term.reset(); };
    }
    $$("svg", actions).forEach((s) => { s.setAttribute("width", 14); s.setAttribute("height", 14); });
    if (scroll) $("#consoleTabs").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // ------------------------------------------------------------------ router
  function route() {
    const h = location.hash || "#/";
    if (h === "#studios") { show("home"); setTimeout(() => $("#studios").scrollIntoView({ behavior: "smooth" }), 30); return; }
    const m = h.match(/^#\/studio\/([\w-]+)(?:\/(open|\d+))?/);
    if (m) {
      const view = m[2] === "open" ? "open" : "details";
      const same = state.route.page === "studio" && state.route.id === m[1] && state.route.view === view;
      state.route = { page: "studio", id: m[1], view, g: m[2] && m[2] !== "open" ? +m[2] : 0 };
      if (m[2] === "open") m[2] = undefined;
      show("studio");
      const box = same && document.querySelector(".gallery");
      const n = state.studios[m[1]] ? galleryOf(state.studios[m[1]]).length : 0;
      if (box && m[2] && n) showGallery(box, Math.min(+m[2], n - 1));
      else { renderStudioPage(true); if (!same) window.scrollTo(0, 0); }
      return;
    }
    if (h.startsWith("#/manager")) { state.route = { page: "manager" }; show("manager"); return; }
    state.route = { page: "home" }; show("home");
  }

  function show(page) {
    if (closeLightbox) closeLightbox();
    if (page !== "studio") disposeStudioPage();
    if (page !== "studio") $("#studioView").innerHTML = "";
    $$(".page").forEach((p) => p.classList.toggle("active", p.id === `page-${page}`));
    $$(".nav a").forEach((a) => a.classList.toggle("active", a.dataset.nav === page));
    document.body.classList.toggle("in-studio", page === "studio");
    if (page === "manager") {
      renderStats(); renderProcTable(); renderUpdates();
      if (Date.now() - lastUpdateCheck > 60 * 1000) checkUpdates(false);   // fresh results on every visit
      if (!currentConsole) selectConsole("shell"); else setTimeout(() => consoles[currentConsole] && consoles[currentConsole].term._fit(), 30);
    }
    if (page !== "studio") window.scrollTo(0, 0);
  }
  window.addEventListener("hashchange", route);
  $("#exploreBtn").addEventListener("click", (e) => { e.preventDefault(); $("#studios").scrollIntoView({ behavior: "smooth" }); });
  $("#checkUpdatesBtn").addEventListener("click", () => checkUpdates(true));
  $("#checkUpdatesBtn2").addEventListener("click", () => checkUpdates(true));
  $("#resetMlaBtn").addEventListener("click", async () => {
    const r = running();
    const ok = await modal({
      title: "Reset the MLA?",
      body: `<p>Restarts <span class="mono">simaai-appcomplex.service</span>, which releases every model loaded on the MLA.${r.length ? " Running studios will lose their models: stop them first." : ""}</p>`,
      actions: [{ label: "Cancel", value: false, cls: "ghost" }, { label: "Reset MLA", value: true, cls: "danger solid" }],
    });
    if (!ok) return;
    try { await api("/api/system/reset-mla", { method: "POST" }); toast("MLA reset", "ok"); }
    catch (e) { toast(e.message, "err"); }
  });

  function renderAll() {
    renderHeader();
    renderGrid();
    renderReel();
    if (state.route.page === "studio") renderStudioPage(false);
    if (state.route.page === "manager") { renderProcTable(); renderUpdates(); }
  }

  // ------------------------------------------------------------------ welcome
  function welcome() {
    let seen = false;
    try { seen = localStorage.getItem("neatLauncherWelcomed") === "1"; } catch (e) { /* ignore */ }
    if (seen) return;
    const back = document.createElement("div");
    back.className = "welcome-back";
    back.innerHTML = `<div class="welcome">
      <img class="w-mark" src="${S}icons/neat-mark.png" alt="">
      <div class="eyebrow">Welcome to SiMa.ai Modalix</div>
      <h1>Your Edge AI Studio Is Ready.</h1>
      <p>This launcher installs, runs and monitors the premier Neat studios on this board. Here's how it works:</p>
      <div class="steps">
        <div class="step"><div class="n">1</div><b>Pick a Studio</b><span>GenAI, Decisions, Change Detection or Zero-Shot Vision Detection.</span></div>
        <div class="step"><div class="n">2</div><b>Install Once</b><span>One Click. Models Download Straight to the Board.</span></div>
        <div class="step"><div class="n">3</div><b>Launch</b><span>It Opens Right Here, Running on the MLA.</span></div>
      </div>
      <button class="btn spectrum lg" id="welcomeGo">${icon("play")}Get Started</button>
    </div>`;
    document.body.appendChild(back);
    $("#welcomeGo").onclick = () => {
      try { localStorage.setItem("neatLauncherWelcomed", "1"); } catch (e) { /* ignore */ }
      back.style.transition = "opacity .3s"; back.style.opacity = "0";
      setTimeout(() => { back.remove(); location.hash = "#studios"; }, 300);
    };
  }

  // ------------------------------------------------------------------ boot
  async function boot() {
    const data = await api("/api/studios");
    data.studios.forEach((s) => { state.studios[s.id] = s; state.order.push(s.id); });
    buildConsoleTabs();
    renderAll();
    route();
    introDone.then(welcome);
    checkUpdates(false);
    setInterval(() => checkUpdates(false), 5 * 60 * 1000);
  }
  boot().catch((e) => toast(`Cannot reach the launcher: ${e.message}`, "err"));
})();
