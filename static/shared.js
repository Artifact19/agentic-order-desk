const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const fmt = (n, digits = 0) => Number(n ?? 0).toLocaleString("en-IN", { maximumFractionDigits: digits });
const signed = (n) => (n > 0 ? "+" : "") + fmt(n, 1);

const ICON = {
  bot: '<svg viewBox="0 0 24 24"><rect x="4" y="8" width="16" height="12" rx="3"/><path d="M12 4v4M9 13h.01M15 13h.01"/></svg>',
  check: '<svg viewBox="0 0 24 24"><path d="M5 12l5 5L20 7"/></svg>',
  x: '<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18"/></svg>',
  clock: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
  star: '<svg viewBox="0 0 24 24"><path d="m12 3 2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z"/></svg>',
  pin: '<svg viewBox="0 0 24 24"><path d="M12 21s-7-6.2-7-11a7 7 0 1 1 14 0c0 4.8-7 11-7 11z"/><circle cx="12" cy="10" r="2.5"/></svg>',
  tag: '<svg viewBox="0 0 24 24"><path d="M3 12V4h8l10 10-8 8z"/><circle cx="7.5" cy="7.5" r="1.5"/></svg>',
  rotate: '<svg viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/></svg>',
  alert: '<svg viewBox="0 0 24 24"><path d="M12 3 2 20h20z"/><path d="M12 10v4M12 17h.01"/></svg>',
  play: '<svg viewBox="0 0 24 24"><path d="M7 4v16l13-8z"/></svg>',
  brain: '<svg viewBox="0 0 24 24"><path d="M9 4a3 3 0 0 0-3 3 3 3 0 0 0-2 5 3 3 0 0 0 2 5 3 3 0 0 0 6 1V5a3 3 0 0 0-3-1zM15 4a3 3 0 0 1 3 3 3 3 0 0 1 2 5 3 3 0 0 1-2 5 3 3 0 0 1-6 1"/></svg>',
  db: '<svg viewBox="0 0 24 24"><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/></svg>',
  bike: '<svg viewBox="0 0 24 24"><circle cx="6" cy="17" r="3"/><circle cx="18" cy="17" r="3"/><path d="M6 17l4-8h5l3 8M10 9 8 5H5"/></svg>',
  chef: '<svg viewBox="0 0 24 24"><path d="M6 14a4 4 0 1 1 2-7.5 4 4 0 0 1 8 0A4 4 0 1 1 18 14v6H6z"/></svg>',
  box: '<svg viewBox="0 0 24 24"><path d="M3 7l9-4 9 4-9 4zM3 7v10l9 4 9-4V7M12 11v10"/></svg>',
  party: '<svg viewBox="0 0 24 24"><path d="M4 20 9 6l9 9z"/><path d="M14 4l1 2M20 10l-2 1M18 3l-1 3"/></svg>',
  plus: '<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>',
  arrow: '<svg viewBox="0 0 24 24"><path d="M5 12h14M13 6l6 6-6 6"/></svg>',
  bowl: '<svg viewBox="0 0 24 24"><path d="M3 11h18a9 9 0 0 1-18 0z"/><path d="M8 7c0-1.5 1-2 1-3.5M12 7c0-1.5 1-2 1-3.5M16 7c0-1.5 1-2 1-3.5"/></svg>',
  sparkle: '<svg viewBox="0 0 24 24"><path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/></svg>',
  external: '<svg viewBox="0 0 24 24"><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/></svg>',
};

const BLOB_SHAPES = [
  "M32 7c3.2 0 5.6 2.2 5.9 5.3C47.6 14.6 55 23 55 34c0 12.7-10 22-23 22S9 46.7 9 34c0-11 7.4-19.4 17.1-21.7C26.4 9.2 28.8 7 32 7z",
  "M32 12c14 0 24 8.5 24 21S46 56 32 56 8 45.5 8 33s10-21 24-21z",
  "M22 6c4 0 6 4 6 10v2.5c1.3-.3 2.6-.5 4-.5s2.7.2 4 .5V16c0-6 2-10 6-10s5 4 4 10l-1.7 6.8C50.7 27 54 32.5 54 38c0 10.5-9.8 18-22 18S10 48.5 10 38c0-5.5 3.3-11 9.7-15.2L18 16c-1-6 0-10 4-10z",
  "M32 8c12 0 20 9 20 22v26l-5-4-5 4-5-4-5 4-5-4-5 4-5-4-5 4V30C12 17 20 8 32 8z",
];

function blob(color = "#3a4fe0", shape = 0) {
  return `<svg class="blob" viewBox="0 0 64 64" aria-hidden="true">
    <path d="${BLOB_SHAPES[shape % BLOB_SHAPES.length]}" fill="${color}"/>
    <ellipse cx="23" cy="24" rx="9" ry="5" fill="#fff" opacity=".22" transform="rotate(-20 23 24)"/>
    <circle cx="25" cy="35" r="2.6" fill="#16151d"/><circle cx="39" cy="35" r="2.6" fill="#16151d"/>
    <circle cx="25.9" cy="34.1" r=".8" fill="#fff"/><circle cx="39.9" cy="34.1" r=".8" fill="#fff"/>
  </svg>`;
}

const BLOB_CREW = [
  { color: "#d98a63", shape: 1 },
  { color: "#efe9df", shape: 3 },
  { color: "#3a4fe0", shape: 0 },
  { color: "#f2b766", shape: 2 },
  { color: "#b9cfa8", shape: 3 },
];

const storage = {
  get(key) { try { return localStorage.getItem(key); } catch { return null; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch {} },
};

const SESSION_KEY = "agent.session";
const sessionChannel = "BroadcastChannel" in window ? new BroadcastChannel("agent-session") : null;

function broadcast(type, data = {}) {
  if (sessionChannel) sessionChannel.postMessage({ type, ...data });
}

class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

async function api(path, { method = "GET", body, headers = {} } = {}) {
  let response;
  try {
    response = await fetch(path, {
      method,
      headers: { ...(body ? { "Content-Type": "application/json" } : {}), ...headers },
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    window.dispatchEvent(new Event("agent:offline"));
    throw new ApiError(0, "Server unreachable");
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data.error || (Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join("; ") : data.detail);
    throw new ApiError(response.status, detail || `Request failed (${response.status})`);
  }
  return data;
}

function toast(text, ms = 3200) {
  const el = $("#toast");
  el.textContent = text;
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { el.hidden = true; }, ms);
}

function watchHealth(onDown, onUp) {
  let online = true;
  const tick = async () => {
    try {
      const res = await fetch("/api/health", { cache: "no-store" });
      if (!res.ok) throw new Error();
      if (!online) { online = true; await onUp(); }
    } catch {
      if (online) { online = false; onDown(); }
    }
    setTimeout(tick, online ? 5000 : 1500);
  };
  window.addEventListener("agent:offline", () => { if (online) { online = false; onDown(); } });
  setTimeout(tick, 5000);
}
