// STUDYVERSE API, Instant Local-First Cache, Offline Sync Queue, & WebSocket Client
const QUEUE_KEY = "sv_sync_queue";

export const api = {
  token: localStorage.getItem("sv_token") || "",

  setToken(t) {
    this.token = t || "";
    if (t) localStorage.setItem("sv_token", t);
    else localStorage.removeItem("sv_token");
  },

  getCached(path) {
    try {
      const raw = localStorage.getItem("sv_cache_" + path);
      return raw ? JSON.parse(raw) : null;
    } catch { return null; }
  },

  setCached(path, data) {
    try { localStorage.setItem("sv_cache_" + path, JSON.stringify(data)); } catch (_) {}
  },

  getQueue() {
    try { return JSON.parse(localStorage.getItem(QUEUE_KEY) || "[]"); } catch { return []; }
  },

  pushQueue(item) {
    const q = this.getQueue();
    q.push({ ...item, ts: Date.now() });
    localStorage.setItem(QUEUE_KEY, JSON.stringify(q));
  },

  async flushQueue() {
    if (!navigator.onLine) return 0;
    const q = this.getQueue();
    if (!q.length) return 0;
    const remaining = [];
    let synced = 0;
    for (const item of q) {
      try {
        await this.req(item.path, { method: item.method, body: item.body, skipQueue: true });
        synced++;
      } catch (err) {
        if (!navigator.onLine) remaining.push(item);
      }
    }
    localStorage.setItem(QUEUE_KEY, JSON.stringify(remaining));
    return synced;
  },

  // Instant Local-First GET: returns cached data in 0ms if available, and refreshes from cloud in background
  async getFast(path, onFresh) {
    const cached = this.getCached(path);
    const netPromise = this.get(path).then((fresh) => {
      if (onFresh) onFresh(fresh);
      return fresh;
    }).catch((err) => {
      if (!cached) throw err;
      return cached;
    });
    if (cached) return cached;
    return await netPromise;
  },

  async req(path, opts = {}) {
    const method = (opts.method || "GET").toUpperCase();
    const headers = opts.headers ? { ...opts.headers } : {};
    if (this.token) headers["Authorization"] = `Bearer ${this.token}`;
    const rawBody = opts.body;
    let sendBody = rawBody;
    if (rawBody && !(rawBody instanceof FormData) && typeof rawBody !== "string") {
      headers["Content-Type"] = "application/json";
      sendBody = JSON.stringify(rawBody);
    }
    try {
      const res = await fetch(path, { ...opts, method, body: sendBody, headers, credentials: "include" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `Request failed (${res.status})`);
      if (method === "GET") this.setCached(path, data);
      return data;
    } catch (err) {
      if (method === "GET") {
        const cached = this.getCached(path);
        if (cached) return cached;
      }
      if (!opts.skipQueue && !navigator.onLine && (path.includes("/progress") || path === "/api/study/sessions") && !(rawBody instanceof FormData)) {
        this.pushQueue({ path, method, body: rawBody });
        return { ok: true, queued_offline: true, revisions_scheduled: 0 };
      }
      throw err;
    }
  },

  get(path) { return this.req(path, { method: "GET" }); },
  post(path, body) { return this.req(path, { method: "POST", body }); },
  put(path, body) { return this.req(path, { method: "PUT", body }); },
  del(path) { return this.req(path, { method: "DELETE" }); },

  connectChatWS(channelId, onMessage) {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const q = this.token ? `?token=${encodeURIComponent(this.token)}` : "";
    const ws = new WebSocket(`${proto}//${location.host}/ws/chat/${channelId}${q}`);
    ws.onmessage = (ev) => {
      try {
        onMessage(JSON.parse(ev.data));
      } catch (_) {}
    };
    return ws;
  }
};

export function fmtMinutes(mins) {
  const m = Math.max(0, Math.round(mins || 0));
  const h = Math.floor(m / 60);
  const rem = m % 60;
  return `${h}h ${rem}m`;
}

export function esc(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export function avatarHtml(user, large = false) {
  const cls = large ? "avatar avatar-lg" : "avatar";
  if (user && user.profile_pic) {
    return `<img class="${cls}" src="${esc(user.profile_pic)}" alt="${esc(user.username || "")}" />`;
  }
  const initial = ((user && (user.full_name || user.username)) || "S").charAt(0).toUpperCase();
  return `<span class="${cls}">${esc(initial)}</span>`;
}
