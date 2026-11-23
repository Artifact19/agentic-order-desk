const PHASES = [
  { key: "understand", label: "Understanding you", match: (n) => n === "understand" },
  { key: "recall", label: "Checking your taste", match: (n) => n === "recall" },
  { key: "connect", label: "Connecting to Zomato", match: (n) => n === "connect" },
  { key: "search", label: "Searching Zomato", match: (n) => n === "search" },
  { key: "menu", label: "Reading menus", match: (n) => n.startsWith("menu:") },
  { key: "pick", label: "Picking the best", match: (n) => n === "rank" || n === "explain" },
];

const ACTION_LABELS = { "Add to Zomato cart": "Add to Zomato cart", "Show next option": "Show me another", "Cancel": "Cancel" };

const PICKS = [
  { text: "Veg pizza under ₹300 within 40 minutes", hint: "Live from Zomato, Pure Veg filter on", icon: "bowl" },
  { text: "Same as last time", hint: "Reorder your usual in one tap", icon: "blob" },
  { text: "Chicken biryani under ₹400 within 45 minutes", hint: "Spicy, slow-cooked and filling", icon: "chef" },
  { text: "Cold coffee in 20 minutes", hint: "Any dish works, even ones without a Zomato dish page", icon: "clock" },
];

const CHEVRON = '<svg class="chev" viewBox="0 0 24 24"><path d="m9 6 6 6-6 6"/></svg>';

const ui = { sessionId: null, context: null, messages: [], run: null, source: null, busy: false, address: null };

const rupees = (n) => `₹${fmt(n)}`;
const friendly = (text) => String(text || "").replace(/restaurant agents?/gi, "restaurants").replace(/Rs (\d)/g, "₹$1");
const title = (s) => (s ? s[0].toUpperCase() + s.slice(1) : "");

function heroHtml() {
  const crew = BLOB_CREW.map((b) => `<span>${blob(b.color, b.shape)}</span>`).join("");
  const picks = PICKS.map((p) => `<button class="pick${p.icon === "blob" ? " featured" : ""}" type="button" data-say="${esc(p.text)}">
      <span class="ic">${p.icon === "blob" ? blob() : ICON[p.icon]}</span>
      <span class="txt"><b>${esc(p.text)}</b><small>${esc(p.hint)}</small></span>${CHEVRON}</button>`).join("");
  return `<section class="hero" id="hero">
    <div class="crew" aria-hidden="true">${crew}</div>
    <h1>Hungry? Tell us what <em>you're craving.</em></h1>
    <p>Our agent opens Zomato in Chrome, searches and filters for you, reads real menus and suggests the best match. You check out yourself; it never pays.</p>
    <div class="picks">${picks}</div>
    <div class="how">
      <span>${ICON.sparkle}Remembers your follow-ups</span>
      <span>${ICON.tag}Live Zomato prices and offers</span>
      <span>${ICON.check}You pay, never the agent</span>
    </div>
  </section>`;
}

function whoHtml() {
  return `<div class="who">${blob()}</div>`;
}

function dishCard(m) {
  const p = m.payload;
  const s = p.selection;
  const saved = s.list_price - s.price;
  const highlights = (p.highlights || []).map((h) => `<li>${ICON.check}<span>${esc(friendly(h))}</span></li>`).join("");
  const others = p.ranking.slice(1).map((r) => `<div class="other"><div>${esc(r.item_name)}<small>${esc(r.restaurant_name.split(" (")[0])}</small></div>
      <div class="p">${rupees(r.price)}</div><div class="t">${esc(r.eta_range || `${r.eta_minutes} min`)}</div></div>`).join("");
  const live = true;
  return `<div class="card dish" data-run="${esc(p.run_id)}" data-item="${esc(s.item_id)}">
    <div class="dish-top">
      <div>
        <div class="eyebrow">${ICON.sparkle}${p.option_no > 1 ? "Another option for you" : "Our pick for you"}</div>
        <div class="dish-name">${esc(s.item_name)}</div>
        <div class="dish-from">${esc(s.restaurant_name)}${live ? ` · <a href="${esc(s.url)}" target="_blank" rel="noopener">view on Zomato</a>` : ""}</div>
      </div>
      <div class="dish-price">
        <div class="now">${rupees(s.price)}</div>
        ${saved > 0 ? `<div class="was">${rupees(s.list_price)}</div><span class="save">Save ${rupees(saved)}</span>` : ""}
      </div>
    </div>
    <div class="facts">
      <span>${ICON.clock}${esc(s.eta_range || `${s.eta_minutes} min`)} delivery</span>
      <span>${ICON.star}${s.rating} rating</span>
      ${live && s.offer_text ? `<span>${ICON.tag}${esc(s.offer_text)}</span>` : ""}
      ${live ? `<span class="zomato-badge">Live · Zomato</span>` : ""}
    </div>
    ${highlights ? `<div class="why"><div class="why-title">Why we picked this</div><ul>${highlights}</ul></div>` : ""}
    ${others ? `<details class="others"><summary>See ${p.ranking.length - 1} other option${p.ranking.length === 2 ? "" : "s"}</summary>${others}</details>` : ""}
    <div class="dish-actions">
      <button class="btn btn-primary btn-big" data-act="approve">${ICON.check}${live ? "Add to Zomato cart" : "Order it"} · ${rupees(s.price)}</button>
      <button class="btn btn-outline btn-big" data-act="next">${ICON.rotate}Show me another</button>
      <span class="grow"></span>
      <button class="btn btn-text" data-act="cancel">Cancel</button>
    </div>
  </div>`;
}

function orderCard(m, animate) {
  const p = m.payload;
  const icons = [ICON.chef, ICON.bike, ICON.box, ICON.party];
  const live = true;
  const shot = p.handoff && p.handoff.screenshot ? `<a href="${esc(p.handoff.screenshot)}" target="_blank"><img class="handoff-shot" src="${esc(p.handoff.screenshot)}" alt="Zomato page after the hand-off"></a>` : "";
  const steps = p.tracking.map((t, i) => `<div class="step${animate ? "" : " on"}${t.startsWith("Check this:") ? " warn" : ""}"><span class="dot">${icons[Math.min(i, icons.length - 1)]}</span><span>${esc(t)}</span></div>`).join("");
  const bill = p.handoff && p.handoff.bill;
  const billRows = bill ? [["Item total", bill.item_total], ["Delivery fee" + (bill.distance_km ? ` (${bill.distance_km} km)` : ""), bill.delivery_fee],
    ["Taxes", bill.taxes], ["Zomato savings", bill.savings], ["Delivery time", bill.delivery_time], ["Deliver to", bill.deliver_to]]
    .filter(([, v]) => v).map(([k, v]) => `<div class="bill-row"><span>${esc(k)}</span><span>${esc(v)}</span></div>`).join("") : "";
  const total = live && bill && bill.to_pay ? bill.to_pay : rupees(p.selection.price);
  return `<div class="card done-card">
    <div class="done-head"><h3>${ICON.check}${live ? (p.handoff && p.handoff.checkout_open ? "Zomato checkout is open" : "Handed off to Zomato") : "Order placed!"}</h3><span class="oid">${esc(p.order.order_id)}</span></div>
    <div class="done-body">
      <div class="done-sum"><span><b>${esc(p.selection.item_name)}</b> · ${esc(p.selection.restaurant_name.split(" (")[0])}</span><span>${live ? "To pay on Zomato" : "Paid"} <b>${esc(total)}</b></span></div>
      ${billRows ? `<div class="bill">${billRows}</div>` : ""}
      <div class="steps">${steps}</div>${shot}
      <div class="remember">${ICON.sparkle}We'll remember this for next time.</div>
    </div>
  </div>`;
}

function explainHtml(m) {
  const list = (m.payload.highlights || []).map((h) => `<li>${ICON.check}<span>${esc(friendly(h))}</span></li>`).join("");
  const item = m.payload.item_name;
  return `<div class="bubble"><div style="margin-bottom:8px">Here's why ${item ? `<b>${esc(item)}</b>` : "this"} came out on top:</div>
    <div class="why" style="margin:0"><ul>${list}</ul></div></div>`;
}

function renderMessage(m, animate) {
  const row = document.createElement("div");
  if (m.role === "user") {
    row.className = `row me${m.kind === "action" ? " action" : ""}`;
    row.innerHTML = `<div class="bubble">${esc(m.kind === "action" ? ACTION_LABELS[m.text] || m.text : m.text)}</div>`;
  } else {
    row.className = "row them";
    let body;
    if (m.kind === "recommendation") body = dishCard(m);
    else if (m.kind === "order") body = orderCard(m, animate);
    else if (m.kind === "explain") body = explainHtml(m);
    else if (["no_offer", "exhausted", "cancelled"].includes(m.kind)) body = `<div class="soft">${esc(friendly(m.text))}</div>`;
    else body = `<div class="bubble">${esc(friendly(m.text))}</div>`;
    row.innerHTML = `${whoHtml()}<div class="stack">${body}</div>`;
    if (animate && m.kind === "order") {
      setTimeout(() => $$(".step", row).forEach((s, i) => setTimeout(() => s.classList.add("on"), i * 700)), 150);
    }
  }
  if (!animate) row.classList.add("still");
  return row;
}

function renderFeed() {
  const feed = $("#feed");
  feed.innerHTML = ui.messages.length ? "" : heroHtml();
  ui.messages.forEach((m) => feed.appendChild(renderMessage(m, false)));
  renderWorking();
  refreshCards();
  scrollDown();
}

function append(list) {
  $("#hero")?.remove();
  const feed = $("#feed");
  const working = $("#working");
  list.forEach((m) => {
    ui.messages.push(m);
    const node = renderMessage(m, true);
    if (working) feed.insertBefore(node, working); else feed.appendChild(node);
  });
  refreshCards();
  scrollDown();
}

function scrollDown() {
  const main = $("#main");
  requestAnimationFrame(() => { main.scrollTop = main.scrollHeight; });
}

function refreshCards() {
  const pending = ui.context?.pending;
  const cards = $$(".dish");
  const live = cards.filter((c) => pending && c.dataset.run === pending.run_id && c.dataset.item === pending.selection.item_id).pop();
  cards.forEach((card) => {
    const isLive = card === live;
    $(".dish-actions", card).hidden = !isLive;
    card.classList.toggle("past", !isLive);
  });
}

function renderLooking() {
  const c = ui.context?.constraints;
  const box = $("#looking");
  if (!c) { box.hidden = true; return; }
  const declined = ui.context.rejected_items.map((r) => `<span class="pill muted">${esc(r.item_name.split(" (")[0])}</span>`).join("");
  box.innerHTML = `<b>Looking for</b>
    <span class="pill">${c.is_veg ? "Veg" : "Non-veg"} ${esc(title(c.item))}</span>
    <span class="pill">Under ${rupees(c.max_price)}</span>
    <span class="pill">Within ${c.max_eta_minutes} min</span>${declined}`;
  box.hidden = false;
}

function renderChips() {
  let chips;
  if (ui.context?.pending) chips = ["Yes, order it", "Show me another", "Why this one?", "Biryani instead", "Under ₹300", "Make it non-veg"];
  else if (ui.context?.constraints) chips = ["Try pasta instead", "Raise the budget", "No rush, I can wait", "What did I ask for?"];
  else chips = ui.messages.length ? PICKS.map((p) => p.text) : [];
  $("#chips").innerHTML = chips.map((t) => `<button class="chip" type="button" data-say="${esc(t)}">${esc(t)}</button>`).join("");
}

function renderHeader() {
  const address = ui.address || ui.context?.address || "";
  $("#address-text").textContent = address.split(",")[0] || "Set address";
  $("#address-chip").title = address;
}

function renderContextBits() {
  renderLooking();
  renderChips();
  renderHeader();
  refreshCards();
}

function phaseIndex(name) {
  return PHASES.findIndex((p) => p.match(name || ""));
}

function renderWorking() {
  const run = ui.run;
  let box = $("#working");
  if (!run || run.status === "completed") { box?.remove(); return; }
  if (!box) {
    $("#hero")?.remove();
    box = document.createElement("div");
    box.id = "working";
    box.className = "row them";
    $("#feed").appendChild(box);
  }
  if (["interrupted", "failed"].includes(run.status) && run.failedStep === "connect") {
    box.innerHTML = `${whoHtml()}<div class="stack"><div class="card halted">
      <h3>Waiting for you on Zomato</h3>
      <p>Sign in and choose your delivery address in the agent's Chrome window, then continue. Nothing has been searched yet.</p>
      <div class="zomato-wait-actions"><button class="btn btn-primary" data-resume="${esc(run.run_id)}">${ICON.play}I'm done, continue</button>
      <button class="btn btn-outline" data-zomato="connect">Show the Chrome window</button></div>
    </div></div>`;
    scrollDown();
    return;
  }
  if (["interrupted", "failed"].includes(run.status)) {
    const phase = PHASES[phaseIndex(run.failedStep)] || PHASES[0];
    box.innerHTML = `${whoHtml()}<div class="stack"><div class="card halted">
      <h3>We hit a connection problem</h3>
      <p>Something interrupted us while ${esc(phase.label.toLowerCase())}. Everything done so far is saved, so we'll pick up exactly where we stopped.</p>
      <button class="btn btn-primary" data-resume="${esc(run.run_id)}">${ICON.play}Continue where we left off</button>
    </div></div>`;
    scrollDown();
    return;
  }
  const total = Math.max(run.steps.length, 1);
  const done = run.done.size;
  const current = phaseIndex(run.current);
  const menuSteps = run.steps.filter((n) => n.startsWith("menu:"));
  const menusDone = menuSteps.filter((n) => run.done.has(n)).length;
  const waiting = run.current === "connect" && run.waitingNote;
  const subtitle = waiting ? run.waitingNote : PHASES[current]?.key === "menu" && menuSteps.length
    ? `Read ${menusDone} of ${menuSteps.length} menus so far`
    : (run.resumed ? "Picking up from where we stopped" : "This usually takes a few seconds");
  const phases = PHASES.map((p, i) => `<span class="phase ${i === current ? "on" : i < current ? "done" : ""}">${i < current ? "✓ " : ""}${esc(p.label)}</span>`).join("");
  box.innerHTML = `${whoHtml()}<div class="stack"><div class="card working">
    <div class="working-title"><span class="spinner"></span>${esc(PHASES[Math.max(current, 0)].label)}…</div>
    <div class="working-sub">${esc(subtitle)}</div>
    ${waiting ? `<div class="zomato-wait-actions"><button class="btn btn-primary" data-zomato="connect">Show the Chrome window</button>
      <button class="btn btn-text" data-zomato="skip">Continue without signing in</button></div>` : ""}
    <div class="progress"><i style="width:${Math.round((done / total) * 100)}%"></i></div>
    <div class="phases">${phases}</div>
  </div></div>`;
  scrollDown();
}

function startRun(runId) {
  ui.run = { run_id: runId, status: "running", steps: ["understand", "recall", "connect", "search", "rank", "explain"], done: new Set(), current: "understand", resumed: false };
  renderWorking();
  subscribe(runId);
}

function runFromView(view) {
  return { run_id: view.run_id, status: view.status, steps: view.planned_steps.map((s) => s.name),
    done: new Set(view.completed_steps), current: view.current_step, failedStep: view.current_step, resumed: view.resume_count > 0 };
}

function onEvent(e) {
  const run = ui.run;
  if (!run) return;
  if (e.type === "run_started") {
    run.status = "running";
    if (e.resumed) { run.resumed = true; run.done = new Set(); }
  } else if (e.type === "plan") {
    run.steps = e.steps.map((s) => s.name);
  } else if (e.type === "step_started") {
    run.current = e.step;
  } else if (e.type === "step_completed" || e.type === "step_restored") {
    run.done.add(e.step);
  } else if (e.type === "waiting") {
    run.waitingNote = e.done ? "" : e.note;
    if (!e.done) refreshZomato();
  } else if (e.type === "step_failed") {
    run.failedStep = e.step;
  } else if (e.type === "run_finished") {
    run.status = e.status;
    closeStream();
    setBusy(false);
    if (e.status === "completed") {
      if (e.context) { ui.context = e.context; renderContextBits(); }
      if (e.message) append([e.message]);
    } else {
      run.failedStep = e.step || run.failedStep;
    }
  }
  renderWorking();
}

function subscribe(runId) {
  closeStream();
  const source = new EventSource(`/api/runs/${encodeURIComponent(runId)}/events`);
  source.onmessage = (msg) => onEvent(JSON.parse(msg.data));
  source.onerror = () => {
    source.close();
    if (ui.source === source) ui.source = null;
    if (ui.run?.status === "running") window.dispatchEvent(new Event("agent:offline"));
  };
  ui.source = source;
}

function closeStream() {
  if (ui.source) { ui.source.close(); ui.source = null; }
}

function setBusy(busy) {
  ui.busy = busy;
  $("#send").disabled = busy;
  $("#message").placeholder = busy ? "Hang on, our agents are on it…" : "What are you craving? e.g. veg pizza under ₹400";
}

function announce(type) {
  broadcast(type, { session_id: ui.sessionId, source: "customer" });
}

async function send(text) {
  text = text.trim();
  if (!text || ui.busy) return;
  setBusy(true);
  append([{ role: "user", kind: "text", text }]);
  $("#message").value = "";
  try {
    const res = await api(`/api/sessions/${ui.sessionId}/messages`, { method: "POST", body: { text, address: ui.address || undefined } });
    ui.address = null;
    ui.context = res.context;
    renderContextBits();
    if (res.type === "run") { startRun(res.run_id); announce("update"); return; }
    append(res.messages);
    announce("update");
  } catch (e) {
    toast(e.status === 409 ? "Still working on your last message — one moment." : e.message);
  }
  setBusy(ui.run?.status === "running" && !!ui.source);
}

async function act(action, button) {
  const card = button.closest(".dish");
  if (!card.dataset.key) card.dataset.key = `${card.dataset.run}:${card.dataset.item}:${crypto.randomUUID ? crypto.randomUUID().slice(0, 8) : Date.now()}`;
  $$("button", card).forEach((b) => { b.disabled = true; });
  try {
    const res = await api(`/api/sessions/${ui.sessionId}/actions`, {
      method: "POST", body: { action }, headers: action === "approve" ? { "Idempotency-Key": card.dataset.key } : {},
    });
    if (res.duplicate) { toast("Already ordered — you won't be charged twice."); return; }
    append([{ role: "user", kind: "action", text: { approve: "Add to Zomato cart", next: "Show next option", cancel: "Cancel" }[action] }]);
    ui.context = res.context;
    renderContextBits();
    append(res.messages);
    announce("update");
  } catch (e) {
    toast(e.message);
  } finally {
    $$("button", card).forEach((b) => { b.disabled = false; });
    refreshCards();
  }
}

async function resume(runId) {
  try {
    await api(`/api/runs/${encodeURIComponent(runId)}/resume`, { method: "POST" });
    ui.run.status = "running";
    ui.run.resumed = true;
    setBusy(true);
    renderWorking();
    subscribe(runId);
    announce("update");
  } catch (e) {
    toast(e.message);
  }
}

function applyView(view) {
  ui.sessionId = view.session_id;
  storage.set(SESSION_KEY, view.session_id);
  ui.context = view.context;
  ui.messages = view.messages;
  closeStream();
  ui.run = view.active_run && view.active_run.status !== "completed" ? runFromView(view.active_run) : null;
  setBusy(ui.run?.status === "running");
  renderFeed();
  renderContextBits();
  if (ui.run?.status === "running") subscribe(ui.run.run_id);
}

async function openSession(id) {
  applyView(await api(`/api/sessions/${encodeURIComponent(id)}`));
}

async function newChat() {
  try {
    const current = ui.sessionId;
    const view = current
      ? await api(`/api/sessions/${current}/clear`, { method: "POST" })
      : await api("/api/sessions", { method: "POST" });
    applyView(view);
    announce("session");
    toast("Fresh start — the previous conversation was cleared.");
  } catch (e) {
    toast(e.message);
  }
}

function bind() {
  $("#composer").addEventListener("submit", (e) => { e.preventDefault(); send($("#message").value); });
  document.addEventListener("click", (e) => {
    const say = e.target.closest("[data-say]");
    if (say) return send(say.dataset.say);
    const a = e.target.closest("[data-act]");
    if (a) return act(a.dataset.act, a);
    const r = e.target.closest("[data-resume]");
    if (r) return resume(r.dataset.resume);
    const z = e.target.closest("[data-zomato]");
    if (z) return z.dataset.zomato === "skip" ? skipZomatoLogin() : connectZomato();
  });
  $("#new-chat").addEventListener("click", newChat);
  $("#zomato-chip").addEventListener("click", () => (ui.zomato?.ready ? refreshZomato(true) : connectZomato()));
  $("#address-chip").addEventListener("click", () => {
    const editor = $("#address-editor");
    editor.hidden = !editor.hidden;
    if (!editor.hidden) { $("#address-input").value = ui.address || ui.context?.address || ""; $("#address-input").focus(); }
  });
  $("#address-save").addEventListener("click", () => {
    const value = $("#address-input").value.trim();
    if (value) { ui.address = value; renderHeader(); toast("Got it — we'll deliver there."); }
    $("#address-editor").hidden = true;
  });
  if (sessionChannel) {
    sessionChannel.onmessage = (e) => {
      const msg = e.data;
      if (msg.source !== "admin") return;
      if (msg.type === "session" || msg.session_id === ui.sessionId) openSession(msg.session_id).catch(() => {});
    };
  }
}

function renderZomato() {
  const z = ui.zomato || {};
  const chip = $("#zomato-chip");
  chip.classList.toggle("ready", !!z.ready);
  let text;
  if (!z.browser_running) text = "Zomato · Connect";
  else if (z.skipped && !z.logged_in) text = "Zomato · browsing signed out";
  else if (!z.logged_in) text = "Zomato · Sign in";
  else if (!z.address_set) text = "Zomato · Set address";
  else text = `Zomato · ${z.location.replace(/^(Home|Work|Other)\s*-\s*/i, "").split(",")[0]}`;
  $("#zomato-text").textContent = text;
  chip.title = z.logged_in ? `Signed in to Zomato${z.user ? ` as ${z.user}` : ""}; delivering to ${z.location || "?"}` :
    "Opens the agent's Chrome window so you can sign in and choose your address";
}

async function refreshZomato(refresh = false) {
  try {
    ui.zomato = await api(`/api/zomato/status${refresh ? "?refresh=1" : ""}`);
    renderZomato();
  } catch (e) { /* status is best effort */ }
}

async function connectZomato() {
  toast("Opening the agent's Chrome window: sign in and choose your delivery address there.");
  try {
    ui.zomato = await api("/api/zomato/connect", { method: "POST" });
    renderZomato();
  } catch (e) {
    toast(e.message);
  }
}

async function skipZomatoLogin() {
  await api("/api/zomato/skip-login", { method: "POST" });
  toast("Continuing without a Zomato sign-in. Delivery times will be for Zomato's default location, not your address.");
}

async function boot() {
  $("#brand-mark").innerHTML = blob();
  bind();
  refreshZomato();
  setInterval(() => { if (!document.hidden) refreshZomato(); }, 4000);
  const saved = storage.get(SESSION_KEY);
  const start = async () => {
    try {
      if (saved && !ui.sessionId) await openSession(saved);
      else if (ui.sessionId) await openSession(ui.sessionId);
      else applyView(await api("/api/sessions", { method: "POST" }));
    } catch (e) {
      if (e.status) applyView(await api("/api/sessions", { method: "POST" }));
    }
  };
  await start();
  watchHealth(() => { $("#offline").hidden = false; }, async () => { $("#offline").hidden = true; await start(); });
}

boot();
