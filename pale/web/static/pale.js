// PALE web terminal. Plain JS, no build step.
(() => {
  const $ = (s) => document.querySelector(s);
  const params = new URLSearchParams(location.search);
  let token = params.get("token") || "";
  try {
    if (token) sessionStorage.setItem("pale-token", token); else token = sessionStorage.getItem("pale-token") || "";
  } catch (_) {}
  if (params.has("token")) history.replaceState(null, "", location.pathname);

  const FLAVOR = [
    "The city is quiet. The sandbox is sealed.",
    "Dust settles on the towers. Systems nominal.",
    "Every action is recorded. Nothing is forgotten.",
    "No signal from the surface. Awaiting directive.",
  ];
  $("#flavor").textContent = FLAVOR[Math.floor(Math.random() * FLAVOR.length)];

  // drifting dust
  const dust = $(".dust");
  for (let i = 0; i < 18; i++) {
    const d = document.createElement("i");
    d.style.left = Math.random() * 100 + "vw";
    d.style.animationDuration = 18 + Math.random() * 30 + "s";
    d.style.animationDelay = -Math.random() * 40 + "s";
    d.style.opacity = (0.15 + Math.random() * 0.3).toFixed(2);
    dust.appendChild(d);
  }

  // ---------------------------------------------------------------- boot sequence
  const bootLines = [
    "PALE OPERATOR SUPPORT HARNESS  v0.1",
    "",
    "> mounting sandbox ............ OK",
    "> black box chain ............. VERIFYING",
    "> memory banks ................ OK",
    "> approval channel ............ OK",
    "> establishing link ........... ",
  ];
  const bootEl = $("#bootText");
  let bi = 0;
  const bootTimer = setInterval(() => {
    bootEl.textContent += bootLines[bi++] + "\n";
    if (bi >= bootLines.length) clearInterval(bootTimer);
  }, 110);
  const endBoot = (msg) => {
    setTimeout(() => {
      bootEl.textContent += msg + "\n";
      setTimeout(() => $("#boot").classList.add("gone"), 380);
    }, 850);
  };

  // ---------------------------------------------------------------- helpers
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  function md(src) {
    // tiny, safe markdown: fences, inline code, bold, paragraphs
    const parts = String(src).split(/```/);
    return parts.map((p, i) => {
      if (i % 2) return "<pre><code>" + esc(p.replace(/^\w*\n/, "")) + "</code></pre>";
      return esc(p)
        .replace(/`([^`]+)`/g, "<code>$1</code>")
        .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
        .split(/\n{2,}/).map((x) => x.trim() ? "<p>" + x.replace(/\n/g, "<br>") + "</p>" : "").join("");
    }).join("");
  }
  function diffHtml(text) {
    return esc(text).split("\n").map((l) =>
      l.startsWith("+") && !l.startsWith("+++") ? `<span class="add">${l}</span>` :
      l.startsWith("-") && !l.startsWith("---") ? `<span class="del">${l}</span>` :
      l.startsWith("@@") ? `<span class="hunk">${l}</span>` : l).join("\n");
  }
  const api = async (path, opts = {}) => {
    const r = await fetch(path, { ...opts, headers: { "X-Pale-Token": token, "Content-Type": "application/json", ...(opts.headers || {}) } });
    if (!r.ok) throw new Error(r.status + " " + (await r.text()));
    return r.json();
  };

  // ---------------------------------------------------------------- transcript
  const log = $("#log");
  let streamEl = null, streamKind = null, streamText = "";
  const tools = {};
  const scroll = () => { log.scrollTop = log.scrollHeight; };
  function add(html, cls) {
    const el = document.createElement("div");
    el.className = cls;
    el.innerHTML = html;
    log.appendChild(el);
    scroll();
    return el;
  }
  function endStream() {
    if (streamEl) streamEl.classList.remove("cursor");
    streamEl = null; streamKind = null; streamText = "";
  }
  function stream(kind, delta) {
    if (streamKind !== kind) {
      endStream();
      streamKind = kind;
      if (kind === "text") {
        const row = add('<div class="who">UNIT</div><div class="body cursor"></div>', "row unit");
        streamEl = row.querySelector(".body");
      } else {
        streamEl = add("", "think cursor");
      }
    }
    streamText += delta;
    if (kind === "text") streamEl.innerHTML = md(streamText); else streamEl.textContent = "∴ " + streamText;
    scroll();
  }
  const operator = (text) => { endStream(); add(`<div class="who">OPERATOR</div><div class="body">${esc(text)}</div>`, "row op"); };
  function toolCall(d) {
    endStream();
    const el = add(`<span class="name">■ ${esc(d.tool)}</span><span class="arg">${esc(d.arg)}</span><span class="risk ${d.risk}">${d.risk}</span>`, "tool");
    tools[d.id] = el;
  }
  function toolResult(d) {
    const el = tools[d.id] || add(`<span class="name">■ ${esc(d.tool)}</span>`, "tool");
    if (!d.ok) el.classList.add("err");
    const det = document.createElement("details");
    det.innerHTML = `<summary>${d.ok ? "result" : "error"} · ${d.output.split("\n").length} line(s)</summary><div class="out">${esc(d.output)}</div>`;
    el.appendChild(det);
    el.style.flexWrap = "wrap";
    scroll();
  }
  const notice = (text, cls = "") => { endStream(); add(esc(text), "notice " + cls); };

  function renderRows(rows) {
    log.innerHTML = "";
    for (const r of rows) {
      if (r.kind === "text" && r.who === "user") operator(r.text);
      else if (r.kind === "text") { const row = add('<div class="who">UNIT</div><div class="body"></div>', "row unit"); row.querySelector(".body").innerHTML = md(r.text); }
      else if (r.kind === "tool_call") add(`<span class="name">■ ${esc(r.tool)}</span><span class="arg">${esc(r.text)}</span>`, "tool");
      else if (r.kind === "tool_result") add(`<details><summary>${r.ok ? "result" : "error"}</summary><div class="out">${esc(r.text)}</div></details>`, "tool" + (r.ok ? "" : " err"));
    }
    endStream();
  }
  function renderPlan(items) {
    const ul = $("#plan");
    ul.innerHTML = items && items.length ? items.map((i) => `<li class="${i.status}">${esc(i.text)}</li>`).join("") : '<li class="dim">no plan filed</li>';
  }

  // ---------------------------------------------------------------- socket
  let ws, sessionId = params.get("session") || "", busy = false;
  const setBusy = (b, msg) => {
    busy = b;
    $("#statusLine").textContent = msg || (b ? "unit active" : "standing by");
  };
  function connect(session) {
    if (ws) { ws.onclose = null; ws.close(); }
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(token)}&session=${encodeURIComponent(session || "")}`);
    ws.onmessage = (e) => handle(JSON.parse(e.data));
    ws.onclose = (e) => {
      if (e.code === 4401) { endBoot("> establishing link ........... REFUSED (bad token)"); notice("link refused: open the URL with ?token= printed by `pale serve`", "err"); return; }
      notice("link lost. reconnecting…", "warn");
      setTimeout(() => connect(sessionId), 2000);
    };
  }
  function handle(m) {
    if (m.type === "hello") {
      sessionId = m.session;
      $("#sessionId").textContent = "SESSION " + m.session;
      $("#mode").value = m.mode;
      setDry(m.dry_run);
      renderRows(m.rows);
      renderPlan(m.todos);
      $("#cost").textContent = "$" + m.usd.toFixed(4);
      endBoot("> establishing link ........... OK");
      return;
    }
    if (m.type === "mode") { $("#mode").value = m.mode; notice("mode → " + m.mode.toUpperCase(), "warn"); return; }
    if (m.type === "dry") { setDry(m.on); return; }
    if (m.type === "approval") return showApproval(m);
    if (m.type === "question") return showQuestion(m);
    if (m.type !== "event") return;
    const d = m.data;
    switch (m.kind) {
      case "text": case "thinking": stream(m.kind, d.delta); break;
      case "status":
        if (d.message === "thinking") { setBusy(true, "unit active · step " + d.step); $("#step").textContent = d.step; }
        else notice("» " + d.message);
        break;
      case "tool_call": toolCall(d); break;
      case "tool_result": toolResult(d); break;
      case "sub_tool_call": notice("· support unit → " + d.tool + " " + d.arg); break;
      case "denied": notice("✕ denied: " + d.tool + " — " + d.reason, "deny"); break;
      case "warning": if (!String(d.message).startsWith("malformed")) notice("⚠ " + d.message, "warn"); break;
      case "error": notice("✕ " + d.message, "err"); setBusy(false); break;
      case "todo": renderPlan(d.items); break;
      case "cost":
        $("#cost").textContent = "$" + d.usd.toFixed(4);
        $("#today").textContent = "$" + d.today.toFixed(2);
        $("#tokens").textContent = `${d.input_tokens.toLocaleString()} in · ${d.output_tokens.toLocaleString()} out · ${d.cache_read.toLocaleString()} cached`;
        break;
      case "done":
        endStream();
        add(`── REPORT FILED · ${d.steps} STEP(S) · $${d.usd.toFixed(4)} SESSION`, "report-end");
        setBusy(false);
        break;
    }
  }

  // ---------------------------------------------------------------- approvals
  let pendingApprovals = [];
  function showApproval(m) {
    pendingApprovals.push(m);
    if (pendingApprovals.length === 1) openApproval();
  }
  function openApproval() {
    const m = pendingApprovals[0];
    if (!m) { $("#approval").hidden = true; return; }
    $("#apRisk").className = "risk " + m.risk;
    $("#apRisk").textContent = m.risk;
    $("#apSummary").textContent = m.summary;
    $("#apReasons").textContent = (m.reasons || []).filter(Boolean).join(" · ");
    $("#apPreview").innerHTML = m.preview ? diffHtml(m.preview) : "(no preview)";
    $("#apNote").value = "";
    $("#apAlways").disabled = m.risk === "high";
    $("#approval").hidden = false;
    $("#apYes").focus();
  }
  function answerApproval(approved, always) {
    const m = pendingApprovals.shift();
    ws.send(JSON.stringify({ type: "approval", id: m.id, approved, always, note: $("#apNote").value }));
    openApproval();
  }
  $("#apYes").onclick = () => answerApproval(true, false);
  $("#apAlways").onclick = () => answerApproval(true, true);
  $("#apNo").onclick = () => answerApproval(false, false);
  document.addEventListener("keydown", (e) => {
    if ($("#approval").hidden) return;
    if (e.key === "Escape") answerApproval(false, false);
  });

  function showQuestion(m) {
    $("#qText").textContent = m.text;
    $("#qAnswer").value = "";
    $("#question").hidden = false;
    $("#qAnswer").focus();
  }
  $("#qSend").onclick = () => {
    ws.send(JSON.stringify({ type: "answer", text: $("#qAnswer").value }));
    $("#question").hidden = true;
  };

  // ---------------------------------------------------------------- controls
  $("#form").onsubmit = (e) => {
    e.preventDefault();
    const text = $("#text").value.trim();
    if (!text) return;
    if (busy) { notice("unit is still executing. HALT first, or wait.", "warn"); return; }
    operator(text);
    ws.send(JSON.stringify({ type: "directive", text }));
    $("#text").value = "";
    setBusy(true);
  };
  $("#text").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#form").requestSubmit(); }
  });
  $("#halt").onclick = () => { ws.send(JSON.stringify({ type: "interrupt" })); notice("⚠ halt signal sent", "warn"); };
  $("#undo").onclick = () => ws.send(JSON.stringify({ type: "undo" }));
  $("#mode").onchange = (e) => ws.send(JSON.stringify({ type: "mode", mode: e.target.value }));
  function setDry(on) {
    $("#dry").setAttribute("aria-pressed", on ? "true" : "false");
    $("#dry").querySelector("b").textContent = on ? "ON" : "OFF";
  }
  $("#dry").onclick = () => ws.send(JSON.stringify({ type: "dry", on: $("#dry").getAttribute("aria-pressed") !== "true" }));

  // ---------------------------------------------------------------- tabs
  const loaders = {
    archive: async () => {
      const rows = await api("/api/sessions");
      $("#sessions").innerHTML = rows.map((r) => `
        <div class="item"><div class="t">${esc(r.title || "untitled")}</div>
        <div class="d">${esc(r.id)} · ${esc(r.surface)} · ${r.turns} msgs · $${r.usd.toFixed(4)} · ${new Date(r.updated * 1000).toLocaleString()}</div>
        <div class="acts"><button class="btn small" data-resume="${esc(r.id)}">RESUME</button></div></div>`).join("") || '<div class="item dim">archive empty</div>';
    },
    memory: async () => {
      const rows = await api("/api/memory");
      $("#memory").innerHTML = rows.map((n) => `
        <div class="item"><div class="t">${esc(n.name)}</div><div class="d">${esc(n.description)}</div>
        <div class="acts"><button class="btn small danger" data-forget="${esc(n.name)}">FORGET</button></div>
        <pre>${esc(n.body)}</pre></div>`).join("") || '<div class="item dim">memory banks empty</div>';
    },
    skills: async () => {
      const rows = await api("/api/skills");
      $("#skills").innerHTML = rows.map((s) => `
        <div class="item"><div class="t">${esc(s.name)} ${s.quarantined ? '<span class="tag">QUARANTINED</span>' : ""}</div>
        <div class="d">${esc(s.description)}</div>
        <div class="acts">${s.quarantined ? `<button class="btn small" data-approve="${esc(s.name)}">AUTHORIZE</button><button class="btn small danger" data-reject="${esc(s.name)}">REJECT</button>` : ""}</div>
        <pre>${esc(s.body)}</pre></div>`).join("") || '<div class="item dim">no skills yet. The unit proposes them after repeated work.</div>';
    },
    schedule: async () => {
      const rows = await api("/api/schedule");
      $("#schedule").innerHTML = rows.map((t) => `
        <div class="item"><div class="t">${esc(t.name)}</div>
        <div class="d">${esc(t.spec)} · next ${esc(t.next || "—")} · last ${esc(t.last || "never")}${t.enabled ? "" : " · finished"}</div>
        <div class="acts"><button class="btn small danger" data-unschedule="${esc(t.id)}">REMOVE</button></div></div>`).join("") || '<div class="item dim">no scheduled directives</div>';
    },
    blackbox: async () => {
      const r = await api("/api/audit");
      $("#chain").innerHTML = `<span class="${r.ok ? "ok" : "bad"}">${r.ok ? "■ CHAIN INTACT" : "✕ " + esc(r.message)}</span> <span class="dim">${r.count} entries</span>`;
      $("#audit").innerHTML = r.entries.reverse().map((e) => `
        <div class="audit-row"><span class="dim">${new Date(e.ts * 1000).toLocaleString()}</span><span class="dim">${esc(e.session.slice(-6))}</span>
        <span class="k">${esc(e.kind)}</span><span>${esc(JSON.stringify(e.data)).slice(0, 220)}</span></div>`).join("");
    },
  };
  function showTab(name) {
    document.querySelectorAll(".menu button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.id === "tab-" + name));
    if (loaders[name]) loaders[name]().catch((e) => notice("✕ " + e.message, "err"));
  }
  $("#menu").onclick = (e) => { const b = e.target.closest("button[data-tab]"); if (b) showTab(b.dataset.tab); };
  document.body.addEventListener("click", async (e) => {
    const t = e.target;
    if (t.dataset.resume) { connect(t.dataset.resume); showTab("directive"); }
    if (t.dataset.forget && confirm("Forget " + t.dataset.forget + "?")) { await api("/api/memory/" + encodeURIComponent(t.dataset.forget), { method: "DELETE" }); loaders.memory(); }
    if (t.dataset.approve) { await api(`/api/skills/${encodeURIComponent(t.dataset.approve)}/approve`, { method: "POST" }); loaders.skills(); }
    if (t.dataset.reject) { await api(`/api/skills/${encodeURIComponent(t.dataset.reject)}/reject`, { method: "POST" }); loaders.skills(); }
    if (t.dataset.unschedule) { await api("/api/schedule/" + t.dataset.unschedule, { method: "DELETE" }); loaders.schedule(); }
  });
  $("#newSession").onclick = () => { connect(""); showTab("directive"); };
  $("#schedForm").onsubmit = async (e) => {
    e.preventDefault();
    try {
      await api("/api/schedule", { method: "POST", body: JSON.stringify({ spec: $("#schedSpec").value, directive: $("#schedText").value }) });
      $("#schedSpec").value = $("#schedText").value = "";
      loaders.schedule();
    } catch (err) { alert(err.message); }
  };

  // ---------------------------------------------------------------- start
  api("/api/state").then((s) => {
    $("#unitName").textContent = s.unit;
    $("#model").textContent = s.model;
    $("#sandbox").textContent = s.sandbox;
    $("#budget").textContent = `$${s.budget.session.toFixed(2)} / session · $${s.budget.daily.toFixed(2)} / day`;
  }).catch(() => {});
  connect(sessionId);
})();
