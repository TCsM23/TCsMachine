// PALE unit terminal. Plain JS, no build step. Runs in the desktop window and in a browser.
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const params = new URLSearchParams(location.search);
  const store = {
    get(k, d) { try { const v = localStorage.getItem("pale." + k); return v === null ? d : JSON.parse(v); } catch (_) { return d; } },
    set(k, v) { try { localStorage.setItem("pale." + k, JSON.stringify(v)); } catch (_) {} },
  };
  let token = params.get("token") || "";
  try {
    if (token) sessionStorage.setItem("pale-token", token); else token = sessionStorage.getItem("pale-token") || "";
  } catch (_) {}
  if (params.has("token")) history.replaceState(null, "", location.pathname);

  let STATE = {}, DESKTOP = null;

  // ---------------------------------------------------------------- theme, sound, ambience
  const applyTheme = (t) => { document.documentElement.dataset.theme = t; $("#themeBtn").setAttribute("aria-pressed", t === "night"); };
  applyTheme(store.get("theme", "day"));
  $("#themeBtn").onclick = () => { const t = store.get("theme", "day") === "day" ? "night" : "day"; store.set("theme", t); applyTheme(t); };

  let audio = null;
  const soundOn = () => store.get("sound", true);
  $("#soundBtn").setAttribute("aria-pressed", soundOn());
  $("#soundBtn").onclick = () => { store.set("sound", !soundOn()); $("#soundBtn").setAttribute("aria-pressed", soundOn()); blip("ok"); };
  function blip(kind) {
    if (!soundOn()) return;
    try {
      audio = audio || new (window.AudioContext || window.webkitAudioContext)();
      const seq = { send: [[880, .04], [1320, .05]], ok: [[660, .05]], done: [[990, .06], [660, .06], [440, .09]], alert: [[520, .08], [0, .05], [520, .08]], err: [[180, .18]] }[kind] || [[660, .05]];
      let t = audio.currentTime;
      for (const [f, d] of seq) {
        if (f) {
          const o = audio.createOscillator(), g = audio.createGain();
          o.type = "square"; o.frequency.value = f;
          g.gain.setValueAtTime(.035, t); g.gain.exponentialRampToValueAtTime(.0001, t + d);
          o.connect(g).connect(audio.destination); o.start(t); o.stop(t + d);
        }
        t += d;
      }
    } catch (_) {}
  }

  const FLAVOR = [
    "The city is quiet. The sandbox is sealed.", "Dust settles on the towers. Systems nominal.",
    "Every action is recorded. Nothing is forgotten.", "No signal from the surface. Awaiting directive.",
    "The white streets are empty. The unit is not.",
  ];
  $("#flavor").textContent = FLAVOR[Math.floor(Math.random() * FLAVOR.length)];
  const dust = $(".dust");
  for (let i = 0; i < 18; i++) {
    const d = document.createElement("i");
    d.style.left = Math.random() * 100 + "vw";
    d.style.animationDuration = 18 + Math.random() * 30 + "s";
    d.style.animationDelay = -Math.random() * 40 + "s";
    d.style.opacity = (0.15 + Math.random() * 0.3).toFixed(2);
    dust.appendChild(d);
  }

  // ---------------------------------------------------------------- boot
  const bootLines = ["PALE OPERATOR SUPPORT HARNESS", "", "> mounting sandbox ............ OK", "> black box chain ............. VERIFYING",
    "> memory banks ................ OK", "> approval channel ............ OK", "> establishing link ........... "];
  const bootEl = $("#bootText");
  let bi = 0;
  const bootTimer = setInterval(() => { bootEl.textContent += bootLines[bi++] + "\n"; if (bi >= bootLines.length) clearInterval(bootTimer); }, 90);
  let booted = false;
  const endBoot = (msg) => {
    if (booted) return; booted = true;
    setTimeout(() => { bootEl.textContent += msg + "\n"; setTimeout(() => $("#boot").classList.add("gone"), 320); }, 700);
  };

  // ---------------------------------------------------------------- helpers
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  function md(src) {
    const parts = String(src).split(/```/);
    return parts.map((p, i) => {
      if (i % 2) return "<pre><code>" + esc(p.replace(/^[\w+-]*\n/, "")) + "</code></pre>";
      return esc(p)
        .replace(/^#{1,3} (.+)$/gm, "<b>$1</b>")
        .replace(/^[-*] (.+)$/gm, "■ $1")
        .replace(/`([^`]+)`/g, "<code>$1</code>")
        .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
        .split(/\n{2,}/).map((x) => x.trim() ? "<p>" + x.replace(/\n/g, "<br>") + "</p>" : "").join("");
    }).join("");
  }
  const diffHtml = (text) => esc(text).split("\n").map((l) =>
    l.startsWith("+") && !l.startsWith("+++") ? `<span class="add">${l}</span>` :
    l.startsWith("-") && !l.startsWith("---") ? `<span class="del">${l}</span>` :
    l.startsWith("@@") ? `<span class="hunk">${l}</span>` : l).join("\n");
  const money = (n, d = 4) => "$" + Number(n || 0).toFixed(d);
  const kb = (n) => n < 1024 ? n + " B" : n < 1048576 ? (n / 1024).toFixed(1) + " KB" : (n / 1048576).toFixed(1) + " MB";
  async function api(path, opts = {}) {
    const r = await fetch(path, { ...opts, headers: { "X-Pale-Token": token, "Content-Type": "application/json", ...(opts.headers || {}) } });
    const ct = r.headers.get("content-type") || "";
    const body = ct.includes("json") ? await r.json() : await r.text();
    if (!r.ok) throw new Error(body && body.detail ? body.detail : r.status + " " + body);
    return body;
  }
  const post = (p, b) => api(p, { method: "POST", body: JSON.stringify(b || {}) });
  const put = (p, b) => api(p, { method: "PUT", body: JSON.stringify(b) });
  function toast(title, text = "", cls = "", onclick) {
    const t = document.createElement("div");
    t.className = "toast " + cls;
    t.innerHTML = `<b>${esc(title)}</b>${esc(text).slice(0, 240)}`;
    t.onclick = () => { t.remove(); onclick && onclick(); };
    $("#toasts").appendChild(t);
    setTimeout(() => t.remove(), 7000);
  }
  function notify(title, body) {  // OS notification while the window is in the background
    try {
      if (document.hidden && "Notification" in window && Notification.permission === "granted") new Notification(title, { body: String(body || "").slice(0, 180), icon: "/static/icon.png", silent: true });
    } catch (_) {}
  }
  const askNotify = () => { try { if ("Notification" in window && Notification.permission === "default") Notification.requestPermission(); } catch (_) {} };
  let titleFlash = null;
  function flashTitle(msg) {
    if (!document.hidden || titleFlash) return;
    const orig = document.title; let on = false;
    titleFlash = setInterval(() => { document.title = (on = !on) ? msg : orig; }, 900);
    const stop = () => { clearInterval(titleFlash); titleFlash = null; document.title = orig; document.removeEventListener("visibilitychange", stop); };
    document.addEventListener("visibilitychange", stop);
  }

  // ---------------------------------------------------------------- transcript
  const log = $("#log");
  let streamEl = null, streamKind = null, streamText = "";
  const tools = {}, touched = new Map();
  const scroll = () => { log.scrollTop = log.scrollHeight; };
  function add(html, cls) {
    const el = document.createElement("div");
    el.className = cls; el.innerHTML = html;
    log.appendChild(el); scroll();
    return el;
  }
  function endStream() { if (streamEl) streamEl.classList.remove("cursor"); streamEl = null; streamKind = null; streamText = ""; }
  function stream(kind, delta) {
    if (streamKind !== kind) {
      endStream(); streamKind = kind;
      streamEl = kind === "text" ? add('<div class="who">UNIT</div><div class="body cursor"></div>', "row unit").querySelector(".body") : add("", "think cursor");
    }
    streamText += delta;
    if (kind === "text") streamEl.innerHTML = md(streamText); else streamEl.textContent = "∴ " + streamText;
    scroll();
  }
  let liveTurns = 0;
  function operator(text, atts = [], turn = null) {
    endStream();
    if (text.startsWith("[review unit findings")) return add(`<div class="who">REVIEW UNIT</div><div class="body">${md(text.replace(/^\[[^\]]+\]\n?/, ""))}</div>`, "row review");
    const th = atts.length ? `<div class="thumbs">${atts.map((a) => `<span>⧉ ${esc(a)}</span>`).join("")}</div>` : "";
    const t = turn === null ? liveTurns : turn;
    liveTurns = t + 1;
    add(`<div class="who">OPERATOR<button class="rewind" data-rewind="${t}" title="Rewind: start a new session from just before this directive. Files are not changed; use UNDO for that.">⟲ REWIND</button></div><div class="body">${esc(text)}${th}</div>`, "row op");
  }
  function toolCall(d) {
    endStream();
    tools[d.id] = add(`<span class="name">■ ${esc(d.tool)}</span><span class="arg">${esc(d.arg)}</span><span class="risk ${d.risk}">${d.risk}</span>`, "tool");
    if (["write_file", "edit_file", "delete_file"].includes(d.tool) && d.verdict !== "deny") {
      touched.set(d.arg, d.tool.split("_")[0]); renderTouched();
    }
  }
  function toolResult(d) {
    const el = tools[d.id] || add(`<span class="name">■ ${esc(d.tool)}</span>`, "tool");
    if (!d.ok) el.classList.add("err");
    const det = document.createElement("details");
    det.innerHTML = `<summary>${d.ok ? "result" : "error"} · ${d.output.split("\n").length} line(s)</summary><div class="out">${esc(d.output)}</div>`;
    el.appendChild(det); el.style.flexWrap = "wrap"; scroll();
  }
  const notice = (text, cls = "") => { endStream(); add(esc(text), "notice " + cls); };
  function renderRows(rows) {
    log.innerHTML = ""; liveTurns = 0;
    for (const r of rows) {
      if (r.kind === "text" && r.who === "user") { if (r.text.startsWith("Attached files (in the sandbox):")) continue; operator(r.text, [], r.turn ?? null); }
      else if (r.kind === "text") add('<div class="who">UNIT</div><div class="body">' + md(r.text) + "</div>", "row unit");
      else if (r.kind === "tool_call") add(`<span class="name">■ ${esc(r.tool)}</span><span class="arg">${esc(r.text)}</span>`, "tool");
      else if (r.kind === "tool_result") add(`<details><summary>${r.ok ? "result" : "error"}</summary><div class="out">${esc(r.text)}</div></details>`, "tool" + (r.ok ? "" : " err"));
    }
    endStream();
    if (!rows.length) add(`<div class="who">UNIT</div><div class="body"><p>Unit online. Awaiting directive.</p></div>`, "row unit");
  }
  function renderPlan(items) {
    $("#plan").innerHTML = items && items.length ? items.map((i) => `<li class="${i.status}">${esc(i.text)}</li>`).join("") : '<li class="dim">no plan filed</li>';
  }
  function renderTouched() {
    $("#touched").innerHTML = touched.size ? [...touched].map(([p, k]) => `<li data-open="${esc(p)}"><b>${k.toUpperCase()}</b>${esc(p)}</li>`).join("") : '<li class="dim">nothing changed yet</li>';
  }
  const jobs = new Map();
  function renderJobs() {
    const rows = [...jobs.values()].sort((a, b) => b.started - a.started).slice(0, 8);
    $("#jobsPanel").hidden = !rows.length;
    $("#jobs").innerHTML = rows.map((j) => `<li data-job="${esc(j.id)}" class="${j.running ? "run" : j.code === 0 ? "ok" : "bad"}"><b>${j.running ? "▶" : j.code === 0 ? "■" : "✕"} ${esc(j.id)}</b><span>${esc(j.command)}</span><i>${j.running ? "running" : (j.stopped || "exit " + j.code)}</i></li>`).join("");
  }
  function onJob(j) {
    const prev = jobs.get(j.id);
    jobs.set(j.id, j); renderJobs();
    if (prev && prev.running && !j.running) { toast("JOB " + j.id + (j.code === 0 ? " FINISHED" : " ENDED · " + (j.stopped || "exit " + j.code)), j.command, j.code === 0 ? "" : "err", () => openJob(j.id)); notify("PALE · job " + j.id, j.command); }
    if (!jobModalId || jobModalId !== j.id) return;
    openJob(j.id);
  }
  let jobModalId = "";
  async function openJob(id) {
    jobModalId = id;
    const j = await api("/api/jobs/" + id);
    $("#jobTitle").textContent = j.id; $("#jobState").textContent = `· ${j.running ? "running" : (j.stopped || "exit " + j.code)} · ${j.seconds}s`;
    $("#jobOut").textContent = "$ " + j.command + "\n\n" + (j.output || "(no output yet)");
    $("#jobStop").disabled = !j.running; $("#jobModal").hidden = false;
    $("#jobOut").scrollTop = $("#jobOut").scrollHeight;
  }
  $("#jobs").onclick = (e) => { const li = e.target.closest("[data-job]"); if (li) openJob(li.dataset.job).catch((err) => toast("ERROR", err.message, "err")); };
  $("#jobRefresh").onclick = () => openJob(jobModalId);
  $("#jobStop").onclick = async () => { onJob(await post(`/api/jobs/${jobModalId}/stop`)); };
  $("#jobClose").onclick = () => { $("#jobModal").hidden = true; jobModalId = ""; };
  api("/api/jobs").then((rows) => { rows.forEach((j) => jobs.set(j.id, j)); renderJobs(); }).catch(() => {});
  $("#touched").onclick = (e) => { const li = e.target.closest("[data-open]"); if (li) { showTab("files"); openFile(li.dataset.open); } };

  // ---------------------------------------------------------------- attachments
  let attachments = [];
  function renderAttachments() {
    $("#attachments").innerHTML = attachments.map((a, i) =>
      `<span class="att">${a.thumb ? `<img src="${a.thumb}" alt="">` : "⧉"} ${esc(a.path)} <button type="button" data-rm="${i}" title="remove">✕</button></span>`).join("");
  }
  $("#attachments").onclick = (e) => { if (e.target.dataset.rm) { attachments.splice(+e.target.dataset.rm, 1); renderAttachments(); } };
  async function uploadFiles(files) {
    for (const f of files) {
      if (f.size > 20e6) { toast("TOO LARGE", f.name + " is over 20 MB", "err"); continue; }
      const data = await new Promise((res) => { const r = new FileReader(); r.onload = () => res(String(r.result).split(",")[1] || ""); r.readAsDataURL(f); });
      try {
        const out = await post("/api/upload", { name: f.name, data });
        attachments.push({ path: out.path, thumb: f.type.startsWith("image/") ? URL.createObjectURL(f) : "" });
        renderAttachments();
      } catch (err) { toast("UPLOAD FAILED", err.message, "err"); }
    }
  }
  $("#attachBtn").onclick = () => $("#fileInput").click();
  $("#fileInput").onchange = (e) => { uploadFiles([...e.target.files]); e.target.value = ""; };
  ["dragenter", "dragover"].forEach((ev) => log.parentElement.addEventListener(ev, (e) => { e.preventDefault(); log.classList.add("drop"); }));
  ["dragleave", "drop"].forEach((ev) => log.parentElement.addEventListener(ev, (e) => { e.preventDefault(); log.classList.remove("drop"); }));
  log.parentElement.addEventListener("drop", (e) => uploadFiles([...e.dataTransfer.files]));
  $("#text").addEventListener("paste", (e) => { const fs = [...(e.clipboardData?.files || [])]; if (fs.length) { e.preventDefault(); uploadFiles(fs); } });

  // ---------------------------------------------------------------- socket
  let ws, sessionId = params.get("session") || store.get("session", ""), busy = false;
  const setBusy = (b, msg) => { busy = b; $("#statusLine").textContent = msg || (b ? "unit active" : "standing by"); $("#sendBtn").disabled = b; };
  function connect(session) {
    if (ws) { ws.onclose = null; ws.close(); }
    touched.clear(); renderTouched();
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(token)}&session=${encodeURIComponent(session || "")}`);
    ws.onmessage = (e) => handle(JSON.parse(e.data));
    ws.onclose = (e) => {
      if (e.code === 4401) { endBoot("> establishing link ........... REFUSED"); notice("link refused: bad token. Restart the app, or open the URL printed by `pale serve`.", "err"); return; }
      notice("link lost. reconnecting…", "warn");
      setTimeout(() => connect(sessionId), 2000);
    };
  }
  const send = (m) => ws && ws.readyState === 1 && ws.send(JSON.stringify(m));
  function handle(m) {
    switch (m.type) {
      case "hello":
        sessionId = m.session; store.set("session", m.session);
        $("#sessionTitle").textContent = m.title ? "· " + m.title.slice(0, 48) : "";
        $("#mode").value = m.mode; setModel(m.model); setDry(m.dry_run);
        renderRows(m.rows); renderPlan(m.todos);
        $("#ctx").textContent = "—"; $("#ctxMeter i").style.width = "0";
        $("#cost").textContent = money(m.usd);
        $("#toolCount").textContent = m.tools.length + " tools online";
        setBusy(false);
        endBoot("> establishing link ........... OK");
        return;
      case "mode": $("#mode").value = m.mode; notice("mode → " + m.mode.toUpperCase(), "warn"); return;
      case "model": setModel(m.model); notice("model → " + m.model, "warn"); return;
      case "dry": setDry(m.on); return;
      case "approval": return showApproval(m);
      case "question": blip("alert"); flashTitle("■ UNIT REQUESTS INPUT"); notify("PALE · unit requests input", m.text); return showQuestion(m);
      case "update": return onUpdate(m.state);
      case "scheduled": toast("FIELD REPORT · " + m.name, m.report, "", () => showTab("schedule")); blip("done"); return;
      case "workspace": refreshState(); connect(""); toast("SECTOR CHANGED", m.path); jobs.clear(); renderJobs(); return;
      case "job": return onJob(m.job);
    }
    if (m.type !== "event") return;
    const d = m.data;
    switch (m.kind) {
      case "text": case "thinking": stream(m.kind, d.delta); break;
      case "status":
        if (d.message === "thinking") { setBusy(true, "unit active · step " + d.step); }
        else notice("» " + d.message);
        break;
      case "tool_call": toolCall(d); break;
      case "tool_result": toolResult(d); break;
      case "sub_tool_call": notice(`· ${d.unit || "support"} unit → ${d.tool} ${d.arg}`); break;
      case "sub_denied": notice(`✕ ${d.unit || "support"} unit denied: ${d.tool} · ${d.reason}`, "deny"); break;
      case "mode": $("#mode").value = d.mode; notice("mode → " + d.mode.toUpperCase(), "warn"); break;
      case "job": onJob(d); break;
      case "review":
        endStream();
        add(`<div class="who">REVIEW UNIT</div><div class="body">${d.clean ? "<p>■ no findings. The diff checks out.</p>" : md(d.text)}</div>`, "row review" + (d.clean ? " clean" : ""));
        if (!d.clean) toast("REVIEW FINDINGS", "the review unit flagged the last change. It will see them with your next directive.", "err");
        break;
      case "denied": notice("✕ denied: " + d.tool + " · " + d.reason, "deny"); break;
      case "warning": if (!String(d.message).startsWith("malformed")) notice("⚠ " + d.message, "warn"); break;
      case "error": notice("✕ " + d.message, "err"); blip("err"); setBusy(false); break;
      case "todo": renderPlan(d.items); break;
      case "cost":
        $("#cost").textContent = money(d.usd); $("#today").textContent = money(d.today, 2);
        $("#tokens").textContent = `${d.input_tokens.toLocaleString()} in · ${d.output_tokens.toLocaleString()} out · ${d.cache_read.toLocaleString()} cached`;
        if (d.context) setCtx(d.context);
        break;
      case "done":
        endStream();
        add(`── REPORT FILED · ${d.steps} STEP(S) · ${money(d.usd)} SESSION`, "report-end");
        setBusy(false); blip("done"); flashTitle("■ REPORT FILED"); notify("PALE · report filed", d.text);
        break;
    }
  }

  // ---------------------------------------------------------------- approvals & questions
  const pendingApprovals = [];
  function showApproval(m) {
    pendingApprovals.push(m); blip("alert"); flashTitle(m.tool === "submit_plan" ? "■ PLAN READY FOR REVIEW" : "■ AUTHORIZATION REQUIRED");
    notify(m.tool === "submit_plan" ? "PALE · plan ready for review" : "PALE · authorization required", m.summary);
    if (pendingApprovals.length === 1) openApproval();
  }
  function openApproval() {
    const m = pendingApprovals[0];
    if (!m) { $("#approval").hidden = true; return; }
    $("#apRisk").className = "risk " + m.risk; $("#apRisk").textContent = m.risk;
    $("#apSummary").textContent = m.summary;
    $("#apReasons").textContent = m.tool === "submit_plan" ? "" : (m.reasons || []).filter(Boolean).join(" · ");
    $("#approval .keys").textContent = m.tool === "submit_plan" ? "Y approve · N / Esc send back" : "Y authorize · A always · N / Esc deny";
    const plan = m.tool === "submit_plan";
    $("#apHead").textContent = plan ? "PLAN REVIEW" : "AUTHORIZATION REQUIRED";
    $("#apRisk").hidden = plan;
    $("#apSummary").textContent = plan ? "The unit has investigated and filed a plan. Nothing changes until you approve it." : m.summary;
    $("#apPreview").hidden = plan; $("#apPlan").hidden = !plan;
    if (plan) $("#apPlan").innerHTML = md(m.preview || "");
    else $("#apPreview").innerHTML = m.preview ? diffHtml(m.preview) : "(no preview)";
    $("#apYes").textContent = plan ? "APPROVE PLAN" : "AUTHORIZE";
    $("#apNo").textContent = plan ? "SEND BACK" : "DENY";
    $("#apAlways").hidden = plan;
    $("#apNote").placeholder = plan ? "what to change in the plan (sent if you send it back)" : "optional message to the unit (sent if you deny)";
    $("#apNote").value = ""; $("#apAlways").disabled = m.risk === "high" || plan;
    $("#approval").hidden = false; $("#apYes").focus();
  }
  function answerApproval(approved, always) {
    const m = pendingApprovals.shift();
    if (!m) return;
    send({ type: "approval", id: m.id, approved, always, note: $("#apNote").value });
    blip(approved ? "ok" : "err"); openApproval();
  }
  $("#apYes").onclick = () => answerApproval(true, false);
  $("#apAlways").onclick = () => answerApproval(true, true);
  $("#apNo").onclick = () => answerApproval(false, false);
  function showQuestion(m) { $("#qText").textContent = m.text; $("#qAnswer").value = ""; $("#question").hidden = false; $("#qAnswer").focus(); }
  $("#qSend").onclick = () => { send({ type: "answer", text: $("#qAnswer").value }); $("#question").hidden = true; };

  // ---------------------------------------------------------------- directive controls
  function transmit(text) {
    text = (text ?? $("#text").value).trim();
    if (!text) return;
    if (busy) { toast("UNIT BUSY", "HALT first, or wait for the report."); return; }
    askNotify();
    const atts = attachments.map((a) => a.path);
    operator(text, atts);
    send({ type: "directive", text, attachments: atts });
    $("#text").value = ""; attachments = []; renderAttachments();
    setBusy(true); blip("send");
    if (!$("#sessionTitle").textContent) $("#sessionTitle").textContent = "· " + text.slice(0, 48);
  }
  $("#form").onsubmit = (e) => { e.preventDefault(); transmit(); };
  $("#text").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); transmit(); } });
  const halt = () => { send({ type: "interrupt" }); notice("⚠ halt signal sent", "warn"); };
  $("#halt").onclick = halt;
  $("#undo").onclick = () => send({ type: "undo" });
  $("#mode").onchange = (e) => send({ type: "mode", mode: e.target.value });
  $("#modelSel").onchange = (e) => send({ type: "model", model: e.target.value });
  function setModel(m) {
    const sel = $("#modelSel");
    if (![...sel.options].some((o) => o.value === m)) sel.add(new Option(m.toUpperCase(), m));
    sel.value = m; $("#model").textContent = m;
  }
  function setDry(on) { $("#dry").setAttribute("aria-pressed", on ? "true" : "false"); $("#dry b").textContent = on ? "ON" : "OFF"; }
  function setCtx(n) {
    const win = /haiku/.test($("#modelSel").value) ? 200000 : 1000000;  // approximate window, for the meter only
    $("#ctx").textContent = (n >= 1000 ? (n / 1000).toFixed(1) + "k" : n) + " tokens";
    const pct = Math.min(100, (n / win) * 100);
    $("#ctxMeter i").style.width = pct.toFixed(1) + "%";
    $("#ctxMeter").classList.toggle("hot", pct > 70);
    $("#ctxMeter").title = `last request: ${n.toLocaleString()} input tokens. Long sessions are compacted on the server.`;
  }
  $("#dry").onclick = () => send({ type: "dry", on: $("#dry").getAttribute("aria-pressed") !== "true" });

  let templates = [];
  async function loadTemplates() {
    templates = await api("/api/templates").catch(() => []);
    $("#templates").innerHTML = '<option value="">QUICK DIRECTIVES…</option>' + templates.map((t, i) => `<option value="${i}">${esc(t.name)}</option>`).join("") + '<option value="save">+ save current text as quick directive</option>';
  }
  $("#templates").onchange = async (e) => {
    const v = e.target.value; e.target.value = "";
    if (v === "save") {
      const text = $("#text").value.trim();
      if (!text) return toast("NOTHING TO SAVE", "type a directive first");
      const name = prompt("Name for this quick directive:", text.slice(0, 30));
      if (!name) return;
      templates.push({ name, text }); await put("/api/templates", templates); loadTemplates(); return;
    }
    if (v !== "") { $("#text").value = templates[+v].text; $("#text").focus(); }
  };

  // ---------------------------------------------------------------- tabs
  const loaders = {};
  function showTab(name) {
    $$(".menu button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
    $$(".tab").forEach((t) => t.classList.toggle("active", t.id === "tab-" + name));
    if (loaders[name]) loaders[name]().catch((e) => toast("ERROR", e.message, "err"));
  }
  $("#menu").onclick = (e) => { const b = e.target.closest("button[data-tab]"); if (b) showTab(b.dataset.tab); };

  // files
  let cwd = ".", viewing = "";
  loaders.files = async () => {
    const r = await api("/api/files?path=" + encodeURIComponent(cwd));
    const parts = r.path === "." ? [] : r.path.split("/");
    $("#crumbs").innerHTML = `<a data-cd=".">sandbox</a>` + parts.map((p, i) => ` / <a data-cd="${esc(parts.slice(0, i + 1).join("/"))}">${esc(p)}</a>`).join("");
    $("#fileList").innerHTML = (r.path !== "." ? `<div class="fitem" data-cd="${esc(parts.slice(0, -1).join("/") || ".")}">↰ ..</div>` : "") +
      r.entries.map((f) => `<div class="fitem ${f.path === viewing ? "sel" : ""}" ${f.dir ? `data-cd="${esc(f.path)}"` : `data-file="${esc(f.path)}"`}>${f.dir ? "▸" : "·"} ${esc(f.name)}${f.dir ? "/" : ""}<span class="sz">${f.dir ? "" : kb(f.size)}</span></div>`).join("") ||
      '<div class="item dim">empty. Drop files into the directive box, or open the folder.</div>';
  };
  async function openFile(path) {
    viewing = path;
    $("#viewerName").textContent = path;
    $("#attachViewed").disabled = false;
    try {
      const f = await api("/api/file?path=" + encodeURIComponent(path));
      $("#viewer").innerHTML = f.binary ? `<span class="dim">binary file · ${kb(f.size)}</span>` :
        f.text.split("\n").map((l, i) => `<span class="ln">${i + 1}</span>${esc(l)}`).join("\n");
    } catch (e) { $("#viewer").innerHTML = `<span class="bad">${esc(e.message)}</span>`; }
    $$("#fileList .fitem").forEach((el) => el.classList.toggle("sel", el.dataset.file === path));
  }
  $("#tab-files").addEventListener("click", (e) => {
    const t = e.target.closest("[data-cd],[data-file]");
    if (!t) return;
    if (t.dataset.cd !== undefined) { cwd = t.dataset.cd; loaders.files(); } else openFile(t.dataset.file);
  });
  $("#refreshFiles").onclick = () => loaders.files();
  $("#attachViewed").onclick = () => { if (viewing && !attachments.some((a) => a.path === viewing)) attachments.push({ path: viewing }); renderAttachments(); showTab("directive"); $("#text").focus(); };
  $("#openSandbox").onclick = () => post("/api/desktop/open", { target: "sandbox" });

  // archive
  let sessionsCache = [];
  let searchHits = null, searchTimer = null;
  const renderSessions = () => {
    if (searchHits) {
      $("#sessions").innerHTML = searchHits.map((r) => `
        <div class="item"><div class="t">${esc(r.title || "untitled")}${r.id === sessionId ? ' <span class="tag">ACTIVE</span>' : ""}</div>
        <div class="d snippet">${esc(r.who === "user" ? "operator: " : r.who === "assistant" ? "unit: " : "")}${highlight(r.snippet, $("#archiveFilter").value)}</div>
        <div class="acts"><button class="btn small" data-resume="${esc(r.id)}">RESUME</button><button class="btn small" data-export="${esc(r.id)}">EXPORT</button></div></div>`).join("") || '<div class="item dim">no session mentions that</div>';
      return;
    }
    const rows = sessionsCache;
    $("#sessions").innerHTML = rows.map((r) => `
      <div class="item"><div class="t">${esc(r.title || "untitled")}${r.id === sessionId ? ' <span class="tag">ACTIVE</span>' : ""}</div>
      <div class="d">${esc(r.id)} · ${esc(r.surface)} · ${r.turns} msgs · ${money(r.usd)} · ${new Date(r.updated * 1000).toLocaleString()}</div>
      <div class="acts"><button class="btn small" data-resume="${esc(r.id)}">RESUME</button><button class="btn small" data-rename="${esc(r.id)}">RENAME</button><button class="btn small" data-export="${esc(r.id)}">EXPORT</button><button class="btn small danger" data-delsess="${esc(r.id)}">DELETE</button></div></div>`).join("") || '<div class="item dim">archive empty</div>';
  };
  const highlight = (text, q) => { const e = esc(text), qq = esc(q.trim()); if (!qq) return e; const i = e.toLowerCase().indexOf(qq.toLowerCase()); return i < 0 ? e : e.slice(0, i) + "<mark>" + e.slice(i, i + qq.length) + "</mark>" + e.slice(i + qq.length); };
  loaders.archive = async () => { sessionsCache = await api("/api/sessions"); renderSessions(); };
  $("#archiveFilter").oninput = () => {
    clearTimeout(searchTimer);
    const q = $("#archiveFilter").value.trim();
    if (q.length < 2) { searchHits = null; renderSessions(); return; }
    searchTimer = setTimeout(async () => { searchHits = await api("/api/sessions/search?q=" + encodeURIComponent(q)).catch(() => []); renderSessions(); }, 220);
  };

  // memory
  loaders.memory = async () => {
    const rows = await api("/api/memory");
    $("#memory").innerHTML = rows.map((n) => `
      <div class="item"><div class="t">${esc(n.name)}</div><div class="d">${esc(n.description)}</div>
      <div class="acts"><button class="btn small" data-editmem="${esc(n.name)}">EDIT</button><button class="btn small danger" data-forget="${esc(n.name)}">FORGET</button></div>
      <pre>${esc(n.body)}</pre></div>`).join("") || '<div class="item dim">memory banks empty. The unit stores durable facts here; you can too.</div>';
    loaders.memory.rows = rows;
  };
  $("#newMemory").onclick = () => { $("#memForm").hidden = false; $("#memName").value = $("#memDesc").value = $("#memBody").value = ""; $("#memName").focus(); };
  $("#memCancel").onclick = () => { $("#memForm").hidden = true; };
  $("#memForm").onsubmit = async (e) => {
    e.preventDefault();
    await put("/api/memory/" + encodeURIComponent($("#memName").value), { description: $("#memDesc").value, body: $("#memBody").value });
    $("#memForm").hidden = true; loaders.memory();
  };

  // skills
  loaders.skills = async () => {
    const rows = await api("/api/skills");
    $("#skills").innerHTML = rows.map((s) => `
      <div class="item"><div class="t">${esc(s.name)} ${s.quarantined ? '<span class="tag">QUARANTINED</span>' : ""}</div>
      <div class="d">${esc(s.description)}</div>
      <div class="acts">${s.quarantined ? `<button class="btn small" data-approve="${esc(s.name)}">AUTHORIZE</button><button class="btn small danger" data-reject="${esc(s.name)}">REJECT</button>` : `<button class="btn small danger" data-delskill="${esc(s.name)}">DELETE</button>`}</div>
      <pre>${esc(s.body)}</pre></div>`).join("") || '<div class="item dim">no skills yet. The unit proposes them after work it would repeat.</div>';
    const units = await api("/api/units");
    loaders.skills.units = units;
    $("#units").innerHTML = units.map((u) => `
      <div class="item"><div class="t">${esc(u.name)} <span class="tag">${esc(u.model || "support model")}</span></div>
      <div class="d">${esc(u.description)} · tools: ${esc(u.tools.length ? u.tools.join(", ") : "read-only set")} · ${u.max_steps} steps</div>
      <div class="acts"><button class="btn small" data-editunit="${esc(u.name)}">EDIT</button><button class="btn small danger" data-delunit="${esc(u.name)}">DELETE</button></div>
      <pre>${esc(u.body)}</pre></div>`).join("") || '<div class="item dim">no specialist units. Add one, or drop a Markdown file into units/.</div>';
  };
  const unitForm = (u = {}) => {
    $("#unitForm").hidden = false;
    $("#unitName2").value = u.name || ""; $("#unitDesc").value = u.description || ""; $("#unitModel").value = u.model || "";
    $("#unitSteps").value = u.max_steps || ""; $("#unitTools").value = (u.tools || []).join(", "); $("#unitBody").value = u.body || "";
    $("#unitName2").focus();
  };
  $("#newUnit").onclick = () => unitForm();
  $("#unitCancel").onclick = () => { $("#unitForm").hidden = true; };
  $("#unitForm").onsubmit = async (e) => {
    e.preventDefault();
    try {
      await put("/api/units/" + encodeURIComponent($("#unitName2").value.trim()), { description: $("#unitDesc").value, model: $("#unitModel").value.trim(),
        max_steps: $("#unitSteps").value, tools: $("#unitTools").value.trim(), body: $("#unitBody").value });
      $("#unitForm").hidden = true; loaders.skills();
    } catch (err) { toast("CAN'T SAVE UNIT", err.message, "err"); }
  };

  // schedule
  loaders.schedule = async () => {
    const [rows, reps] = await Promise.all([api("/api/schedule"), api("/api/reports")]);
    $("#schedule").innerHTML = rows.map((t) => `
      <div class="item"><div class="t">${esc(t.name)}</div>
      <div class="d">${esc(t.spec)} · next ${esc(t.next || "—")} · last ${esc(t.last || "never")}${t.enabled ? "" : " · finished"}</div>
      <div class="acts"><button class="btn small danger" data-unschedule="${esc(t.id)}">REMOVE</button></div></div>`).join("") || '<div class="item dim">no scheduled directives</div>';
    $("#reports").innerHTML = reps.map((r) => `<div class="item"><div class="t">${esc(r.name)}</div><pre>${esc(r.text)}</pre></div>`).join("") || '<div class="item dim">no reports yet</div>';
  };
  $("#schedForm").onsubmit = async (e) => {
    e.preventDefault();
    try { await post("/api/schedule", { spec: $("#schedSpec").value, directive: $("#schedText").value }); $("#schedSpec").value = $("#schedText").value = ""; loaders.schedule(); }
    catch (err) { toast("CAN'T SCHEDULE", err.message, "err"); }
  };
  $("#openReports").onclick = () => post("/api/desktop/open", { target: "reports" });

  // ledger
  loaders.ledger = async () => {
    const r = await api("/api/ledger");
    const days = [];
    for (let i = 29; i >= 0; i--) { const d = new Date(Date.now() - i * 864e5); days.push(d.toISOString().slice(0, 10)); }
    const map = Object.fromEntries(r.days);
    const vals = days.map((d) => map[d] || 0);
    const total = vals.reduce((a, b) => a + b, 0);
    $("#ledgerTotal").textContent = `30-day total ${money(total, 2)} · today ${money(vals[29], 2)} · cap ${money(r.cap, 2)}/day`;
    const W = 900, H = 260, pad = 34, max = Math.max(r.cap, ...vals, 0.01), bw = (W - pad * 2) / 30;
    const y = (v) => H - pad - (v / max) * (H - pad * 2);
    let svg = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="daily spend">`;
    for (let g = 0; g <= 4; g++) { const v = (max / 4) * g; svg += `<text x="2" y="${y(v) + 3}">${money(v, 2)}</text><line x1="${pad}" x2="${W - 6}" y1="${y(v)}" y2="${y(v)}" stroke="var(--paper-3)"/>`; }
    vals.forEach((v, i) => {
      const x = pad + i * bw + 2;
      svg += `<rect class="bar" x="${x}" y="${y(v)}" width="${bw - 4}" height="${Math.max(0, H - pad - y(v))}"><title>${days[i]}: ${money(v)}</title></rect>`;
      if (i % 5 === 0 || i === 29) svg += `<text x="${x}" y="${H - 14}">${days[i].slice(5)}</text>`;
    });
    svg += `<line class="cap" x1="${pad}" x2="${W - 6}" y1="${y(r.cap)}" y2="${y(r.cap)}"/><text x="${W - 70}" y="${y(r.cap) - 4}" fill="var(--rust)">DAILY CAP</text></svg>`;
    $("#chart").innerHTML = svg;
  };

  // black box
  loaders.blackbox = async () => {
    const r = await api("/api/audit?n=300");
    const k = $("#bbKind").value;
    $("#chain").innerHTML = `<span class="${r.ok ? "ok" : "bad"}">${r.ok ? "■ CHAIN INTACT" : "✕ " + esc(r.message)}</span> <span class="dim">${r.count} entries</span>`;
    $("#audit").innerHTML = r.entries.reverse().filter((e) => !k || e.kind === k).map((e) => `
      <div class="audit-row"><span class="dim">${new Date(e.ts * 1000).toLocaleString()}</span><span class="dim">${esc((e.session || "").slice(-6))}</span>
      <span class="k">${esc(e.kind)}</span><span>${esc(JSON.stringify(e.data)).slice(0, 260)}</span></div>`).join("");
  };
  $("#bbKind").onchange = () => loaders.blackbox();

  // settings
  const form = $("#settingsForm");
  loaders.settings = async () => {
    const s = await api("/api/settings");
    for (const [k, v] of Object.entries(s)) {
      const el = form.elements[k];
      if (!el) continue;
      if (el.type === "checkbox") el.checked = !!v;
      else if (Array.isArray(v)) el.value = v.join("\n");
      else el.value = v;
    }
    if (DESKTOP) await loadDesktopSettings();
  };
  form.onsubmit = async (e) => {
    e.preventDefault();
    const body = {};
    for (const el of form.elements) {
      if (!el.name) continue;
      if (el.type === "checkbox") body[el.name] = el.checked;
      else if (el.tagName === "TEXTAREA") body[el.name] = el.value.split("\n").map((x) => x.trim()).filter(Boolean);
      else if (el.type === "number") body[el.name] = Number(el.value);
      else body[el.name] = el.value;
    }
    try { await put("/api/settings", body); $("#settingsMsg").textContent = "saved. New sessions use these settings."; refreshState(); blip("ok"); }
    catch (err) { $("#settingsMsg").textContent = "✕ " + err.message; }
  };

  // shared list actions
  document.body.addEventListener("click", async (e) => {
    const d = e.target.dataset || {};
    try {
      if (d.resume) { connect(d.resume); showTab("directive"); }
      if (d.rewind !== undefined) {
        if (busy) return toast("UNIT BUSY", "HALT first, then rewind.");
        const r = await post(`/api/sessions/${sessionId}/fork`, { turn: +d.rewind });
        connect(r.id); $("#text").value = r.directive; $("#text").focus();
        toast("REWOUND", "new session from before that directive. The original is in the archive. Files were not changed.");
      }
      if (d.editunit) unitForm(loaders.skills.units.find((u) => u.name === d.editunit));
      if (d.delunit && confirm("Delete specialist unit " + d.delunit + "?")) { await api("/api/units/" + encodeURIComponent(d.delunit), { method: "DELETE" }); loaders.skills(); }
      if (d.rename) { const t = prompt("New title:"); if (t) { await api("/api/sessions/" + d.rename, { method: "PATCH", body: JSON.stringify({ title: t }) }); loaders.archive(); } }
      if (d.export) {
        const text = await api("/api/sessions/" + d.export + "/export");
        const a = document.createElement("a");
        a.href = URL.createObjectURL(new Blob([text], { type: "text/markdown" })); a.download = `pale-${d.export}.md`; a.click();
      }
      if (d.delsess && confirm("Delete this session permanently?")) { await api("/api/sessions/" + d.delsess, { method: "DELETE" }); loaders.archive(); }
      if (d.forget && confirm("Forget " + d.forget + "?")) { await api("/api/memory/" + encodeURIComponent(d.forget), { method: "DELETE" }); loaders.memory(); }
      if (d.editmem) {
        const n = loaders.memory.rows.find((r) => r.name === d.editmem);
        $("#memForm").hidden = false; $("#memName").value = n.name; $("#memDesc").value = n.description; $("#memBody").value = n.body;
      }
      if (d.approve) { await post(`/api/skills/${encodeURIComponent(d.approve)}/approve`); loaders.skills(); }
      if (d.reject) { await post(`/api/skills/${encodeURIComponent(d.reject)}/reject`); loaders.skills(); }
      if (d.delskill && confirm("Delete skill " + d.delskill + "?")) { await post(`/api/skills/${encodeURIComponent(d.delskill)}/delete`); loaders.skills(); }
      if (d.unschedule) { await api("/api/schedule/" + d.unschedule, { method: "DELETE" }); loaders.schedule(); }
      if (d.activate) { await post("/api/desktop/workspaces/activate", { path: d.activate }); loadDesktopSettings(); }
      if (d.unlist && confirm("Remove this sector from the list? Its files stay on disk.")) { await api("/api/desktop/workspaces?path=" + encodeURIComponent(d.unlist), { method: "DELETE" }); loadDesktopSettings(); }
    } catch (err) { toast("ERROR", err.message, "err"); }
  });
  $("#newSession").onclick = () => { store.set("session", ""); connect(""); showTab("directive"); };

  // ---------------------------------------------------------------- desktop: workspaces, key, updates
  async function loadDesktopSettings() {
    DESKTOP = await api("/api/desktop/info");
    $("#keyMasked").textContent = DESKTOP.api_key || "none";
    $("#wsList").innerHTML = DESKTOP.workspaces.map((w) => `
      <div class="item ws-row"><div class="t">${esc(w.name)} ${w.active ? "<b>■ ACTIVE</b>" : ""}${w.exists ? "" : ' <span class="tag">MISSING</span>'}</div>
      <div class="d">${esc(w.path)}</div>
      <div class="acts">${w.active ? "" : `<button class="btn small" data-activate="${esc(w.path)}">ACTIVATE</button>`}<button class="btn small danger" data-unlist="${esc(w.path)}">REMOVE</button></div></div>`).join("");
    $("#curVer").textContent = "v" + DESKTOP.version;
    $("#updRepo").value = DESKTOP.update.repo; $("#updAuto").checked = DESKTOP.update.auto_check;
    onUpdate(DESKTOP.update, true);
  }
  $("#saveKey").onclick = async () => { try { const r = await post("/api/desktop/apikey", { api_key: $("#newKey").value }); $("#newKey").value = ""; $("#keyMasked").textContent = r.api_key; toast("KEY STORED", "new sessions use it"); refreshState(); } catch (e) { toast("KEY REJECTED", e.message, "err"); } };
  $("#forgetKey").onclick = async () => { if (confirm("Remove the stored API key?")) { await post("/api/desktop/apikey", { api_key: "" }); $("#keyMasked").textContent = "none"; refreshState(); } };
  async function addWorkspace() {
    const r = await post("/api/desktop/pick-folder");
    if (!r.path) return;
    await post("/api/desktop/workspaces", { path: r.path });
    loadDesktopSettings();
  }
  $("#addWs").onclick = addWorkspace;
  $("#wsChip").onclick = () => { if (DESKTOP) { showTab("settings"); setTimeout(() => $("#wsList").scrollIntoView({ behavior: "smooth" }), 100); } };
  $("#updSave").onclick = async () => { try { await put("/api/desktop/update/prefs", { repo: $("#updRepo").value, auto_check: $("#updAuto").checked }); toast("SAVED", "update preferences stored"); } catch (e) { toast("ERROR", e.message, "err"); } };
  $("#updCheck").onclick = async () => { $("#updStatus").textContent = "checking…"; onUpdate(await post("/api/desktop/update/check"), false, true); };

  let lastUpd = {}, notified = "";
  function onUpdate(u, quiet = false, manual = false) {
    lastUpd = u;
    const st = { idle: "", checking: "checking…", current: "you're on the latest version.", available: `v${u.latest} is available.`,
      downloading: `downloading v${u.latest}…`, verifying: "verifying checksum…", ready: "verified.", installing: "installing. PALE will restart.", error: u.error }[u.status] || "";
    $("#updStatus").textContent = st;
    const avail = ["available", "downloading", "verifying", "ready", "installing"].includes(u.status) || (u.status === "error" && u.latest && u.latest !== u.current);
    $("#updateChip").hidden = !avail || u.skipped && u.status === "available";
    $("#updateVer").textContent = u.latest ? "v" + u.latest : "";
    if (u.status === "available" && !u.skipped && !manual && notified !== u.latest) {
      notified = u.latest;
      toast("NEW FIRMWARE · v" + u.latest, "click to review the update", "", openUpdate); blip("alert");
    }
    if (manual && u.status === "available") openUpdate();
    if (manual && u.status === "current") toast("UP TO DATE", "v" + u.current + " is the latest release");
    if (manual && u.status === "error") toast("UPDATE CHECK FAILED", u.error, "err");
    // modal progress
    const prog = ["downloading", "verifying", "ready", "installing"].includes(u.status);
    $("#updProgress").hidden = !prog;
    $("#updProgress i").style.width = Math.round((u.progress || 0) * 100) + "%";
    $("#updProgress span").textContent = u.status === "downloading" ? Math.round((u.progress || 0) * 100) + "%" : u.status.toUpperCase();
    $("#updError").hidden = u.status !== "error"; $("#updError").textContent = u.error || "";
    $("#updInstall").disabled = prog;
  }
  function openUpdate() {
    const u = lastUpd;
    if (!u.latest) return;
    $("#updVer").textContent = `v${u.current} → v${u.latest}`;
    $("#updMeta").textContent = (u.published ? "published " + new Date(u.published).toLocaleString() + " · " : "") + (u.size ? kb(u.size) : "") +
      (u.self_install ? "" : " · this install can't replace itself; the download page will open");
    $("#updNotes").innerHTML = md(u.notes || "No release notes.");
    $("#updInstall").textContent = u.self_install ? "INSTALL & RESTART" : "OPEN DOWNLOAD PAGE";
    $("#updateModal").hidden = false;
  }
  $("#updateChip").onclick = openUpdate;
  $("#updLater").onclick = () => { $("#updateModal").hidden = true; };
  $("#updSkip").onclick = async () => { onUpdate(await post("/api/desktop/update/skip"), true); $("#updateModal").hidden = true; };
  $("#updInstall").onclick = async () => {
    if (busy && !confirm("The unit is still working. Install anyway? The current directive will be interrupted.")) return;
    onUpdate(await post("/api/desktop/update/install"), true);
  };

  // ---------------------------------------------------------------- first-run wizard
  let wizStep = 0, wizModel = "claude-opus-5-5";
  function wizShow(n) {
    wizStep = n;
    $$("#wizard section").forEach((s) => s.classList.toggle("on", +s.dataset.step === n));
    $$("#wizSteps li").forEach((li) => { li.classList.toggle("on", +li.dataset.step === n); li.classList.toggle("done", +li.dataset.step < n); });
    $("#wizBack").style.visibility = n ? "visible" : "hidden";
    $("#wizNext").textContent = n === 4 ? "DEPLOY UNIT" : n === 0 ? "BEGIN" : "NEXT";
    if (n === 4) {
      $("#wizSummary").innerHTML = `<dt>Link</dt><dd>${$("#wizKey").value ? "new key" : (DESKTOP.api_key ? "stored key " + esc(DESKTOP.api_key) : "missing")}</dd>
        <dt>Sector</dt><dd class="wrap">${esc($("#wizPath").value)}</dd><dt>Unit</dt><dd>${esc($("#wizName").value)}</dd>
        <dt>Model</dt><dd>${esc(wizModel)}</dd><dt>Mode</dt><dd>${esc($("#wizMode").value)}</dd><dt>Daily cap</dt><dd>${money($("#wizBudget").value, 2)}</dd>`;
    }
  }
  function startWizard() {
    $("#wizard").hidden = false;
    $("#wizVer").textContent = "v" + DESKTOP.version;
    $("#wizPath").value = (DESKTOP.workspaces.find((w) => w.active) || {}).path || DESKTOP.default_workspace;
    $("#wizKeyExisting").hidden = !DESKTOP.api_key;
    $$(".card").forEach((c) => c.classList.toggle("on", c.dataset.model === wizModel));
    wizShow(0);
  }
  $("#wizModels").onclick = (e) => { const c = e.target.closest(".card"); if (!c) return; wizModel = c.dataset.model; $$(".card").forEach((x) => x.classList.toggle("on", x === c)); };
  $("#wizBrowse").onclick = async () => { const r = await post("/api/desktop/pick-folder"); if (r.path) $("#wizPath").value = r.path; };
  $("#wizBack").onclick = () => wizShow(Math.max(0, wizStep - 1));
  $("#wizNext").onclick = async () => {
    if (wizStep === 1) {
      const k = $("#wizKey").value.trim();
      if (!k && !DESKTOP.api_key) return toast("KEY REQUIRED", "paste your Anthropic API key", "err");
      if (k && !k.startsWith("sk-ant-")) return toast("CHECK THE KEY", "Anthropic keys start with sk-ant-", "err");
    }
    if (wizStep === 2 && !$("#wizPath").value.trim()) return toast("CHOOSE A FOLDER", "", "err");
    if (wizStep < 4) return wizShow(wizStep + 1);
    $("#wizNext").disabled = true;
    try {
      await post("/api/desktop/setup", { api_key: $("#wizKey").value.trim(), workspace: $("#wizPath").value.trim(), unit_name: $("#wizName").value.trim(),
        model: wizModel, mode: $("#wizMode").value, budget_daily_usd: Number($("#wizBudget").value) });
      $("#wizKey").value = "";
      $("#wizard").hidden = true; blip("done");
      await refreshState(); connect("");
      toast("UNIT DEPLOYED", "Put files in the sandbox and issue your first directive.");
    } catch (e) { $("#wizError").hidden = false; $("#wizError").textContent = e.message; }
    $("#wizNext").disabled = false;
  };

  // ---------------------------------------------------------------- command palette
  const commands = () => [
    ["New session", "archive", () => $("#newSession").click()],
    ["Halt the unit", "ctrl+.", halt],
    ["Undo last directive's changes", "", () => send({ type: "undo" })],
    ["Toggle dry run", "", () => $("#dry").click()],
    ["Search the archive", "archive", () => { showTab("archive"); $("#archiveFilter").focus(); }],
    ["Rewind to before the last directive", "session", () => { const b = $$(".rewind").pop(); if (b) b.click(); else toast("NOTHING TO REWIND"); }],
    ["Show background jobs", "jobs", () => { const j = [...jobs.keys()].pop(); if (j) openJob(j); else toast("NO JOBS", "the unit hasn't started any"); }],
    ...["observe", "plan", "ask", "edits", "autonomous"].map((m) => ["Mode: " + m, "mode", () => { $("#mode").value = m; send({ type: "mode", mode: m }); }]),
    ...[...$("#modelSel").options].map((o) => ["Model: " + o.text, "model", () => { $("#modelSel").value = o.value; send({ type: "model", model: o.value }); }]),
    ...$$(".menu button").map((b) => ["Go to " + b.textContent.trim(), "tab", () => showTab(b.dataset.tab)]),
    ["Toggle day / night", "theme", () => $("#themeBtn").click()],
    ["Toggle sound", "", () => $("#soundBtn").click()],
    ...templates.map((t) => ["Run: " + t.name, "quick directive", () => transmit(t.text)]),
    ...(DESKTOP ? [
      ["Check for updates", "updates", () => $("#updCheck").click()],
      ["Open sandbox folder", "desktop", () => post("/api/desktop/open", { target: "sandbox" })],
      ["Add a sector (workspace)", "desktop", addWorkspace],
      ["Run setup wizard again", "desktop", startWizard],
      ...DESKTOP.workspaces.filter((w) => !w.active).map((w) => ["Switch sector: " + w.name, "desktop", () => post("/api/desktop/workspaces/activate", { path: w.path })]),
    ] : []),
  ];
  let palItems = [], palSel = 0;
  function openPalette() { $("#palette").hidden = false; $("#paletteInput").value = ""; renderPalette(); $("#paletteInput").focus(); }
  function renderPalette() {
    const q = $("#paletteInput").value.toLowerCase().split(/\s+/).filter(Boolean);
    palItems = commands().filter(([n]) => q.every((w) => n.toLowerCase().includes(w))).slice(0, 40);
    palSel = Math.min(palSel, Math.max(0, palItems.length - 1));
    $("#paletteList").innerHTML = palItems.map(([n, h], i) => `<li class="${i === palSel ? "on" : ""}" data-i="${i}">${esc(n)}<small>${esc(h)}</small></li>`).join("") || '<li class="dim">no match</li>';
  }
  const runPalette = (i) => { const c = palItems[i]; $("#palette").hidden = true; if (c) c[2](); };
  $("#paletteInput").oninput = () => { palSel = 0; renderPalette(); };
  $("#paletteInput").onkeydown = (e) => {
    if (e.key === "ArrowDown") { palSel = Math.min(palSel + 1, palItems.length - 1); renderPalette(); e.preventDefault(); }
    if (e.key === "ArrowUp") { palSel = Math.max(palSel - 1, 0); renderPalette(); e.preventDefault(); }
    if (e.key === "Enter") runPalette(palSel);
  };
  $("#paletteList").onclick = (e) => { const li = e.target.closest("[data-i]"); if (li) runPalette(+li.dataset.i); };
  $("#palette").onclick = (e) => { if (e.target.id === "palette") $("#palette").hidden = true; };
  $("#paletteBtn").onclick = openPalette;

  document.addEventListener("keydown", (e) => {
    const mod = e.ctrlKey || e.metaKey;
    if (mod && e.key.toLowerCase() === "k") { e.preventDefault(); openPalette(); return; }
    if (mod && e.key === ".") { e.preventDefault(); halt(); return; }
    if (e.key === "Escape") {
      if (!$("#palette").hidden) { $("#palette").hidden = true; return; }
      if (!$("#updateModal").hidden) { $("#updateModal").hidden = true; return; }
      if (!$("#jobModal").hidden) { $("#jobModal").hidden = true; jobModalId = ""; return; }
    }
    if (!$("#approval").hidden && document.activeElement !== $("#apNote")) {
      const k = e.key.toLowerCase();
      if (k === "y") answerApproval(true, false);
      else if (k === "a" && !$("#apAlways").disabled) answerApproval(true, true);
      else if (k === "n" || k === "escape") answerApproval(false, false);
    }
  });

  // ---------------------------------------------------------------- start
  async function refreshState() {
    STATE = await api("/api/state");
    document.body.classList.toggle("is-desktop", STATE.desktop);
    $("#unitName").textContent = STATE.unit;
    $("#sandbox").textContent = STATE.sandbox;
    $("#wsName").textContent = STATE.root.split(/[\\/]/).filter(Boolean).pop() || STATE.root;
    $("#budget").textContent = `${money(STATE.budget.session, 2)} / session · ${money(STATE.budget.daily, 2)} / day`;
    $("#keyState").innerHTML = STATE.has_key ? '<span class="ok">■ key present</span>' : '<span class="bad">✕ no API key</span>';
    $("#verLine").textContent = "PALE v" + STATE.version;
    return STATE;
  }
  (async () => {
    try { await refreshState(); } catch (e) { endBoot("> establishing link ........... REFUSED"); return; }
    loadTemplates();
    if (STATE.desktop) {
      await loadDesktopSettings();
      if (!DESKTOP.setup_done) { endBoot("> establishing link ........... FIRST CONTACT"); startWizard(); }
    }
    connect(sessionId);
  })();
})();
