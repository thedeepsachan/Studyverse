// STUDYVERSE API & WebSocket Client
export const api = {
  token: localStorage.getItem("sv_token") || "",

  setToken(t) {
    this.token = t || "";
    if (t) localStorage.setItem("sv_token", t);
    else localStorage.removeItem("sv_token");
  },

  async req(path, opts = {}) {
    const headers = opts.headers ? { ...opts.headers } : {};
    if (this.token) headers["Authorization"] = `Bearer ${this.token}`;
    if (opts.body && !(opts.body instanceof FormData) && !headers["Content-Type"]) {
      headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.body);
    }
    const res = await fetch(path, { ...opts, headers, credentials: "include" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || `Request failed (${res.status})`);
    }
    return data;
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
