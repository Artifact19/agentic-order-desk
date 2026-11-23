const BASE_STEPS = [
  { name: "understand", label: "Understand message with session context" },
  { name: "recall", label: "Recall long-term memory" },
  { name: "connect", label: "Check Zomato sign-in and address" },
  { name: "search", label: "Search Zomato and apply filters" },
  { name: "rank", label: "Rank dishes (MAUT + memory)" },
  { name: "explain", label: "Explain the choice" },
];

const state = {
  sessionId: null,
  context: null,
  previousConstraints: null,
  messages: [],
  system: null,
  run: null,
  runDetail: null,
  source: null,
  selectedAgent: null,
  online: true,
  busy: false,
  activeTab: "session",
  sessions: [],
};

function sourceBadge(source) {
  if (!source) return "";
  const badge = (cls, text) => `<span class="src ${cls}" title="${esc(source)}">${text}</span>`;
  if (source === "this message") return badge("src-msg", "this message");
  if (source.startsWith("session context")) {
    const turn = (source.match(/turn (\d+)/) || [])[1];
    return badge("src-ctx", turn ? `earlier · turn ${turn}` : "earlier turn");
  }
  if (source.startsWith("refined")) return badge("src-ref", "refined");
  if (source.startsWith("long-term memory")) {
    const id = (source.match(/\((.+)\)/) || [])[1];
    return badge("src-ltm", id ? `memory · ${esc(id)}` : "memory");
  }
  return badge("src-def", esc(source));
}

function describeConstraints(c) {
  return {
    item: c.item ? c.item[0].toUpperCase() + c.item.slice(1) : "—",
    is_veg: c.is_veg === null || c.is_veg === undefined ? "—" : c.is_veg ? "Veg only" : "Non-veg",
    max_price: c.max_price ? `≤ Rs ${fmt(c.max_price)}` : "—",
    max_eta_minutes: c.max_eta_minutes ? `≤ ${c.max_eta_minutes} min` : "—",
  };
}

function renderContext() {
  const ctx = state.context;
  if (!ctx) return;
  const picker = $("#session-picker");
  if (![...picker.options].some((o) => o.value === ctx.session_id)) {
    picker.insertAdjacentHTML("afterbegin", `<option value="${esc(ctx.session_id)}">${esc(ctx.session_id)}</option>`);
  }
  picker.value = ctx.session_id;
  $("#session-turns").textContent = `${ctx.turn_no} turn${ctx.turn_no === 1 ? "" : "s"}`;
  $("#address-input").value = ctx.address;

  const slots = $("#slots");
  if (ctx.constraints) {
    const view = describeConstraints(ctx.constraints);
    const prev = state.previousConstraints;
    const names = { item: "Dish", is_veg: "Diet", max_price: "Budget", max_eta_minutes: "Delivery" };
    slots.innerHTML = Object.entries(names).map(([key, label]) => {
      const changed = prev && prev[key] !== ctx.constraints[key];
      return `<div class="slot${changed ? " changed" : ""}"><div class="k">${label}</div>
        <div class="v">${esc(view[key])}</div>${sourceBadge(ctx.constraint_sources[key])}</div>`;
    }).join("");
    $("#slots-empty").hidden = true;
  } else {
    slots.innerHTML = "";
    $("#slots-empty").hidden = false;
  }
  state.previousConstraints = ctx.constraints ? { ...ctx.constraints } : null;

  $("#window-count").textContent = `${ctx.window.length} / ${ctx.window_limit}`;
  $("#window-list").innerHTML = ctx.window.map((w) =>
    `<li><span class="who ${w.role}">${w.role === "user" ? "You" : "Agent"}</span><span class="txt">${esc(w.text)}</span></li>`).join("");
  $("#declined").innerHTML = ctx.rejected_items.map((r) => `<span class="chip strike" title="${esc(r.restaurant_name)}">${esc(r.item_name)}</span>`).join("");
  $("#session-orders").innerHTML = ctx.orders.map((o) =>
    `<div class="order-row"><span>${esc(o.item_name)}</span><b class="mono">${esc(o.order_id)}</b></div>`).join("");
  renderSuggestions();
  refreshActionStates();
}

function renderSuggestions() {
  const ctx = state.context;
  let items;
  if (ctx && ctx.pending) {
    items = ["yes, place the order", "next option", "why this one?", "what about biryani?", "under 300 please", "make it non-veg"];
  } else if (ctx && ctx.constraints) {
    items = ["what about pasta?", "increase the budget", "no rush, I can wait", "what did I ask for?", "order the same again"];
  } else {
    items = ["Order a veg pizza under 500 within 30 minutes", "Misal pav under 150 in 20 minutes", "Get me a non-veg burger under 250", "Order the same as last time"];
  }
  $("#suggestions").innerHTML = items.map((t) => `<button type="button" class="suggestion">${esc(t)}</button>`).join("");
}

function renderLongTermStats() {
  const memory = state.system?.memory;
  if (!memory) return;
  const html = `<div class="stat"><div class="n">${memory.total}</div><div class="l">memories</div></div>
    <div class="stat"><div class="n">${memory.orders}</div><div class="l">orders</div></div>
    <div class="stat"><div class="n">${memory.rejections}</div><div class="l">rejections</div></div>`;
  $("#ltm-stats").innerHTML = html;
  $("#memory-stats").innerHTML = html;
}

function renderModelStatus() {
  const pill = $("#model-status");
  if (!state.online) {
    pill.className = "status-pill down";
    pill.lastElementChild.textContent = "Offline";
    return;
  }
  const llm = state.system?.llm;
  if (!llm) return;
  const ok = llm.status.startsWith("loaded");
  pill.className = `status-pill ${ok ? "ok" : "degraded"}`;
  pill.title = `LLM: ${llm.status}\nEmbeddings: ${state.system.embeddings.status}`;
  pill.lastElementChild.textContent = ok ? `${llm.model.split("/").pop()} · ${llm.status.replace("loaded on ", "").toUpperCase()}` : "Rule-based parser";
}

function welcomeHtml() {
  const crew = BLOB_CREW.map((b) => `<span>${blob(b.color, b.shape)}</span>`).join("");
  return `<div class="welcome" id="welcome">
    <div class="crew">${crew}</div>
    <h1>Watch the agent work</h1>
    <p>Send a request here or on the customer page. Every step, query and decision shows up on the right.</p>
    <div class="welcome-grid">
      <div class="welcome-card"><b>Session</b><span>What the agent remembers in this chat, and where each value came from.</span></div>
      <div class="welcome-card"><b>Browser</b><span>Every search, menu and screenshot from Zomato for the latest request.</span></div>
      <div class="welcome-card"><b>Checkpoints</b><span>Each step is saved, so a failed run continues from where it stopped.</span></div>
    </div>
  </div>`;
}

function scoreBars(s) {
  const bar = (label, value, max, cls = "") => {
    const width = Math.max(0, Math.min(Math.abs(value) / max, 1)) * 100;
    return `<div class="bar-row"><span>${label}</span><div class="bar ${cls}"><i style="width:${width.toFixed(0)}%"></i></div><b>${signed(value)}</b></div>`;
  };
  return `<div class="bars">${bar("Rating", s.rating_pts, 40)}${bar("Speed", s.speed_pts, 35)}${bar("Savings", s.price_pts, 25)}
    ${bar("Memory", s.memory_adjustment, 15, s.memory_adjustment < 0 ? "mem-neg" : "mem-pos")}</div>`;
}

function scoreRing(score) {
  const pct = Math.max(0, Math.min(score, 100));
  return `<div class="score-ring" style="background:conic-gradient(var(--accent) ${pct}%, var(--line) 0)">
    <div class="inner"><div><b>${fmt(score, 1)}</b><span>/ 100</span></div></div></div>`;
}

function runMetaLine(run) {
  if (!run) return "";
  return `<div class="meta-line">
    <span>${run.cards_read} cards · ${run.menus_read} menus read</span>
    <span>${run.browser_actions} browser action${run.browser_actions === 1 ? "" : "s"}</span>
    <span>cache ${run.cache_hits}/${run.cache_lookups} hits</span>
    <span>${ICON.db.replace("<svg", '<svg style="width:12px;height:12px;vertical-align:-2px"')} ${run.db_queries} DB quer${run.db_queries === 1 ? "y" : "ies"}</span>
    <span>${fmt(run.total_ms)} ms</span></div>`;
}

function contextUsed(run) {
  if (!run) return "";
  const view = describeConstraints(run.constraints);
  const labels = { item: "Dish", is_veg: "Diet", max_price: "Budget", max_eta_minutes: "Delivery" };
  const rows = Object.entries(labels).map(([k, l]) => `<div class="ctx-row"><span class="k">${l}</span><b class="v">${esc(view[k])}</b>${sourceBadge(run.sources[k])}</div>`).join("");
  const notes = run.notes?.length ? `<div class="meta-line ctx-note">Refinement: ${esc(run.notes.join("; "))}</div>` : "";
  const dish = run.dish ? `<div class="meta-line ctx-note">Dish reading: rules "${esc(run.dish.rules || "—")}" · model "${esc(run.dish.model || "—")}" → <b>${esc(run.dish.chosen || "—")}</b> (${esc(run.dish.method)})</div>` : "";
  const recalled = run.recalled?.length ? `<div class="ctx-recalled"><span class="k">Memories recalled</span>${run.recalled.map((m) => `<span class="mem-chip" title="similarity ${m.similarity}">${esc(m.memory_id)} · ${esc(m.memory_type)} · ${Math.round(m.similarity * 100)}%</span>`).join("")}</div>` : "";
  return `<div class="card-section ctx-section"><div class="eyebrow" style="margin-bottom:8px">Context used</div><div class="ctx-used">${rows}</div>${dish}${notes}${recalled}</div>`;
}

function recommendationCard(m) {
  const p = m.payload;
  const s = p.selection;
  const memoryTags = s.memory_reasons.length
    ? s.memory_reasons.map((r) => `<span class="tag ${r.includes("rejected") ? "bad" : "good"}">${ICON.brain}${esc(r)}</span>`).join("")
    : "";
  const ranking = p.ranking.map((r, i) => `<tr class="${i === 0 ? "current" : ""}"><td>${i === 0 ? "▶ " : ""}${esc(r.item_name.split(" (")[0])}</td>
      <td>${esc(r.restaurant_name)}</td><td class="num">Rs ${r.price}</td><td class="num">${esc(r.eta_range || r.eta_minutes + "m")}</td><td class="num">${fmt(r.final_score, 1)}</td></tr>`).join("");
  return `<div class="card rec-card" data-run="${esc(p.run_id)}" data-item="${esc(s.item_id)}" data-message="${m.message_id}">
    <div class="card-section">
      <div class="rec-head">
        <div>
          <div class="eyebrow">Recommendation · option ${p.option_no} of ${p.remaining + p.option_no - 1}</div>
          <div class="rec-title">${esc(s.item_name)}</div>
          <div class="rec-sub">${esc(s.restaurant_name)} · <a href="${esc(s.url)}" target="_blank" rel="noopener">open on Zomato</a></div>
        </div>
        <div class="price"><div class="now">Rs ${s.price}</div></div>
      </div>
      <div class="tags">
        <span class="tag">${ICON.clock}${esc(s.eta_range || s.eta_minutes + " min")}</span>
        <span class="tag">${ICON.star}${s.rating} / 5</span>
        ${s.offer_text ? `<span class="tag accent">${ICON.tag}${esc(s.offer_text)}</span>` : ""}
        ${memoryTags}
      </div>
    </div>
    <div class="card-section scores">${scoreBars(s)}${scoreRing(s.final_score)}</div>
    <div class="card-section explain">${esc(p.explanation.comparison)}
      <div class="just">${esc(p.explanation.justification)}</div>
      ${p.explanation.llm_rewrite_used ? '<div class="meta-line" style="margin-top:6px">Wording by local LLM · passed grounding checks</div>' : ""}
    </div>
    ${contextUsed(p.run)}
    <div class="card-section"><details class="more"><summary>Ranked dishes (${p.remaining} remaining)</summary>
      <table class="mini-rank">${ranking}</table></details></div>
    <div class="actions">
      <button class="btn btn-success" data-act="approve">${ICON.check}Add to Zomato cart</button>
      <button class="btn" data-act="next">${ICON.rotate}Next option</button>
      <button class="btn" data-say="why this one?">Why?</button>
      <span class="spacer"></span>
      <button class="btn btn-ghost-danger" data-act="cancel">${ICON.x}Cancel</button>
    </div>
  </div>${runMetaLine(p.run)}`;
}

function orderCard(m, animate) {
  const p = m.payload;
  const icons = [ICON.chef, ICON.bike, ICON.box, ICON.party];
  const rows = p.tracking.map((t, i) => `<div class="track-row${animate ? "" : " on"}${t.startsWith("Check this:") ? " warn" : ""}" data-i="${i}">${icons[Math.min(i, icons.length - 1)]}<span>${esc(t)}</span></div>`).join("");
  const bill = p.handoff && p.handoff.bill;
  const billRows = bill ? [["Item total", bill.item_total], ["Delivery fee" + (bill.distance_km ? ` (${bill.distance_km} km)` : ""), bill.delivery_fee],
    ["Taxes", bill.taxes], ["Savings", bill.savings], ["To pay", bill.to_pay], ["Delivery time", bill.delivery_time]]
    .filter(([, v]) => v).map(([k, v]) => `<div class="ctx-row"><span class="k">${esc(k)}</span><b class="v">${esc(v)}</b></div>`).join("") : "";
  const shot = p.handoff && p.handoff.screenshot ? `<a href="${esc(p.handoff.screenshot)}" target="_blank"><img class="shot-img" src="${esc(p.handoff.screenshot)}" alt="Zomato checkout"></a>` : "";
  const memories = p.new_memories.map((r) => `<div class="mem"><div class="hd"><span class="ty-${r.memory_type}">${esc(r.memory_type)}</span><span class="mono">${esc(r.memory_id)}</span></div>${esc(r.text)}</div>`).join("");
  return `<div class="card result-card order">
    <div class="card-section">
      <div class="eyebrow">${p.handoff && p.handoff.checkout_open ? "Zomato checkout open" : "Handed to Zomato"} · <span class="mono">${esc(p.order.order_id)}</span></div>
      <div class="rec-title">${esc(p.selection.item_name)}</div>
      <div class="rec-sub">${esc(p.selection.restaurant_name)} · Rs ${p.selection.price} · ${esc(p.selection.eta_range || p.selection.eta_minutes + " min")}</div>
      <div class="tags"><span class="tag good">stopped before payment</span>
        <span class="tag" title="Idempotency key">key ${esc(p.idempotency_key.slice(0, 18))}…</span></div>
    </div>
    ${billRows ? `<div class="card-section"><div class="eyebrow" style="margin-bottom:6px">Bill read from Zomato checkout</div><div class="ctx-used">${billRows}</div></div>` : ""}
    <div class="card-section"><div class="tracking">${rows}</div>${shot}</div>
    ${memories ? `<div class="card-section"><div class="eyebrow" style="margin-bottom:6px">Saved to long-term memory</div><div class="mem-list">${memories}</div></div>` : ""}
  </div>`;
}

function mutedCard(m) {
  const memories = (m.payload?.new_memories || []).map((r) => `<span class="chip">${esc(r.memory_id)} · ${esc(r.memory_type)}</span>`).join("");
  return `<div class="card result-card muted"><div class="card-section">${esc(m.text)}
    ${memories ? `<div class="chips" style="margin-top:8px">${memories}</div>` : ""}</div></div>${runMetaLine(m.payload?.run)}`;
}

function explainCard(m) {
  const b = m.payload.breakdown;
  return `<div class="bubble">${esc(m.text)}<div class="meta-line" style="margin-top:8px">${esc(m.payload.justification)}</div>
    <div style="margin-top:10px">${scoreBars(b)}</div></div>`;
}

function renderMessage(m, animate) {
  const el = document.createElement("div");
  if (m.role === "user") {
    el.className = `msg user${m.kind === "action" ? " action" : ""}`;
    el.innerHTML = `<div class="msg-body"><div class="bubble">${esc(m.text)}</div></div>`;
  } else {
    el.className = "msg agent";
    let body;
    if (m.kind === "recommendation") body = `<div class="bubble">${esc(m.text)}</div>${recommendationCard(m)}`;
    else if (m.kind === "order") body = orderCard(m, animate);
    else if (["no_offer", "exhausted", "cancelled"].includes(m.kind)) body = mutedCard(m);
    else if (m.kind === "explain") body = explainCard(m);
    else body = `<div class="bubble">${esc(m.text)}</div>`;
    el.innerHTML = `<div class="avatar">${blob()}</div><div class="msg-body">${body}</div>`;
  }
  if (!animate) el.style.animation = "none";
  if (animate && m.kind === "order") {
    setTimeout(() => $$(".track-row", el).forEach((row, i) => setTimeout(() => row.classList.add("on"), i * 650)), 100);
  }
  return el;
}

function renderThread() {
  const thread = $("#thread");
  thread.innerHTML = "";
  if (!state.messages.length) thread.insertAdjacentHTML("beforeend", welcomeHtml());
  state.messages.forEach((m) => thread.appendChild(renderMessage(m, false)));
  renderLiveRun();
  refreshActionStates();
  scrollThread();
}

function appendMessages(list) {
  $("#welcome")?.remove();
  const thread = $("#thread");
  const live = $("#live-run");
  list.forEach((m) => {
    state.messages.push(m);
    const node = renderMessage(m, true);
    if (live) thread.insertBefore(node, live); else thread.appendChild(node);
  });
  refreshActionStates();
  scrollThread();
}

function scrollThread() {
  const thread = $("#thread");
  requestAnimationFrame(() => { thread.scrollTop = thread.scrollHeight; });
}

function refreshActionStates() {
  const pending = state.context?.pending;
  const cards = $$(".rec-card");
  const last = cards.filter((c) => pending && c.dataset.run === pending.run_id && c.dataset.item === pending.selection.item_id).pop();
  cards.forEach((card) => {
    const actions = $(".actions", card);
    if (actions) actions.hidden = card !== last;
  });
}

function stepIcon(status) {
  const inner = { done: ICON.check, restored: ICON.rotate, retry: ICON.rotate, failed: ICON.x }[status] || "";
  return `<span class="st-icon st-${status}">${inner}</span>`;
}

function newRun(runId, message) {
  return { run_id: runId, message, status: "running", resumeCount: 0, error: null,
    steps: BASE_STEPS.map((s) => ({ ...s, status: "pending", retries: 0, cache: [], sql: [] })) };
}

function runFromView(view) {
  const steps = view.planned_steps.map((s) => {
    const metric = view.step_metrics[s.name];
    const done = view.completed_steps.includes(s.name);
    let status = done ? "done" : "pending";
    if (!done && s.name === view.current_step) status = view.status === "running" ? "running" : (["interrupted", "failed"].includes(view.status) ? "failed" : "pending");
    return { name: s.name, label: s.label, status, retries: 0, ms: metric?.duration_ms, attempts: metric?.attempts,
      db: metric?.db_queries, cache: metric?.cache || [], sql: metric?.sql || [], error: status === "failed" ? view.error : null };
  });
  return { run_id: view.run_id, message: view.message, status: view.status, resumeCount: view.resume_count, error: view.error, steps };
}

function upsertStep(run, name, label) {
  let step = run.steps.find((s) => s.name === name);
  if (!step) {
    step = { name, label, status: "pending", retries: 0, cache: [], sql: [] };
    const rankIndex = run.steps.findIndex((s) => s.name === "rank");
    if (name.startsWith("menu:") && rankIndex >= 0) run.steps.splice(rankIndex, 0, step); else run.steps.push(step);
  }
  if (label) step.label = label;
  return step;
}

function handleEvent(event) {
  const run = state.run;
  if (!run) return;
  if (event.type === "run_started") {
    run.status = "running";
    run.error = null;
    if (event.resumed) {
      run.resumeCount = event.resume_count ?? run.resumeCount + 1;
      run.steps.forEach((s) => Object.assign(s, { status: "pending", error: null, retries: 0 }));
    }
  } else if (event.type === "plan") {
    const known = new Map(run.steps.map((s) => [s.name, s]));
    run.steps = event.steps.map((p) => known.get(p.name) || { ...p, status: "pending", retries: 0, cache: [], sql: [] });
  } else if (event.type === "step_started") {
    upsertStep(run, event.step, event.label).status = "running";
  } else if (event.type === "step_retry") {
    const step = upsertStep(run, event.step, event.label);
    step.status = "retry";
    step.retries = event.attempt;
    step.error = `${event.error} · retrying in ${event.retry_in_ms} ms`;
  } else if (event.type === "step_completed") {
    const step = upsertStep(run, event.step, event.label);
    Object.assign(step, { status: "done", ms: event.duration_ms, attempts: event.attempts, db: event.db_queries,
      cache: event.cache || [], sql: event.sql || [], seq: event.checkpoint_seq, error: event.attempts > 1 ? step.error : null });
  } else if (event.type === "step_restored") {
    const step = upsertStep(run, event.step, event.label);
    Object.assign(step, { status: "restored", ms: event.original_ms, db: event.db_queries, cache: event.cache || [], sql: event.sql || [] });
  } else if (event.type === "waiting") {
    run.waitingNote = event.done ? "" : event.note;
  } else if (event.type === "step_failed") {
    const step = upsertStep(run, event.step, event.label);
    Object.assign(step, { status: "failed", error: event.error, attempts: event.attempts });
  } else if (event.type === "run_finished") {
    run.status = event.status;
    run.error = event.error || null;
    closeSource();
    loadSessions();
    if (event.status === "completed") {
      if (event.context) { state.context = event.context; renderContext(); }
      if (event.message) appendMessages([event.message]);
      state.busy = false;
      setComposerBusy(false);
      loadRunDetail(run.run_id);
    } else {
      state.busy = false;
      setComposerBusy(false);
    }
    loadSystem();
    if (state.activeTab === "memory") loadMemory();
  }
  renderLiveRun();
  renderRunPanel();
  renderDataRun();
}

function subscribe(runId) {
  closeSource();
  const source = new EventSource(`/api/runs/${encodeURIComponent(runId)}/events`);
  source.onmessage = (e) => handleEvent(JSON.parse(e.data));
  source.onerror = () => {
    source.close();
    if (state.source === source) state.source = null;
    if (state.run && state.run.status === "running") window.dispatchEvent(new Event("agent:offline"));
  };
  state.source = source;
}

function closeSource() {
  if (state.source) { state.source.close(); state.source = null; }
}

function renderLiveRun() {
  const thread = $("#thread");
  let live = $("#live-run");
  const run = state.run;
  if (!run || run.status === "completed") { live?.remove(); return; }
  if (!live) {
    $("#welcome")?.remove();
    live = document.createElement("div");
    live.id = "live-run";
    live.className = "msg agent";
    thread.appendChild(live);
  }
  const halted = ["interrupted", "failed"].includes(run.status);
  const visible = run.steps.filter((s) => s.status !== "pending" || !s.name.startsWith("menu:"));
  const menuSteps = run.steps.filter((s) => s.name.startsWith("menu:"));
  const menusDone = menuSteps.filter((s) => ["done", "restored"].includes(s.status)).length;
  const rows = visible.map((s) => {
    if (s.name.startsWith("menu:") && ["done", "restored"].includes(s.status) && menuSteps.length > 2) return "";
    return `<div class="live-step ${s.status}">${stepIcon(s.status)}<span>${esc(s.label)}${s.status === "retry" ? ` <span class="badge badge-retry">retry ${s.retries}</span>` : ""}${s.status === "restored" ? ' <span class="badge badge-restored">from checkpoint</span>' : ""}</span>
      <span class="t">${s.ms !== undefined && s.ms !== null && s.status === "done" ? `${fmt(s.ms)} ms` : ""}</span></div>`;
  }).join("");
  const negLine = menuSteps.length > 2 ? `<div class="live-collapsed">Zomato: ${menusDone} / ${menuSteps.length} menus read</div>` : "";
  const waiting = run.waitingNote ? `<div class="note" style="margin-top:6px">${ICON.alert}${esc(run.waitingNote)}</div>` : "";
  const failed = run.steps.find((s) => s.status === "failed");
  const halt = halted ? `<div class="card-section">
      <div class="halt-title">${ICON.alert}Run halted at “${esc(failed?.label || run.error || "a step")}”</div>
      <div class="meta-line" style="margin:4px 0 10px">${esc(failed?.error || run.error || "")}. Completed steps are checkpointed and will not run again.</div>
      <button class="btn btn-primary" data-resume="${esc(run.run_id)}">${ICON.play}Resume from checkpoint</button></div>` : "";
  live.innerHTML = `<div class="avatar">${blob()}</div><div class="msg-body"><div class="card live-run ${halted ? "halt-card" : ""}">
    ${halt}
    <div class="card-section"><div class="eyebrow">${halted ? "Checkpoint timeline" : `Agent working${run.resumeCount ? " · resumed" : ""}`}</div>
      <div class="live-steps">${rows}</div>${negLine}${waiting}
      ${halted ? "" : '<div class="typing" style="margin-top:6px"><i></i><i></i><i></i></div>'}</div></div></div>`;
  scrollThread();
}

function renderRunPanel() {
  const panel = $("#run-panel");
  const run = state.run;
  $('.tab[data-tab="run"]').dataset.status = run ? run.status : "";
  if (!run) {
    panel.innerHTML = '<div class="panel-empty">Send a request to see each pipeline step being executed and checkpointed.</div>';
    return;
  }
  const saved = run.steps.filter((s) => ["done", "restored"].includes(s.status)).length;
  const rows = run.steps.map((s) => {
    const parts = [];
    if (s.status === "restored") parts.push("restored from checkpoint, not re-executed");
    if (s.attempts > 1) parts.push(`succeeded on attempt ${s.attempts}`);
    if (s.db !== undefined && s.db !== null && s.status === "done") parts.push(`${s.db} DB quer${s.db === 1 ? "y" : "ies"}`);
    const hits = s.cache.filter((c) => c.hit).length;
    if (s.cache.length && s.status === "done") parts.push(`cache ${hits}/${s.cache.length}`);
    const error = s.error && ["failed", "retry"].includes(s.status) || (s.error && s.attempts > 1) ? `<div class="detail err">${esc(s.error)}</div>` : "";
    return `<li class="tl-row st-row-${s.status}">${stepIcon(s.status)}
      <div><div class="name">${esc(s.label)}${s.status === "restored" ? '<span class="badge badge-restored">restored</span>' : ""}${s.retries ? `<span class="badge badge-retry">${s.retries} retr${s.retries === 1 ? "y" : "ies"}</span>` : ""}</div>
        ${parts.length ? `<div class="detail">${esc(parts.join(" · "))}</div>` : ""}${error}</div>
      <div class="right">${s.ms !== undefined && s.ms !== null && s.status !== "pending" ? `${fmt(s.ms, 1)} ms` : ""}${s.seq ? `<br>cp #${s.seq}` : ""}</div></li>`;
  }).join("");
  const halted = ["interrupted", "failed"].includes(run.status);
  panel.innerHTML = `<section class="block">
      <div class="run-status"><span class="mono">${esc(run.run_id)}</span><span class="status-tag status-${run.status}">${esc(run.status)}</span></div>
      <div class="run-msg">“${esc(run.message)}”</div>
      <div class="kpis">
        <div class="kpi"><div class="n">${saved}<small> / ${run.steps.length}</small></div><div class="l">steps checkpointed</div></div>
        <div class="kpi"><div class="n">${run.resumeCount}</div><div class="l">${run.resumeCount === 1 ? "time" : "times"} resumed</div></div>
      </div>
      ${halted ? `<div class="resume-box"><p>${esc(run.error || "The run stopped.")}</p><button class="btn btn-primary" data-resume="${esc(run.run_id)}">${ICON.play}Resume from checkpoint</button></div>` : ""}
    </section>
    <section class="block"><div class="block-head"><h3>Step timeline</h3><span class="hint">saved to SQLite after each step</span></div>
      <ol class="timeline">${rows}</ol></section>`;
}

function renderDataRun() {
  const box = $("#data-run");
  const run = state.run;
  if (!run) { box.innerHTML = ""; return; }
  const executed = run.steps.filter((s) => ["done", "restored"].includes(s.status));
  const queries = executed.reduce((n, s) => n + (s.db || 0), 0);
  const cache = executed.flatMap((s) => s.cache || []);
  const hits = cache.filter((c) => c.hit).length;
  const zomato = cache.filter((c) => ["search", "menu"].includes(c.cache));
  const reads = zomato.filter((c) => !c.hit).length;
  const sql = executed.flatMap((s) => (s.sql || []).map((q) => ({ ...q, step: s.label })));
  box.innerHTML = `<section class="block">
    <div class="block-head"><h3>Last turn</h3><span class="hint mono">${esc(run.run_id)}</span></div>
    <div class="kpis">
      <div class="kpi ${reads < zomato.length ? "good" : ""}"><div class="n">${reads}<small> / ${zomato.length}</small></div><div class="l">Zomato pages read (rest reused)</div></div>
      <div class="kpi ${hits ? "good" : ""}"><div class="n">${hits}<small> / ${cache.length}</small></div><div class="l">cache hits</div></div>
      <div class="kpi"><div class="n">${queries}</div><div class="l">SQL statements</div></div>
    </div>
    <div class="sql-list">${sql.length ? sql.slice(0, 12).map((q) => `<div class="sql">${esc(q.sql)}<span class="m">${esc(q.step)} · ${q.rows} rows · ${q.duration_ms} ms</span></div>`).join("")
      : '<div class="note">No database writes this turn: everything came from cache.</div>'}</div>
    ${cache.length ? `<div class="chips" style="margin-top:10px">${cache.map((c) => `<span class="chip" title="${esc(c.key)}">${esc(c.cache)} · ${c.hit ? "hit" : "miss"}</span>`).join("")}</div>` : ""}
  </section>`;
}

function renderCaches() {
  const system = state.system;
  if (!system) return;
  $("#cache-table").innerHTML = `<table class="cache-table"><tr><th>Cache</th><th>Hit rate</th><th class="num">Size</th><th class="num">TTL</th></tr>
    ${system.caches.map((c) => `<tr><td><b>${esc(c.name)}</b><div class="hint">${c.hits} hits · ${c.misses} misses${c.evictions ? ` · ${c.evictions} evicted` : ""}</div></td>
      <td><span class="hit-bar"><i style="width:${(c.hit_rate * 100).toFixed(0)}%"></i></span>${(c.hit_rate * 100).toFixed(0)}%</td>
      <td class="num">${c.size}/${c.capacity}</td><td class="num">${c.ttl_seconds}s</td></tr>`).join("")}</table>
    <div class="note mono" style="margin-top:8px">Total SQL statements since start: ${fmt(system.database.total_queries)}</div>`;
  const plans = system.database.query_plans;
  const names = { dishes_by_query: "Dishes by search and price", price_history: "Price history of one dish" };
  $("#query-plans").innerHTML = Object.entries(plans).map(([name, rows]) =>
    `<div class="plan"><b>${esc(names[name] || name)}</b>${rows.map((r) => `<div class="${r.includes("INDEX") ? "idx" : ""}">${esc(r)}</div>`).join("")}</div>`).join("");
  const o = system.database.observations;
  $("#observation-count").textContent = `${o.restaurants} restaurants · ${o.dishes} dishes · ${o.price_points} prices`;
}

async function loadObservations() {
  try {
    const data = await api("/api/observations");
    $("#observations").innerHTML = data.dishes.length ? `<table class="cache-table"><tr><th>Dish</th><th class="num">Price</th><th>Seen</th></tr>
      ${data.dishes.map((d) => `<tr><td><b>${esc(d.name)}</b><div class="hint">${esc(d.restaurant_name)} · ${d.rating ?? "new"}★ · ${d.eta_low}–${d.eta_high} min · search "${esc(d.query)}"</div></td>
        <td class="num">Rs ${d.price}${d.price_changes > 1 ? `<div class="hint">${d.price_changes} prices</div>` : ""}</td><td class="hint">${esc((d.last_seen || "").replace("T", " ").slice(5, 16))}</td></tr>`).join("")}</table>`
      : '<div class="note">Nothing read yet. Send a request and the dishes the agent reads on Zomato appear here.</div>';
  } catch (e) { void e; }
}

function renderAgents() {
  const panel = $("#agents-panel");
  const detail = state.runDetail;
  if (!detail || !detail.search) {
    panel.innerHTML = '<div class="panel-empty">What the agent did on Zomato for the latest request appears here: the search, each menu it read, and screenshots.</div>';
    return;
  }
  const found = detail.restaurants.filter((r) => r.outcome === "Dishes found").length;
  const shots = detail.tool_calls.filter((t) => t.screenshot).map((t) => `<a class="shot" href="${esc(t.screenshot)}" target="_blank" title="${esc(t.url || "")}">
      <img src="${esc(t.screenshot)}" alt="${esc(t.detail)}" loading="lazy"><span>${esc(t.detail.length > 70 ? t.detail.slice(0, 70) + "…" : t.detail)}</span></a>`).join("");
  const order = { "Dishes found": 0, "Over budget": 1, "No match": 2, "Skipped": 3 };
  const cards = [...detail.restaurants].sort((a, b) => (order[a.outcome] ?? 9) - (order[b.outcome] ?? 9)).map((r) => `<div class="agent-card">
      <div class="nm">${esc(r.restaurant)}</div>
      <div class="oc ${r.outcome === "Dishes found" ? "ok" : "no"}"><b>${esc(r.outcome)}</b> · ${esc(r.detail)}</div></div>`).join("");
  const calls = detail.tool_calls.map((t) => `<div class="acl-msg ${t.cached ? "from-coord" : "from-agent"}"><div class="hd"><span class="mono">${esc(t.tool)}</span>
      <span class="perf ${t.cached ? "perf-inform" : "perf-propose"}">${t.cached ? "reused" : "browser"}</span></div>${esc(t.detail)}</div>`).join("");
  panel.innerHTML = `<section class="block"><div class="kpis">
      <div class="kpi"><div class="n">${detail.search.cards_read}</div><div class="l">restaurant cards read</div></div>
      <div class="kpi"><div class="n">${found}<small> / ${detail.search.shortlisted}</small></div><div class="l">menus with a matching dish</div></div>
      <div class="kpi"><div class="n">${detail.offers_count}</div><div class="l">dishes within budget</div></div>
    </div></section>
    <section class="block"><div class="block-head"><h3>Search</h3><span class="hint">filters: ${esc((detail.search.filters || []).join(", ") || "none")}${detail.search.reused ? " · reused" : ""}</span></div>
      <ol class="browser-actions">${(detail.search.actions || []).map((a) => `<li>${esc(a)}</li>`).join("")}</ol>
      ${shots ? `<div class="shots">${shots}</div>` : ""}</section>
    <section class="block"><div class="block-head"><h3>Restaurants</h3><span class="hint">${detail.restaurants.length} considered</span></div><div class="agent-grid">${cards}</div></section>
    <section class="block"><div class="block-head"><h3>Tool calls</h3><span class="hint">${detail.tool_calls.length}</span></div><div class="acl">${calls}</div></section>`;
}

function memoryRow(r, showSimilarity) {
  return `<div class="mem"><div class="hd"><span class="ty-${esc(r.memory_type)}">${esc(r.memory_type)} · <span class="mono">${esc(r.memory_id)}</span></span>
    <span class="${showSimilarity ? "sim" : ""}">${showSimilarity ? `sim ${r.similarity}` : esc((r.created_at || "").replace("T", " "))}</span></div>${esc(r.text)}</div>`;
}

async function loadMemory() {
  try {
    const data = await api("/api/memory");
    $("#memory-records").innerHTML = data.records.length
      ? `<div class="mem-list">${data.records.map((r) => memoryRow(r, false)).join("")}</div>`
      : '<div class="note">Empty. Confirm, decline or cancel a recommendation to store an outcome.</div>';
  } catch (e) { void e; }
}

async function searchMemory() {
  const q = $("#memory-query").value.trim();
  if (!q) return;
  const hits = await api(`/api/memory/search?q=${encodeURIComponent(q)}`);
  $("#memory-hits").innerHTML = hits.length ? `<div class="mem-list">${hits.map((h) => memoryRow(h, true)).join("")}</div>` : '<div class="note">No memories yet.</div>';
}

async function loadSystem() {
  try {
    state.system = await api("/api/system");
    renderModelStatus();
    renderLongTermStats();
    renderCaches();
    renderFaults(state.system.faults);
  } catch (e) { void e; }
}

async function loadRunDetail(runId) {
  try {
    state.runDetail = await api(`/api/runs/${encodeURIComponent(runId)}`);
    renderAgents();
  } catch (e) { void e; }
}

async function loadFaultSteps() {
  try {
    const steps = await api("/api/faults/steps");
    const current = $("#fault-step").value;
    $("#fault-step").innerHTML = steps.map((s) => `<option value="${esc(s.step)}">${esc(s.label)}</option>`).join("");
    $("#fault-step").value = steps.some((s) => s.step === current) ? current
      : (steps.find((s) => s.step.startsWith("menu:")) || steps.find((s) => s.step === "rank")).step;
  } catch (e) { void e; }
}

function renderFaults(faults) {
  if (!faults) return;
  const modes = $("#fault-modes");
  if (!modes.children.length) {
    modes.innerHTML = Object.entries(faults.modes).map(([mode, text], i) =>
      `<label class="radio-card"><input type="radio" name="fault-mode" value="${esc(mode)}" ${i === 1 ? "checked" : ""}><div><b>${esc(mode)}</b><span>${esc(text)}</span></div></label>`).join("");
  }
  $("#fault-list").innerHTML = faults.armed.map((f) => `<div class="fault-row"><span class="mono">${esc(f.step)}</span><span class="mode">${esc(f.mode)}</span></div>`).join("");
  $("#slow-motion").checked = faults.step_delay_seconds > 0;
}

function setComposerBusy(busy) {
  $("#send-btn").disabled = busy;
  $("#composer-input").placeholder = busy ? "The agent is working…" : "Ask the agent to order something, or refine the last request…";
}

async function sendMessage(text) {
  text = text.trim();
  if (!text || state.busy) return;
  if (!state.online) { toast("Server is offline"); return; }
  state.busy = true;
  setComposerBusy(true);
  appendMessages([{ role: "user", kind: "text", text }]);
  $("#composer-input").value = "";
  autosize();
  try {
    const address = $("#address-input").value.trim();
    const res = await api(`/api/sessions/${state.sessionId}/messages`, { method: "POST", body: { text, address } });
    state.context = res.context;
    renderContext();
    if (res.type === "run") {
      state.run = newRun(res.run_id, text);
      state.runDetail = null;
      renderLiveRun();
      renderRunPanel();
      renderDataRun();
      subscribe(res.run_id);
      announce("update");
      return;
    }
    appendMessages(res.messages);
    afterReply(res.intent);
    announce("update");
  } catch (e) {
    toast(e.message);
  }
  state.busy = state.run?.status === "running" && !!state.source;
  setComposerBusy(state.busy);
}

async function sendAction(action, button) {
  const card = button.closest(".rec-card");
  if (!card.dataset.key) card.dataset.key = `${card.dataset.run}:${card.dataset.item}:${crypto.randomUUID ? crypto.randomUUID().slice(0, 8) : Date.now()}`;
  $$("button", card).forEach((b) => { b.disabled = true; });
  try {
    const res = await api(`/api/sessions/${state.sessionId}/actions`, {
      method: "POST", body: { action }, headers: action === "approve" ? { "Idempotency-Key": card.dataset.key } : {},
    });
    if (res.duplicate) { toast(`Order ${res.order.order_id} already placed: duplicate request ignored`); return; }
    const label = { approve: "Add to Zomato cart", next: "Show next option", cancel: "Cancel" }[action];
    appendMessages([{ role: "user", kind: "action", text: label }]);
    state.context = res.context;
    renderContext();
    appendMessages(res.messages);
    afterReply(action);
    announce("update");
  } catch (e) {
    toast(e.message);
  } finally {
    $$("button", card).forEach((b) => { b.disabled = false; });
    refreshActionStates();
  }
}

function afterReply(intent) {
  if (["approve", "cancel", "next"].includes(intent)) {
    loadSystem();
    if (state.activeTab === "memory") loadMemory();
  }
}

async function resumeRun(runId) {
  try {
    await api(`/api/runs/${encodeURIComponent(runId)}/resume`, { method: "POST" });
    state.busy = true;
    setComposerBusy(true);
    subscribe(runId);
    announce("update");
    toast("Resuming from the last checkpoint");
  } catch (e) {
    toast(e.message);
  }
}

function applySessionView(view, persist = true) {
  state.sessionId = view.session_id;
  if (persist) storage.set(SESSION_KEY, view.session_id);
  state.context = view.context;
  state.previousConstraints = view.context.constraints ? { ...view.context.constraints } : null;
  state.messages = view.messages;
  closeSource();
  state.run = view.active_run ? runFromView(view.active_run) : null;
  state.runDetail = null;
  state.busy = state.run?.status === "running";
  setComposerBusy(state.busy);
  renderContext();
  renderThread();
  renderRunPanel();
  renderDataRun();
  renderAgents();
  if (state.run) {
    if (state.run.status === "running") subscribe(state.run.run_id);
    loadRunDetail(state.run.run_id);
  }
}

async function openSession(sessionId, persist = true) {
  const view = await api(`/api/sessions/${encodeURIComponent(sessionId)}`);
  applySessionView(view, persist);
  loadSessions();
}

async function newSession() {
  applySessionView(await api("/api/sessions", { method: "POST" }));
  announce("session");
  loadSessions();
}

async function clearSession() {
  if (!state.sessionId) return;
  try {
    const view = await api(`/api/sessions/${state.sessionId}/clear`, { method: "POST" });
    applySessionView(view);
    announce("session");
    loadSessions();
    toast(`Context cleared. New session ${view.session_id} · ${view.cleared.cache_entries_evicted} cached context entr${view.cleared.cache_entries_evicted === 1 ? "y" : "ies"} evicted`, 4200);
    loadSystem();
  } catch (e) {
    toast(e.message);
  }
}

function markOffline() {
  if (!state.online) return;
  state.online = false;
  $("#connection-banner").hidden = false;
  renderModelStatus();
}

async function backOnline() {
  state.online = true;
  $("#connection-banner").hidden = true;
  await loadSystem();
  if (state.sessionId) {
    try { await openSession(state.sessionId); } catch { await newSession(); }
  }
  toast(state.run && ["interrupted", "failed"].includes(state.run.status)
    ? "Server is back. The interrupted run can resume from its checkpoint." : "Server is back online", 4500);
}

async function loadSessions() {
  try {
    state.sessions = await api("/api/sessions");
    const picker = $("#session-picker");
    const ids = state.sessions.map((s) => s.session_id);
    if (state.sessionId && !ids.includes(state.sessionId)) ids.unshift(state.sessionId);
    picker.innerHTML = ids.map((id) => {
      const s = state.sessions.find((x) => x.session_id === id);
      const note = s ? `${s.status === "active" ? "" : " · closed"} · ${s.user_turns} ${s.user_turns === 1 ? "msg" : "msgs"}` : "";
      return `<option value="${esc(id)}">${esc(id)}${esc(note)}</option>`;
    }).join("");
    picker.value = state.sessionId;
  } catch (e) { void e; }
}

function announce(type) {
  broadcast(type, { session_id: state.sessionId, source: "admin" });
}

function autosize() {
  const el = $("#composer-input");
  el.style.height = "auto";
  el.style.height = `${Math.min(el.scrollHeight, 140)}px`;
}

const SECTIONS = {
  session: ["Session", "What the agent remembers in this conversation. Cleared by Clear cache · New session."],
  run: ["Checkpoints", "Each step is saved as it finishes. If a step fails, Resume continues from the last save instead of starting over."],
  data: ["Data layer", "Everything read from Zomato is stored with a timestamp. Searches and menus are reused for 5 minutes."],
  agents: ["Browser", "What the agent did on Zomato for the latest request: the search, each menu, and screenshots."],
  memory: ["Long-term memory", "Past orders and rejections, kept across sessions. They nudge future rankings up or down."],
  faults: ["Fault injection", "Break a step on purpose to see retry, halt and resume in action."],
};

function switchTab(name) {
  state.activeTab = name;
  $$(".tab[data-tab]").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  $$(".tab-body").forEach((b) => { b.hidden = b.dataset.body !== name; });
  $("#inspector-title").textContent = SECTIONS[name][0];
  $("#inspector-hint").textContent = SECTIONS[name][1];
  if (name === "memory") { loadMemory(); searchMemory().catch(() => {}); }
  if (name === "data" || name === "faults") { loadSystem(); loadObservations(); loadFaultSteps(); }
  if (name === "agents") renderAgents();
}

function toggleInspector(open) {
  const panel = $("#panel-inspector");
  const willOpen = open ?? !panel.classList.contains("open");
  panel.classList.toggle("open", willOpen);
  $("#scrim").hidden = !willOpen;
}

function bindUI() {
  $("#composer-form").addEventListener("submit", (e) => { e.preventDefault(); sendMessage($("#composer-input").value); });
  $("#composer-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(e.target.value); }
  });
  $("#composer-input").addEventListener("input", autosize);
  $("#suggestions").addEventListener("click", (e) => {
    const b = e.target.closest(".suggestion");
    if (b) sendMessage(b.textContent);
  });
  $("#thread").addEventListener("click", (e) => {
    const act = e.target.closest("[data-act]");
    if (act) return sendAction(act.dataset.act, act);
    const say = e.target.closest("[data-say]");
    if (say) return sendMessage(say.dataset.say);
    const resume = e.target.closest("[data-resume]");
    if (resume) return resumeRun(resume.dataset.resume);
  });
  $("#run-panel").addEventListener("click", (e) => {
    const resume = e.target.closest("[data-resume]");
    if (resume) resumeRun(resume.dataset.resume);
  });
  $("#clear-session").addEventListener("click", clearSession);
  $("#session-picker").addEventListener("change", (e) => openSession(e.target.value, false).catch((err) => toast(err.message)));
  $("#session-picker").addEventListener("focus", loadSessions);
  $$(".tab[data-tab]").forEach((t) => t.addEventListener("click", () => {
    switchTab(t.dataset.tab);
    if (window.matchMedia("(max-width: 1020px)").matches) toggleInspector(true);
  }));
  $("#flush-cache").addEventListener("click", async () => {
    const res = await api("/api/cache/flush", { method: "POST" });
    toast(`Flushed ${res.entries_dropped} cached entries. The next request reads Zomato again.`);
    loadSystem();
  });
  $("#memory-search").addEventListener("click", () => searchMemory().catch((e) => toast(e.message)));
  $("#memory-query").addEventListener("keydown", (e) => { if (e.key === "Enter") searchMemory().catch(() => {}); });
  $("#memory-reset").addEventListener("click", async () => {
    if (!confirm("Delete every long-term memory? Past orders and rejections will no longer influence ranking.")) return;
    await api("/api/memory", { method: "DELETE" });
    toast("Long-term memory cleared");
    loadMemory();
    loadSystem();
  });
  $("#fault-arm").addEventListener("click", async () => {
    const step = $("#fault-step").value;
    const mode = $("input[name=fault-mode]:checked")?.value;
    if (!step || !mode) return;
    const faults = await api("/api/faults", { method: "POST", body: { step, mode } });
    renderFaults(faults);
    toast(`Armed ${mode} fault at ${step}. Send a request to trigger it.`);
  });
  $("#fault-clear").addEventListener("click", async () => renderFaults(await api("/api/faults", { method: "DELETE" })));
  $("#slow-motion").addEventListener("change", async (e) => {
    renderFaults(await api("/api/faults/delay", { method: "POST", body: { seconds: e.target.checked ? 0.8 : 0 } }));
  });
  $("#toggle-inspector").addEventListener("click", () => toggleInspector());
  $("#scrim").addEventListener("click", () => toggleInspector(false));
}

async function boot() {
  $("#rail-logo").innerHTML = blob();
  bindUI();
  switchTab("session");
  renderRunPanel();
  await loadSystem();
  loadFaultSteps();
  const saved = storage.get(SESSION_KEY);
  try {
    if (saved) await openSession(saved); else await newSession();
  } catch {
    try { await newSession(); } catch { window.dispatchEvent(new Event("agent:offline")); }
  }
  if (sessionChannel) {
    sessionChannel.onmessage = (e) => {
      const msg = e.data;
      if (msg.source === "admin") return;
      if (msg.type === "session" || msg.session_id === state.sessionId) {
        openSession(msg.session_id).then(() => { if (msg.type === "session") toast(`Following new customer session ${msg.session_id}`); }).catch(() => {});
        loadSystem();
      }
    };
  }
  window.addEventListener("agent:offline", markOffline);
  watchHealth(markOffline, backOnline);
}

boot();
