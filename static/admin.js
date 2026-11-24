import { api, fmtMinutes, esc, avatarHtml } from "./api.js";

const root = document.getElementById("adminRoot");
let currentAdmin = null;
let activeTab = "monitoring";

async function boot() {
  try {
    const res = await api.get("/api/auth/me");
    currentAdmin = res.user && res.user.role === "admin" ? res.user : null;
  } catch (_) {
    currentAdmin = null;
  }
  render();
}

function render() {
  if (!currentAdmin) return renderAdminLogin();
  renderAdminConsole();
}

function renderAdminLogin() {
  root.innerHTML = `
    <div class="auth-wrap">
      <div class="auth-card">
        <div class="brand" style="padding:0;margin-bottom:18px">
          <div class="brand-logo">S</div>
          <div>
            <div class="brand-name">STUDYVERSE Admin</div>
            <div class="brand-tag">Super Administrator Access Only</div>
          </div>
        </div>
        <form id="admLoginForm" class="stack">
          <div><label class="muted">Admin Username</label><input class="input" name="username" required /></div>
          <div><label class="muted">Admin Password</label><input class="input" type="password" name="password" required /></div>
          <button class="btn btn-primary" type="submit">Authenticate</button>
        </form>
        <div id="admErr" class="muted" style="color:var(--red);margin-top:10px"></div>
        <div style="margin-top:16px;text-align:center"><a href="/">← Return to Student Portal</a></div>
      </div>
    </div>
  `;
  document.getElementById("admLoginForm").onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    try {
      const res = await api.post("/api/auth/login", { username: fd.get("username"), password: fd.get("password") });
      if (res.user.role !== "admin") throw new Error("Access denied: Super Administrator role required.");
      api.setToken(res.token);
      currentAdmin = res.user;
      render();
    } catch (err) {
      document.getElementById("admErr").textContent = err.message;
    }
  };
}

function renderAdminConsole() {
  root.innerHTML = `
    <div class="app-shell">
      <aside class="sidebar">
        <div class="brand">
          <div class="brand-logo">S</div>
          <div>
            <div class="brand-name">ADMIN CONSOLE</div>
            <div class="brand-tag">${esc(currentAdmin.username)}</div>
          </div>
        </div>
        <nav class="nav-list">
          ${[
            ["monitoring", "📊 Daily Monitoring & Students"],
            ["academics", "📚 Syllabus, Timetables & Quotes"],
            ["moderation", "🛡 Community Moderation"],
            ["settings", "⚙ Settings, Announcements & Audit"],
          ].map(([id, lbl]) => `
            <button class="nav-item ${activeTab === id ? "active" : ""}" data-tab="${id}">${lbl}</button>
          `).join("")}
        </nav>
        <div class="stack">
          <a class="btn btn-secondary btn-sm" href="/">Student View</a>
          <button class="btn btn-ghost btn-sm" id="admLogout">Logout</button>
        </div>
      </aside>
      <div class="main-area">
        <header class="topbar">
          <strong>Super Administrator Dashboard</strong>
          <div class="row">
            <button class="btn btn-secondary btn-sm" id="trigDaily">Run 11 PM Daily Report</button>
            <button class="btn btn-secondary btn-sm" id="trigWeekly">Publish Weekly Archive</button>
          </div>
        </header>
        <main class="content" id="admContent">Loading...</main>
      </div>
    </div>
  `;
  root.querySelectorAll("[data-tab]").forEach(b => b.onclick = () => { activeTab = b.dataset.tab; renderAdminConsole(); });
  document.getElementById("admLogout").onclick = async () => { await api.post("/api/auth/logout", {}); api.setToken(""); currentAdmin = null; render(); };
  document.getElementById("trigDaily").onclick = async () => { await api.post("/api/admin/trigger-daily-report", {}); alert("Daily report generated!"); loadTab(); };
  document.getElementById("trigWeekly").onclick = async () => { await api.post("/api/admin/trigger-weekly-archive", {}); alert("Weekly leaderboard archived!"); loadTab(); };
  loadTab();
}

async function loadTab() {
  const el = document.getElementById("admContent");
  if (activeTab === "monitoring") await tabMonitoring(el);
  else if (activeTab === "academics") await tabAcademics(el);
  else if (activeTab === "moderation") await tabModeration(el);
  else if (activeTab === "settings") await tabSettings(el);
}

async function tabMonitoring(el) {
  const data = await api.get("/api/admin/overview");
  el.innerHTML = `
    <div class="grid grid-3" style="margin-bottom:16px">
      <div class="card"><div class="muted">Registered Students</div><div style="font-size:26px;font-weight:700">${data.summary.total_students}</div></div>
      <div class="card"><div class="muted">Below 6h Target Today</div><div style="font-size:26px;font-weight:700;color:var(--amber)">${data.summary.below_6h}</div></div>
      <div class="card"><div class="muted">Unreported Today</div><div style="font-size:26px;font-weight:700;color:var(--red)">${data.summary.unreported}</div></div>
    </div>

    <div class="card stack">
      <div class="between" style="flex-wrap:wrap;gap:8px">
        <h3>All Student Progress &amp; Management</h3>
        <div class="row">
          <select class="select" id="fBoard" style="width:130px"><option value="">All Boards</option><option>CBSE</option><option>MP Board</option></select>
          <select class="select" id="fStream" style="width:140px"><option value="">All Streams</option><option>PCM</option><option>PCB</option><option>Commerce</option><option>Arts / Humanities</option></select>
          <select class="select" id="fAct" style="width:150px"><option value="">All Activity</option><option value="below_target">Below 6h</option><option value="unreported">Unreported</option></select>
          <button class="btn btn-secondary btn-sm" id="applyFilters">Filter</button>
        </div>
      </div>
      <div id="stuTable" class="stack"></div>
    </div>
  `;

  const renderRows = (rows) => {
    document.getElementById("stuTable").innerHTML = rows.map(s => `
      <div class="between" style="padding:12px;border:1px solid var(--border);border-radius:10px;flex-wrap:wrap;gap:10px">
        <div class="row">
          ${avatarHtml(s)}
          <div>
            <strong>${esc(s.full_name)}</strong> <span class="muted">@${esc(s.username)}</span>
            ${s.role === "moderator" ? `<span class="badge badge-purple">Moderator</span>` : ""}
            ${s.ban_type !== "none" ? `<span class="badge badge-red">Banned (${esc(s.ban_type)})</span>` : ""}
            ${s.is_suspended ? `<span class="badge badge-amber">Suspended</span>` : ""}
            <div class="muted">${esc(s.board || "-")} • ${esc(s.stream || "-")} • Today: ${s.daily_reported ? fmtMinutes(s.daily_minutes) : "Not reported"} • Week: ${fmtMinutes(s.weekly_minutes)} • Syllabus: ${s.syllabus_pct}%</div>
          </div>
        </div>
        <div class="row" style="flex-wrap:wrap">
          <button class="btn btn-secondary btn-sm" data-mkmod="${s.id}" data-cur="${s.role}">${s.role === "moderator" ? "Revoke Mod" : "Make Moderator"}</button>
          <button class="btn btn-secondary btn-sm" data-ban="${s.id}" data-act="temp_ban">Temp Ban</button>
          <button class="btn btn-danger btn-sm" data-ban="${s.id}" data-act="perm_ban">Perm Ban</button>
          <button class="btn btn-ghost btn-sm" data-ban="${s.id}" data-act="unban">Unban</button>
          <button class="btn btn-ghost btn-sm" data-ban="${s.id}" data-act="delete">Delete</button>
        </div>
      </div>
    `).join("") || `<div class="muted">No matching students.</div>`;

    document.querySelectorAll("[data-mkmod]").forEach(b => b.onclick = async () => {
      const nextRole = b.dataset.cur === "moderator" ? "student" : "moderator";
      await api.post(`/api/admin/students/${b.dataset.mkmod}/role`, { role: nextRole, mod_categories: ["ACADEMIC", "NON-ACADEMIC"] });
      tabMonitoring(el);
    });
    document.querySelectorAll("[data-ban]").forEach(b => b.onclick = async () => {
      const act = b.dataset.act;
      let reason = "";
      let confirmFlag = false;
      if (act === "temp_ban" || act === "perm_ban") {
        reason = prompt("Enter moderation reason:") || "";
        if (!reason) return;
      }
      if (act === "perm_ban" || act === "delete") {
        confirmFlag = confirm(`Confirm destructive action (${act})?`);
        if (!confirmFlag) return;
      }
      await api.post(`/api/admin/students/${b.dataset.ban}/moderation`, { action: act, reason, duration_days: 7, confirm: confirmFlag });
      tabMonitoring(el);
    });
  };

  renderRows(data.students);
  document.getElementById("applyFilters").onclick = async () => {
    const b = document.getElementById("fBoard").value;
    const s = document.getElementById("fStream").value;
    const a = document.getElementById("fAct").value;
    const r = await api.get(`/api/admin/overview?board=${encodeURIComponent(b)}&stream=${encodeURIComponent(s)}&activity=${encodeURIComponent(a)}`);
    renderRows(r.students);
  };
}

async function tabAcademics(el) {
  const [ov, ac] = await Promise.all([api.get("/api/admin/overview"), api.get("/api/admin/academics")]);
  el.innerHTML = `
    <div class="grid grid-2" style="margin-bottom:16px">
      <div class="card stack">
        <h3>Official Timetable Management</h3>
        ${ov.timetables.map(t => `
          <form class="stack tt-form" style="padding:10px;border:1px solid var(--border);border-radius:10px">
            <strong>${esc(t.board)}</strong>
            <input type="hidden" name="board" value="${esc(t.board)}" />
            <input class="input" name="official_url" value="${esc(t.official_url)}" />
            <div class="row">
              <select class="select" name="status"><option value="published" ${t.status === "published" ? "selected" : ""}>published</option><option value="not_published" ${t.status === "not_published" ? "selected" : ""}>not_published</option></select>
              <input class="input" name="last_verified" value="${esc(t.last_verified)}" />
            </div>
            <div class="row">
              <input class="input" type="date" name="exam_start_date" value="${esc(t.exam_start_date || "")}" />
              <input class="input" type="date" name="exam_end_date" value="${esc(t.exam_end_date || "")}" />
            </div>
            <input class="input" name="notes" value="${esc(t.notes || "")}" />
            <button class="btn btn-primary btn-sm" type="submit">Update ${esc(t.board)}</button>
          </form>
        `).join("")}
      </div>

      <div class="card stack">
        <h3>Add Syllabus Chapter</h3>
        <form id="addChForm" class="stack">
          <select class="select" name="subject_id">
            ${ac.subjects.map(s => `<option value="${s.id}">${esc(s.board)} • ${esc(s.stream)} • ${esc(s.name)}</option>`).join("")}
          </select>
          <div class="row">
            <input class="input" type="number" name="chapter_number" value="15" style="width:90px" />
            <input class="input" name="unit_name" placeholder="Unit name..." />
          </div>
          <input class="input" name="title" required placeholder="Chapter title..." />
          <button class="btn btn-primary btn-sm" type="submit">Add Chapter</button>
        </form>

        <h3 style="margin-top:12px">Add Motivational Quote</h3>
        <form id="addQuoteForm" class="stack">
          <textarea class="textarea" name="text" rows="2" required placeholder="Quote text..."></textarea>
          <div class="row">
            <input class="input" name="author" required placeholder="Author" />
            <input class="input" name="category" value="Discipline" />
          </div>
          <button class="btn btn-secondary btn-sm" type="submit">Add Quote</button>
        </form>
      </div>
    </div>
  `;
  el.querySelectorAll(".tt-form").forEach(f => f.onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    await api.put("/api/admin/timetables", Object.fromEntries(fd.entries()));
    alert("Timetable updated!");
  });
  document.getElementById("addChForm").onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    await api.post("/api/admin/chapters", {
      subject_id: Number(fd.get("subject_id")),
      chapter_number: Number(fd.get("chapter_number")),
      unit_name: fd.get("unit_name"),
      title: fd.get("title"),
    });
    alert("Chapter added!");
  };
  document.getElementById("addQuoteForm").onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    await api.post("/api/admin/quotes", Object.fromEntries(fd.entries()));
    alert("Quote added!");
    e.target.reset();
  };
}

async function tabModeration(el) {
  const res = await api.get("/api/mod/reports");
  el.innerHTML = `
    <div class="card stack">
      <h3>Reported Messages Queue</h3>
      ${res.reports.map(r => `
        <div class="between" style="padding:10px;border:1px solid var(--border);border-radius:10px">
          <div>
            <strong>@${esc(r.author_username)}</strong> in #${esc(r.channel_name)}: “${esc(r.message_content)}”
            <div class="muted">${esc(r.reason)} (${esc(r.status)})</div>
          </div>
          ${r.status === "pending" ? `
            <div class="row">
              <button class="btn btn-danger btn-sm" data-r="${r.id}" data-a="delete_message">Delete</button>
              <button class="btn btn-secondary btn-sm" data-r="${r.id}" data-a="mute_24h">Mute 24h</button>
              <button class="btn btn-ghost btn-sm" data-r="${r.id}" data-a="dismiss">Dismiss</button>
            </div>
          ` : ""}
        </div>
      `).join("") || `<div class="muted">No reports.</div>`}
    </div>
  `;
  el.querySelectorAll("[data-r]").forEach(b => b.onclick = async () => {
    await api.post(`/api/mod/reports/${b.dataset.r}/resolve`, { action: b.dataset.a });
    tabModeration(el);
  });
}

async function tabSettings(el) {
  const ov = await api.get("/api/admin/overview");
  el.innerHTML = `
    <div class="grid grid-2">
      <div class="card stack">
        <h3>Publish Platform Announcement</h3>
        <form id="annForm" class="stack">
          <input class="input" name="title" required placeholder="Announcement title..." />
          <textarea class="textarea" name="body" rows="3" required placeholder="Announcement message..."></textarea>
          <button class="btn btn-primary btn-sm" type="submit">Publish Announcement</button>
        </form>
        <h3 style="margin-top:12px">Platform Settings</h3>
        <form id="setForm" class="stack">
          <label class="muted">Default Daily Target (minutes)</label>
          <input class="input" name="daily_target_minutes" value="${esc(ov.settings.daily_target_minutes || "360")}" />
          <label class="muted">Leaderboard Sunday-Only Public Lock (1 = Mon-Sat Private, 0 = Always Public)</label>
          <input class="input" name="leaderboard_public_sunday_only" value="${esc(ov.settings.leaderboard_public_sunday_only || "1")}" />
          <button class="btn btn-secondary btn-sm" type="submit">Save Settings</button>
        </form>
      </div>
      <div class="card stack">
        <h3>Administrator Audit Log</h3>
        <div class="stack" style="max-height:420px;overflow-y:auto">
          ${ov.audit_logs.map(l => `
            <div style="padding:8px;border:1px solid var(--border);border-radius:8px;font-size:12px">
              <strong>${esc(l.action)}</strong> (${esc(l.actor_role)}) → ${esc(l.target_type)} #${esc(l.target_id)}
              <div class="muted">${esc(l.details)} • ${esc(l.created_at.slice(0, 19))}</div>
            </div>
          `).join("")}
        </div>
      </div>
    </div>
  `;
  document.getElementById("annForm").onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    await api.post("/api/admin/announcements", Object.fromEntries(fd.entries()));
    alert("Announcement published!");
    e.target.reset();
  };
  document.getElementById("setForm").onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    await api.put("/api/admin/settings", Object.fromEntries(fd.entries()));
    alert("Settings saved!");
  };
}

boot();
