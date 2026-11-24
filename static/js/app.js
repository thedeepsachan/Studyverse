import { api, fmtMinutes, esc, avatarHtml } from "./api.js";
import { renderBarChart, renderDonutChart, renderHorizontalBars } from "./charts.js";

const root = document.getElementById("root");

const state = {
  user: null,
  route: "home",
  theme: localStorage.getItem("sv_theme") || "light",
  authMode: "login", // login | register | recover
  selectedSubjectId: null,
  activeChannelId: null,
  chatWs: null,
  pomodoro: { running: false, mode: "study", secondsLeft: 25 * 60, customStudyMins: 25, intervalId: null },
  countdownInterval: null,
  unreadNotifs: 0,
};

document.documentElement.setAttribute("data-theme", state.theme);

function toggleTheme() {
  state.theme = state.theme === "light" ? "dark" : "light";
  localStorage.setItem("sv_theme", state.theme);
  document.documentElement.setAttribute("data-theme", state.theme);
  render();
}

function toast(msg, isErr = false) {
  const el = document.createElement("div");
  el.textContent = msg;
  Object.assign(el.style, {
    position: "fixed", bottom: "84px", right: "20px", zIndex: "9999",
    padding: "10px 16px", borderRadius: "10px", fontWeight: "500", fontSize: "13px",
    background: isErr ? "var(--red)" : "var(--navy)", color: "#fff",
    boxShadow: "var(--shadow-md)"
  });
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 3200);
}

async function boot() {
  try {
    const res = await api.get("/api/auth/me");
    state.user = res.user;
  } catch (_) {
    state.user = null;
  }
  render();
}

function render() {
  if (state.countdownInterval) { clearInterval(state.countdownInterval); state.countdownInterval = null; }
  if (!state.user) return renderAuth();
  if (!state.user.onboarding_complete && state.user.role !== "admin") return renderOnboarding();
  renderShell();
}

// ---------------- AUTH ----------------
function renderAuth() {
  root.innerHTML = `
    <div class="auth-wrap">
      <div class="auth-card">
        <div class="between" style="margin-bottom:20px">
          <div class="brand" style="padding:0">
            <div class="brand-logo">S</div>
            <div>
              <div class="brand-name">STUDYVERSE</div>
              <div class="brand-tag">Study Together. Grow Together.</div>
            </div>
          </div>
          <button class="btn btn-ghost btn-sm" id="themeBtn">${state.theme === "light" ? "Dark" : "Light"}</button>
        </div>

        <div class="row" style="margin-bottom:18px;background:var(--bg-subtle);padding:4px;border-radius:10px">
          <button class="btn btn-sm ${state.authMode === "login" ? "btn-primary" : "btn-ghost"}" style="flex:1" data-auth="login">Sign In</button>
          <button class="btn btn-sm ${state.authMode === "register" ? "btn-primary" : "btn-ghost"}" style="flex:1" data-auth="register">Register</button>
          <button class="btn btn-sm ${state.authMode === "recover" ? "btn-primary" : "btn-ghost"}" style="flex:1" data-auth="recover">Recover</button>
        </div>

        <form id="authForm" class="stack">
          ${state.authMode === "register" ? `
            <div>
              <label class="muted">Full Name</label>
              <input class="input" name="full_name" required placeholder="e.g. Aarav Sharma" />
            </div>
          ` : ""}
          <div>
            <label class="muted">Username</label>
            <input class="input" name="username" required placeholder="e.g. aarav_12" />
          </div>
          ${state.authMode === "recover" ? `
            <div>
              <label class="muted">Account Recovery Code</label>
              <input class="input" name="recovery_code" required placeholder="SV-XXXX-XXXX-XXXX" />
            </div>
          ` : ""}
          <div>
            <label class="muted">${state.authMode === "recover" ? "New Password" : "Password"}</label>
            <input class="input" type="password" name="password" required minlength="6" placeholder="••••••••" />
          </div>
          ${state.authMode === "register" ? `
            <div>
              <label class="muted">Optional Profile Picture</label>
              <input class="input" type="file" name="avatar" accept="image/*" />
            </div>
          ` : ""}
          <button class="btn btn-primary" type="submit">
            ${state.authMode === "login" ? "Sign In" : state.authMode === "register" ? "Create Student Account" : "Reset Password"}
          </button>
        </form>
        <div id="recoveryModal" class="hidden" style="margin-top:16px;padding:14px;border-radius:10px;background:var(--green-soft);border:1px solid var(--green)"></div>
        <div class="muted" style="margin-top:16px;text-align:center">
          Exclusively for Class 12 MP Board &amp; CBSE students.
          <br /><a href="/admin">Administrator Portal</a>
        </div>
      </div>
    </div>
  `;
  document.getElementById("themeBtn").onclick = toggleTheme;
  root.querySelectorAll("[data-auth]").forEach(b => b.onclick = () => { state.authMode = b.dataset.auth; render(); });
  document.getElementById("authForm").onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    try {
      if (state.authMode === "login") {
        const res = await api.post("/api/auth/login", { username: fd.get("username"), password: fd.get("password") });
        api.setToken(res.token);
        state.user = res.user;
        render();
      } else if (state.authMode === "register") {
        const res = await api.post("/api/auth/register", {
          full_name: fd.get("full_name"),
          username: fd.get("username"),
          password: fd.get("password"),
        });
        api.setToken(res.token);
        const file = fd.get("avatar");
        if (file && file.size > 0) {
          const up = new FormData();
          up.append("file", file);
          const av = await api.req("/api/profile/avatar", { method: "POST", body: up });
          res.user.profile_pic = av.profile_pic;
        }
        state.user = res.user;
        alert(`Save your Recovery Code securely (shown once):\n\n${res.recovery_code}`);
        render();
      } else {
        const res = await api.post("/api/auth/recover", {
          username: fd.get("username"),
          recovery_code: fd.get("recovery_code"),
          new_password: fd.get("password"),
        });
        const box = document.getElementById("recoveryModal");
        box.classList.remove("hidden");
        box.innerHTML = `<strong>Password updated!</strong> New recovery code: <code>${esc(res.new_recovery_code)}</code>. You can now sign in.`;
      }
    } catch (err) {
      toast(err.message, true);
    }
  };
}

// ---------------- ONBOARDING ----------------
function renderOnboarding() {
  root.innerHTML = `
    <div class="auth-wrap">
      <div class="auth-card">
        <h2 style="margin-bottom:6px">Welcome, ${esc(state.user.full_name)}</h2>
        <p class="muted" style="margin-bottom:18px">Select your Class 12 Board and Stream to organize your subjects, syllabus, and official exam countdown.</p>
        <form id="onboardForm" class="stack">
          <div>
            <label class="muted">Class</label>
            <input class="input" value="Class 12" disabled />
          </div>
          <div>
            <label class="muted">Board</label>
            <select class="select" name="board" required>
              <option value="CBSE">CBSE</option>
              <option value="MP Board">MP Board (MPBSE)</option>
            </select>
          </div>
          <div>
            <label class="muted">Stream</label>
            <select class="select" name="stream" required>
              <option value="PCM">PCM (Physics, Chemistry, Mathematics, English, Hindi)</option>
              <option value="PCB">PCB (Physics, Chemistry, Biology, English, Hindi)</option>
              <option value="Commerce">Commerce (Accountancy, Business Studies, Economics, English, Hindi)</option>
              <option value="Arts / Humanities">Arts / Humanities (History, Political Science, Geography, Economics, English, Hindi)</option>
            </select>
          </div>
          <button class="btn btn-primary" type="submit">Launch My Dashboard</button>
        </form>
      </div>
    </div>
  `;
  document.getElementById("onboardForm").onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    try {
      const res = await api.post("/api/auth/onboarding", { board: fd.get("board"), stream: fd.get("stream") });
      state.user = res.user;
      render();
    } catch (err) {
      toast(err.message, true);
    }
  };
}

// ---------------- MAIN APP SHELL ----------------
const NAV_ITEMS = [
  { id: "home", label: "Home", icon: "⌂" },
  { id: "subjects", label: "My Subjects", icon: "📚" },
  { id: "tracker", label: "Study Tracker", icon: "⏱" },
  { id: "backlogs", label: "My Backlogs", icon: "📌" },
  { id: "revision", label: "Revision", icon: "↻" },
  { id: "community", label: "Community", icon: "💬" },
  { id: "leaderboard", label: "Leaderboard", icon: "🏆" },
];

function renderShell() {
  const u = state.user;
  root.innerHTML = `
    <div class="app-shell">
      <aside class="sidebar">
        <div class="brand">
          <div class="brand-logo">S</div>
          <div>
            <div class="brand-name">STUDYVERSE</div>
            <div class="brand-tag">Study Together. Grow Together.</div>
          </div>
        </div>
        <nav class="nav-list">
          ${NAV_ITEMS.map(n => `
            <button class="nav-item ${state.route === n.id ? "active" : ""}" data-nav="${n.id}">
              <span>${n.icon}</span><span>${n.label}</span>
            </button>
          `).join("")}
          <div style="border-top:1px solid var(--border);margin:8px 0"></div>
          <button class="nav-item ${state.route === "calendar" ? "active" : ""}" data-nav="calendar"><span>📅</span><span>Calendar &amp; Planner</span></button>
          <button class="nav-item ${state.route === "notifications" ? "active" : ""}" data-nav="notifications"><span>🔔</span><span>Notifications <span id="notifBadge" class="badge badge-indigo hidden">0</span></span></button>
          <button class="nav-item ${state.route === "profile" ? "active" : ""}" data-nav="profile"><span>👤</span><span>Profile</span></button>
          ${u.role === "admin" ? `<a class="nav-item" href="/admin"><span>🛡</span><span>Admin Panel</span></a>` : ""}
        </nav>
        <div style="border-top:1px solid var(--border);padding-top:12px" class="stack">
          <div class="row">
            ${avatarHtml(u)}
            <div style="min-width:0">
              <div style="font-weight:600;font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(u.full_name)}</div>
              <div class="muted" style="font-size:11px">${esc(u.board || "")} • ${esc(u.stream || "")}</div>
            </div>
          </div>
          <div class="row">
            <button class="btn btn-secondary btn-sm" style="flex:1" id="themeToggle">${state.theme === "light" ? "Dark" : "Light"}</button>
            <button class="btn btn-ghost btn-sm" id="logoutBtn">Logout</button>
          </div>
        </div>
      </aside>

      <div class="main-area">
        <header class="topbar">
          <div class="row">
            <strong style="font-size:16px">${NAV_ITEMS.find(n => n.id === state.route)?.label || (state.route === "calendar" ? "Calendar & Planner" : state.route === "notifications" ? "Notifications" : "Student Profile")}</strong>
            ${u.role === "moderator" ? `<span class="badge badge-purple">Moderator</span>` : ""}
          </div>
          <div class="row">
            <button class="btn btn-ghost btn-sm" data-nav="notifications">🔔</button>
            <button class="btn btn-ghost btn-sm" data-nav="profile">${esc(u.username)}</button>
          </div>
        </header>
        <main class="content" id="viewContent"><div class="muted">Loading...</div></main>
      </div>

      <nav class="mobile-nav">
        ${NAV_ITEMS.map(n => `
          <button class="${state.route === n.id ? "active" : ""}" data-nav="${n.id}">
            <span style="font-size:16px">${n.icon}</span><span>${n.label.replace("My ", "")}</span>
          </button>
        `).join("")}
      </nav>
    </div>
  `;

  root.querySelectorAll("[data-nav]").forEach(el => {
    el.onclick = () => {
      if (state.chatWs) { state.chatWs.close(); state.chatWs = null; }
      state.route = el.dataset.nav;
      renderShell();
    };
  });
  document.getElementById("themeToggle").onclick = toggleTheme;
  document.getElementById("logoutBtn").onclick = async () => {
    await api.post("/api/auth/logout", {});
    api.setToken("");
    state.user = null;
    render();
  };

  loadNotifCount();
  loadView();
}

async function loadNotifCount() {
  try {
    const res = await api.get("/api/notifications");
    const badge = document.getElementById("notifBadge");
    if (badge && res.unread_count > 0) {
      badge.textContent = res.unread_count;
      badge.classList.remove("hidden");
    }
  } catch (_) {}
}

async function loadView() {
  const container = document.getElementById("viewContent");
  try {
    if (state.route === "home") await viewHome(container);
    else if (state.route === "subjects") await viewSubjects(container);
    else if (state.route === "tracker") await viewTracker(container);
    else if (state.route === "backlogs") await viewBacklogs(container);
    else if (state.route === "revision") await viewRevision(container);
    else if (state.route === "community") await viewCommunity(container);
    else if (state.route === "leaderboard") await viewLeaderboard(container);
    else if (state.route === "calendar") await viewCalendar(container);
    else if (state.route === "notifications") await viewNotifications(container);
    else if (state.route === "profile") await viewProfile(container);
  } catch (err) {
    container.innerHTML = `<div class="card"><strong style="color:var(--red)">Error:</strong> ${esc(err.message)}</div>`;
  }
}

// ---------------- VIEW 1: HOME ----------------
async function viewHome(el) {
  const data = await api.get("/api/dashboard/home");
  const tt = data.timetable;
  const st = data.stats;
  const published = tt && tt.status === "published" && tt.exam_start_date;

  el.innerHTML = `
    <div class="quote-banner">
      <div class="quote-text">“${esc(data.quote.text)}”</div>
      <div class="quote-author">— ${esc(data.quote.author)} • <span style="opacity:0.8">${esc(data.quote.category)}</span></div>
    </div>

    ${data.announcements.length ? `
      <div class="card" style="margin-bottom:16px;border-left:4px solid var(--indigo)">
        <div class="between">
          <strong>📢 ${esc(data.announcements[0].title)}</strong>
          <span class="muted">${data.announcements[0].created_at.slice(0, 10)}</span>
        </div>
        <p class="muted" style="margin-top:4px">${esc(data.announcements[0].body)}</p>
      </div>
    ` : ""}

    <div class="card" style="margin-bottom:16px">
      <div class="between">
        <div>
          <span class="badge badge-indigo">${esc(state.user.board)} Class 12</span>
          <h2 style="margin-top:6px">Board Examination Countdown</h2>
          <p class="muted">${tt ? esc(tt.notes) : ""} ${tt ? `• Last verified: <strong>${esc(tt.last_verified)}</strong>` : ""}</p>
        </div>
        ${tt ? `<a class="btn btn-secondary btn-sm" href="${esc(tt.official_url)}" target="_blank" rel="noopener">Official Timetable ↗</a>` : ""}
      </div>
      ${published ? `
        <div class="countdown-boxes" id="mainCountdown"></div>
        <div style="margin-top:18px">
          <div class="muted" style="margin-bottom:8px;font-weight:600">Subject-Wise Examination Dates (${esc(state.user.stream)})</div>
          <div class="grid grid-3">
            ${data.subject_exams.map(s => {
              const daysLeft = s.exam_date ? Math.max(0, Math.ceil((new Date(s.exam_date + "T09:00:00+05:30") - Date.now()) / 86400000)) : null;
              return `
                <div style="padding:12px;border:1px solid var(--border);border-radius:10px;background:var(--bg-subtle)">
                  <div class="between"><strong>${esc(s.name)}</strong><span class="badge badge-purple">${daysLeft !== null ? `${daysLeft}d left` : "TBA"}</span></div>
                  <div class="muted" style="margin-top:4px">${esc(s.exam_date || "TBA")} • ${esc(s.exam_time || "")}</div>
                </div>
              `;
            }).join("")}
          </div>
        </div>
      ` : `<p style="margin-top:12px;font-weight:600;color:var(--amber)">Official examination timetable not released yet.</p>`}
    </div>

    <div class="grid grid-4" style="margin-bottom:16px">
      <div class="card">
        <div class="muted">Today's Study</div>
        <div style="font-size:26px;font-weight:700;margin:4px 0">${fmtMinutes(st.today_minutes)}</div>
        <div class="progress-track"><div class="progress-fill" style="width:${Math.min(100, Math.round((st.today_minutes / st.daily_target_minutes) * 100))}%"></div></div>
        <div class="muted" style="margin-top:6px">Target: ${fmtMinutes(st.daily_target_minutes)}</div>
      </div>
      <div class="card">
        <div class="muted">Syllabus Progress</div>
        <div style="font-size:26px;font-weight:700;margin:4px 0">${st.syllabus_pct}%</div>
        <div class="progress-track"><div class="progress-fill" style="width:${st.syllabus_pct}%"></div></div>
        <div class="muted" style="margin-top:6px">${st.completed_chapters}/${st.total_chapters} chapters done</div>
      </div>
      <div class="card">
        <div class="muted">Study Streak</div>
        <div style="font-size:26px;font-weight:700;margin:4px 0">${st.current_streak} days</div>
        <div class="muted">Longest: ${st.longest_streak} days</div>
      </div>
      <div class="card">
        <div class="muted">Action Items</div>
        <div style="font-size:26px;font-weight:700;margin:4px 0">${st.due_revisions} revisions</div>
        <div class="muted">${st.pending_backlogs} pending backlogs</div>
      </div>
    </div>

    ${st.today_minutes < st.daily_target_minutes ? `
      <div class="card" style="border-color:var(--amber);background:var(--amber-soft)">
        <div class="between" style="flex-wrap:wrap;gap:12px">
          <div>
            <strong>6-Hour Challenge Check-In</strong>
            <p class="muted" style="color:var(--text)">You haven't completed your 6-hour goal today. What stopped you?</p>
          </div>
          <button class="btn btn-primary btn-sm" id="goAccountability">Share Accountability Update</button>
        </div>
      </div>
    ` : ""}
  `;

  if (published) {
    const targetMs = new Date(tt.exam_start_date + "T10:30:00+05:30").getTime();
    const tick = () => {
      const box = document.getElementById("mainCountdown");
      if (!box) return;
      const diff = Math.max(0, targetMs - Date.now());
      const d = Math.floor(diff / 86400000);
      const h = Math.floor((diff % 86400000) / 3600000);
      const m = Math.floor((diff % 3600000) / 60000);
      const s = Math.floor((diff % 60000) / 1000);
      box.innerHTML = [
        [d, "Days"], [h, "Hours"], [m, "Minutes"], [s, "Seconds"]
      ].map(([v, l]) => `<div class="cd-box"><div class="cd-num">${String(v).padStart(2, "0")}</div><div class="cd-lbl">${l}</div></div>`).join("");
    };
    tick();
    state.countdownInterval = setInterval(tick, 1000);
  }
  const accBtn = document.getElementById("goAccountability");
  if (accBtn) accBtn.onclick = () => { state.route = "community"; renderShell(); };
}

// ---------------- VIEW 2: MY SUBJECTS ----------------
async function viewSubjects(el) {
  const data = await api.get("/api/subjects");
  if (!state.selectedSubjectId && data.subjects.length) state.selectedSubjectId = data.subjects[0].id;

  el.innerHTML = `
    <div class="card" style="margin-bottom:16px">
      <div class="between" style="flex-wrap:wrap;gap:12px">
        <div>
          <h3>Overall Syllabus Progress (${esc(data.board)} • ${esc(data.stream)})</h3>
          <p class="muted">${data.overall.completed_chapters} completed • ${data.overall.pending_chapters} pending of ${data.overall.total_chapters} chapters</p>
        </div>
        <strong style="font-size:22px;color:var(--indigo)">${data.overall.progress_pct}%</strong>
      </div>
      <div class="progress-track" style="margin-top:10px"><div class="progress-fill" style="width:${data.overall.progress_pct}%"></div></div>
    </div>

    <div class="grid grid-3" style="margin-bottom:18px">
      ${data.subjects.map(s => `
        <div class="card" style="cursor:pointer;border-color:${s.id === state.selectedSubjectId ? "var(--indigo)" : "var(--border)"}" data-subj="${s.id}">
          <div class="between">
            <strong>${esc(s.name)}</strong>
            <span class="badge badge-indigo">${s.progress_pct}%</span>
          </div>
          <div class="muted" style="margin:6px 0">${s.completed_chapters}/${s.total_chapters} chapters • Exam: ${esc(s.exam_date || "TBA")}</div>
          <div class="progress-track"><div class="progress-fill" style="width:${s.progress_pct}%"></div></div>
        </div>
      `).join("")}
    </div>

    <div class="card">
      <div class="between" style="flex-wrap:wrap;gap:10px;margin-bottom:14px">
        <h3 id="subjTitle">Chapters</h3>
        <div class="row">
          <input class="input" id="chSearch" placeholder="Search chapters..." style="width:200px" />
          <select class="select" id="chFilter" style="width:150px">
            <option value="all">All Chapters</option>
            <option value="incomplete">Incomplete</option>
            <option value="completed">Completed</option>
          </select>
        </div>
      </div>
      <div id="chaptersList" class="stack"></div>
    </div>
  `;

  el.querySelectorAll("[data-subj]").forEach(c => {
    c.onclick = () => { state.selectedSubjectId = Number(c.dataset.subj); viewSubjects(el); };
  });

  const loadChapters = async () => {
    const q = document.getElementById("chSearch").value;
    const f = document.getElementById("chFilter").value;
    const res = await api.get(`/api/subjects/${state.selectedSubjectId}/chapters?q=${encodeURIComponent(q)}&status_filter=${f}`);
    document.getElementById("subjTitle").textContent = `${res.subject.name} — Chapters (${res.chapters.length})`;
    const list = document.getElementById("chaptersList");
    list.innerHTML = res.chapters.map(ch => `
      <div style="padding:14px;border:1px solid var(--border);border-radius:12px">
        <div class="between" style="margin-bottom:8px">
          <div>
            <strong>Ch ${ch.chapter_number}: ${esc(ch.title)}</strong>
            <span class="muted"> • ${esc(ch.unit_name)}</span>
          </div>
          <span class="badge ${ch.full_done ? "badge-green" : "badge-indigo"}">${ch.chapter_progress_pct}%</span>
        </div>
        <div class="row" style="flex-wrap:wrap;gap:8px">
          ${[
            ["notes_done", "Notes Completed"],
            ["lectures_done", "Lectures Completed"],
            ["theory_done", "Theory Completed"],
            ["pyqs_done", "PYQs Completed"],
            ["full_done", "Full Chapter Completed"],
          ].map(([key, label]) => `
            <label class="chk-pill ${ch[key] ? "done" : ""}">
              <input type="checkbox" data-ch="${ch.id}" data-key="${key}" ${ch[key] ? "checked" : ""} />
              <span>${label}</span>
            </label>
          `).join("")}
        </div>
      </div>
    `).join("") || `<div class="muted">No matching chapters.</div>`;

    list.querySelectorAll("input[type=checkbox]").forEach(chk => {
      chk.onchange = async () => {
        try {
          const r = await api.put(`/api/chapters/${chk.dataset.ch}/progress`, { [chk.dataset.key]: chk.checked });
          if (r.revisions_scheduled > 0) toast(`Chapter confirmed complete! ${r.revisions_scheduled} spaced-repetition revisions scheduled.`);
          loadChapters();
        } catch (err) {
          toast(err.message, true);
        }
      };
    });
  };

  document.getElementById("chSearch").oninput = loadChapters;
  document.getElementById("chFilter").onchange = loadChapters;
  if (state.selectedSubjectId) await loadChapters();
}

// ---------------- VIEW 3: STUDY TRACKER ----------------
async function viewTracker(el) {
  const [stats, subjs, timerRes] = await Promise.all([
    api.get("/api/study/stats"),
    api.get("/api/subjects"),
    api.get("/api/study/timer"),
  ]);

  el.innerHTML = `
    <div class="grid grid-4" style="margin-bottom:16px">
      <div class="card"><div class="muted">Today</div><div style="font-size:24px;font-weight:700">${fmtMinutes(stats.today_minutes)}</div><div class="muted">${stats.target_achievement_pct}% of target</div></div>
      <div class="card"><div class="muted">This Week</div><div style="font-size:24px;font-weight:700">${fmtMinutes(stats.weekly_minutes)}</div><div class="muted">Daily avg: ${fmtMinutes(stats.daily_average_minutes)}</div></div>
      <div class="card"><div class="muted">This Month</div><div style="font-size:24px;font-weight:700">${fmtMinutes(stats.monthly_minutes)}</div><div class="muted">Streak: ${stats.current_streak}d (Best ${stats.longest_streak}d)</div></div>
      <div class="card"><div class="muted">Lifetime</div><div style="font-size:24px;font-weight:700">${fmtMinutes(stats.lifetime_minutes)}</div><div class="muted">Target: ${fmtMinutes(stats.daily_target_minutes)}/day</div></div>
    </div>

    <div class="grid grid-2" style="margin-bottom:16px">
      <div class="card stack">
        <h3>Live Study Timer</h3>
        <select class="select" id="timerSubj">
          <option value="">General Study</option>
          ${subjs.subjects.map(s => `<option value="${s.id}">${esc(s.name)}</option>`).join("")}
        </select>
        <div style="font-size:36px;font-weight:800;font-variant-numeric:tabular-nums;color:var(--indigo)" id="liveTimerDisp">00:00:00</div>
        <div class="row">
          <button class="btn btn-primary btn-sm" id="tStart">Start</button>
          <button class="btn btn-secondary btn-sm" id="tPause">Pause</button>
          <button class="btn btn-secondary btn-sm" id="tResume">Resume</button>
          <button class="btn btn-success btn-sm" id="tStop">Stop &amp; Save</button>
        </div>
      </div>

      <div class="card stack">
        <h3>Pomodoro Focus Timer</h3>
        <div class="between">
          <span class="badge badge-purple" id="pomoMode">${state.pomodoro.mode === "study" ? "Study Session" : "5-Min Break"}</span>
          <div class="row">
            <input class="input" type="number" id="pomoCustomMins" min="5" max="120" value="${state.pomodoro.customStudyMins}" style="width:80px" />
            <span class="muted">min</span>
          </div>
        </div>
        <div style="font-size:36px;font-weight:800;font-variant-numeric:tabular-nums" id="pomoDisp">25:00</div>
        <div class="row">
          <button class="btn btn-primary btn-sm" id="pStart">${state.pomodoro.running ? "Pause" : "Start Pomodoro"}</button>
          <button class="btn btn-secondary btn-sm" id="pReset">Reset</button>
        </div>
      </div>
    </div>

    <div class="grid grid-2" style="margin-bottom:16px">
      <div class="card">
        <h3>Log Study Hours Manually</h3>
        <form id="manualStudyForm" class="stack" style="margin-top:12px">
          <div class="row">
            <select class="select" name="subject_id">
              <option value="">General Study</option>
              ${subjs.subjects.map(s => `<option value="${s.id}">${esc(s.name)}</option>`).join("")}
            </select>
            <input class="input" type="date" name="session_date" value="${new Date().toISOString().slice(0,10)}" />
          </div>
          <div class="row">
            <input class="input" type="number" name="hours" min="0" max="16" value="1" placeholder="Hours" />
            <input class="input" type="number" name="minutes" min="0" max="59" value="30" placeholder="Minutes" />
          </div>
          <input class="input" name="note" placeholder="Daily study note (topics covered)..." />
          <button class="btn btn-primary" type="submit">Save Study Session</button>
        </form>
      </div>
      <div class="card">
        <h3>14-Day Study Trend</h3>
        <div id="trendChart" style="margin-top:10px"></div>
      </div>
    </div>

    <div class="grid grid-2">
      <div class="card">
        <h3>Subject-Wise Hours</h3>
        <div id="subjHoursChart" style="margin-top:12px"></div>
      </div>
      <div class="card">
        <h3>Recent Study Sessions</h3>
        <div class="stack" style="margin-top:10px;max-height:280px;overflow-y:auto">
          ${stats.recent_sessions.map(s => `
            <div class="between" style="padding:8px;border:1px solid var(--border);border-radius:8px">
              <div>
                <strong>${esc(s.subject_name)}</strong> • ${fmtMinutes(s.duration_minutes)}
                <div class="muted">${esc(s.session_date)} (${esc(s.source)}) ${s.note ? "— " + esc(s.note) : ""}</div>
              </div>
              <button class="btn btn-ghost btn-sm" data-del-sess="${s.id}">Delete</button>
            </div>
          `).join("") || `<div class="muted">No sessions logged yet.</div>`}
        </div>
      </div>
    </div>
  `;

  renderBarChart(document.getElementById("trendChart"), stats.daily_trend, { targetValue: stats.daily_target_minutes });
  renderHorizontalBars(
    document.getElementById("subjHoursChart"),
    stats.subject_breakdown.map(x => ({ label: esc(x.subject_name), value: +(x.minutes / 60).toFixed(1) })),
    { maxVal: Math.max(1, ...stats.subject_breakdown.map(x => x.minutes / 60)), suffix: "h" }
  );

  // Live timer display sync
  const disp = document.getElementById("liveTimerDisp");
  let baseSec = timerRes.timer ? timerRes.timer.elapsed_seconds : 0;
  let running = timerRes.timer && !timerRes.timer.is_paused;
  const fmtSec = (sec) => {
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    return [h, m, s].map(v => String(v).padStart(2, "0")).join(":");
  };
  disp.textContent = fmtSec(baseSec);
  const tInt = setInterval(() => { if (running && document.getElementById("liveTimerDisp")) { baseSec++; disp.textContent = fmtSec(baseSec); } else if (!document.getElementById("liveTimerDisp")) clearInterval(tInt); }, 1000);

  document.getElementById("tStart").onclick = async () => {
    const sid = document.getElementById("timerSubj").value;
    await api.post("/api/study/timer/start", { subject_id: sid ? Number(sid) : null });
    viewTracker(el);
  };
  document.getElementById("tPause").onclick = async () => { await api.post("/api/study/timer/pause", {}); viewTracker(el); };
  document.getElementById("tResume").onclick = async () => { await api.post("/api/study/timer/resume", {}); viewTracker(el); };
  document.getElementById("tStop").onclick = async () => {
    const note = prompt("Add a quick note for this study session (optional):") || "";
    const r = await api.post("/api/study/timer/stop", { note });
    toast(`Saved ${r.logged_minutes} minutes!`);
    viewTracker(el);
  };

  // Pomodoro controls
  const pDisp = document.getElementById("pomoDisp");
  const updatePomoDisp = () => {
    const m = Math.floor(state.pomodoro.secondsLeft / 60), s = state.pomodoro.secondsLeft % 60;
    if (pDisp) pDisp.textContent = `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  };
  updatePomoDisp();
  document.getElementById("pStart").onclick = () => {
    if (state.pomodoro.running) {
      clearInterval(state.pomodoro.intervalId);
      state.pomodoro.running = false;
    } else {
      state.pomodoro.running = true;
      state.pomodoro.intervalId = setInterval(async () => {
        state.pomodoro.secondsLeft--;
        updatePomoDisp();
        if (state.pomodoro.secondsLeft <= 0) {
          clearInterval(state.pomodoro.intervalId);
          state.pomodoro.running = false;
          if (state.pomodoro.mode === "study") {
            await api.post("/api/study/sessions", { duration_minutes: state.pomodoro.customStudyMins, source: "pomodoro", note: "Pomodoro session" });
            toast("Pomodoro complete! Logged study session. Starting 5-min break.");
            state.pomodoro.mode = "break";
            state.pomodoro.secondsLeft = 5 * 60;
          } else {
            state.pomodoro.mode = "study";
            state.pomodoro.secondsLeft = state.pomodoro.customStudyMins * 60;
          }
          viewTracker(el);
        }
      }, 1000);
    }
    document.getElementById("pStart").textContent = state.pomodoro.running ? "Pause" : "Start Pomodoro";
  };
  document.getElementById("pReset").onclick = () => {
    clearInterval(state.pomodoro.intervalId);
    state.pomodoro.running = false;
    state.pomodoro.mode = "study";
    state.pomodoro.customStudyMins = Number(document.getElementById("pomoCustomMins").value) || 25;
    state.pomodoro.secondsLeft = state.pomodoro.customStudyMins * 60;
    viewTracker(el);
  };

  document.getElementById("manualStudyForm").onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const totalMins = (Number(fd.get("hours")) || 0) * 60 + (Number(fd.get("minutes")) || 0);
    if (totalMins <= 0) return toast("Enter at least 1 minute.", true);
    try {
      await api.post("/api/study/sessions", {
        subject_id: fd.get("subject_id") ? Number(fd.get("subject_id")) : null,
        duration_minutes: totalMins,
        session_date: fd.get("session_date"),
        source: "manual",
        note: fd.get("note") || "",
      });
      toast("Study session saved!");
      viewTracker(el);
    } catch (err) { toast(err.message, true); }
  };

  el.querySelectorAll("[data-del-sess]").forEach(b => {
    b.onclick = async () => { await api.del(`/api/study/sessions/${b.dataset.delSess}`); viewTracker(el); };
  });
}

// ---------------- VIEW 4: MY BACKLOGS ----------------
async function viewBacklogs(el) {
  const [data, subjs] = await Promise.all([api.get("/api/backlogs"), api.get("/api/subjects")]);
  const st = data.stats;
  const pct = st.total > 0 ? Math.round((st.completed / st.total) * 100) : 0;

  el.innerHTML = `
    <div class="grid grid-4" style="margin-bottom:16px">
      <div class="card"><div class="muted">Total Backlogs</div><div style="font-size:26px;font-weight:700">${st.total}</div></div>
      <div class="card"><div class="muted">High Priority</div><div style="font-size:26px;font-weight:700;color:var(--red)">${st.high_priority}</div></div>
      <div class="card"><div class="muted">Overdue</div><div style="font-size:26px;font-weight:700;color:var(--amber)">${st.overdue}</div></div>
      <div class="card"><div class="muted">Completed</div><div style="font-size:26px;font-weight:700;color:var(--green)">${st.completed}</div></div>
    </div>

    <div class="grid grid-2" style="margin-bottom:16px">
      <div class="card">
        <h3>Add Pending Chapter to Backlogs</h3>
        <form id="backlogForm" class="stack" style="margin-top:12px">
          <div class="row">
            <select class="select" name="subject_name" required>
              ${subjs.subjects.map(s => `<option value="${esc(s.name)}">${esc(s.name)}</option>`).join("")}
            </select>
            <select class="select" name="priority">
              <option value="High">High Priority</option>
              <option value="Medium" selected>Medium Priority</option>
              <option value="Low">Low Priority</option>
            </select>
          </div>
          <input class="input" name="chapter_title" required placeholder="Chapter title..." />
          <div class="row">
            <input class="input" type="date" name="target_date" required value="${new Date(Date.now() + 7 * 86400000).toISOString().slice(0,10)}" />
            <button class="btn btn-primary" type="submit">Add Backlog</button>
          </div>
        </form>
      </div>
      <div class="card between">
        <div>
          <h3>Backlog Completion</h3>
          <p class="muted">Suggested for today's study plan:</p>
          <ul style="margin-top:8px;padding-left:18px;font-size:13px">
            ${data.suggested_for_today.map(s => `<li><strong>${esc(s.subject_name)}</strong>: ${esc(s.chapter_title)} (${esc(s.priority)})</li>`).join("") || `<li class="muted">All caught up!</li>`}
          </ul>
        </div>
        <div id="backlogDonut"></div>
      </div>
    </div>

    <div class="card stack">
      <h3>Backlog List</h3>
      ${data.backlogs.map(b => `
        <div class="between" style="padding:12px;border:1px solid var(--border);border-radius:10px">
          <div class="row">
            <input type="checkbox" data-toggle-bl="${b.id}" ${b.is_completed ? "checked" : ""} />
            <div>
              <strong style="${b.is_completed ? "text-decoration:line-through;opacity:0.6" : ""}">${esc(b.chapter_title)}</strong>
              <div class="muted">${esc(b.subject_name)} • Target: ${esc(b.target_date)}</div>
            </div>
          </div>
          <div class="row">
            <span class="badge ${b.priority === "High" ? "badge-red" : b.priority === "Medium" ? "badge-amber" : "badge-indigo"}">${esc(b.priority)}</span>
            <button class="btn btn-ghost btn-sm" data-del-bl="${b.id}">Remove</button>
          </div>
        </div>
      `).join("") || `<div class="muted">No backlogs added yet.</div>`}
    </div>
  `;

  renderDonutChart(document.getElementById("backlogDonut"), pct, "Cleared", "var(--green)");
  document.getElementById("backlogForm").onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    await api.post("/api/backlogs", {
      subject_name: fd.get("subject_name"),
      chapter_title: fd.get("chapter_title"),
      priority: fd.get("priority"),
      target_date: fd.get("target_date"),
    });
    viewBacklogs(el);
  };
  el.querySelectorAll("[data-toggle-bl]").forEach(c => {
    c.onchange = async () => { await api.put(`/api/backlogs/${c.dataset.toggleBl}`, { is_completed: c.checked }); viewBacklogs(el); };
  });
  el.querySelectorAll("[data-del-bl]").forEach(b => {
    b.onclick = async () => { await api.del(`/api/backlogs/${b.dataset.delBl}`); viewBacklogs(el); };
  });
}

// ---------------- VIEW 5: REVISION ----------------
async function viewRevision(el) {
  const data = await api.get("/api/revisions");
  el.innerHTML = `
    <div class="card" style="margin-bottom:16px">
      <div class="between" style="flex-wrap:wrap;gap:12px">
        <div>
          <h3>Spaced Repetition Schedule</h3>
          <p class="muted">Automatically triggered when you confirm a chapter as Full Chapter Completed. Current intervals (days): <strong>${data.intervals.join(" → ")}</strong></p>
        </div>
        <form id="revSetForm" class="row">
          <input class="input" name="intervals" value="${data.intervals.join(", ")}" style="width:160px" />
          <button class="btn btn-secondary btn-sm" type="submit">Update Intervals</button>
        </form>
      </div>
    </div>

    ${data.overdue.length ? `
      <div class="card" style="margin-bottom:16px;border-color:var(--red)">
        <h3 style="color:var(--red)">Overdue Revisions (${data.overdue.length})</h3>
        <div class="stack" style="margin-top:10px">
          ${data.overdue.map(r => `
            <div class="between" style="padding:10px;border:1px solid var(--border);border-radius:10px">
              <div><strong>${esc(r.subject_name)} — ${esc(r.chapter_title)}</strong><div class="muted">Revision #${r.revision_number} • Due ${esc(r.due_date)}</div></div>
              <button class="btn btn-success btn-sm" data-rev-done="${r.id}">Mark as Revised</button>
            </div>
          `).join("")}
        </div>
      </div>
    ` : ""}

    <div class="grid grid-2">
      <div class="card">
        <h3>Upcoming Revisions (${data.upcoming.length})</h3>
        <div class="stack" style="margin-top:10px">
          ${data.upcoming.map(r => `
            <div class="between" style="padding:10px;border:1px solid var(--border);border-radius:10px">
              <div><strong>${esc(r.subject_name)} — ${esc(r.chapter_title)}</strong><div class="muted">Revision #${r.revision_number} • Due ${esc(r.due_date)}</div></div>
              <button class="btn btn-primary btn-sm" data-rev-done="${r.id}">Mark as Revised</button>
            </div>
          `).join("") || `<div class="muted">Mark chapters as "Full Chapter Completed" in My Subjects to auto-schedule revisions!</div>`}
        </div>
      </div>
      <div class="card">
        <h3>Revision History (${data.history.length})</h3>
        <div class="stack" style="margin-top:10px;max-height:320px;overflow-y:auto">
          ${data.history.map(r => `
            <div class="between" style="padding:8px;border:1px solid var(--border);border-radius:8px">
              <div><strong>${esc(r.subject_name)}</strong>: ${esc(r.chapter_title)} <span class="badge badge-green">Rev #${r.revision_number}</span></div>
              <span class="muted">${(r.completed_at || "").slice(0, 10)}</span>
            </div>
          `).join("") || `<div class="muted">No completed revisions yet.</div>`}
        </div>
      </div>
    </div>
  `;

  document.getElementById("revSetForm").onsubmit = async (e) => {
    e.preventDefault();
    const raw = new FormData(e.target).get("intervals");
    const arr = raw.split(",").map(x => parseInt(x.trim(), 10)).filter(n => n > 0);
    await api.put("/api/revisions/settings", { intervals: arr });
    toast("Revision intervals updated!");
    viewRevision(el);
  };
  el.querySelectorAll("[data-rev-done]").forEach(b => {
    b.onclick = async () => { await api.post(`/api/revisions/${b.dataset.revDone}/complete`, {}); toast("Marked as revised!"); viewRevision(el); };
  });
}

// ---------------- VIEW 6: COMMUNITY ----------------
async function viewCommunity(el) {
  el.innerHTML = `
    <div class="row" style="margin-bottom:14px;flex-wrap:wrap">
      <button class="btn btn-primary btn-sm" data-ctab="chat">Public Chat</button>
      <button class="btn btn-secondary btn-sm" data-ctab="accountability">6-Hour Challenge Feed</button>
      <button class="btn btn-secondary btn-sm" data-ctab="support">Support &amp; Study Buddies</button>
      ${["moderator", "admin"].includes(state.user.role) ? `<button class="btn btn-secondary btn-sm" data-ctab="mod">Moderator Queue</button>` : ""}
    </div>
    <div id="commBody"></div>
  `;
  const body = document.getElementById("commBody");
  const openTab = async (tab) => {
    el.querySelectorAll("[data-ctab]").forEach(b => {
      b.className = `btn btn-sm ${b.dataset.ctab === tab ? "btn-primary" : "btn-secondary"}`;
    });
    if (tab === "chat") await renderChatTab(body);
    else if (tab === "accountability") await renderAccountabilityTab(body);
    else if (tab === "support") await renderSupportBuddiesTab(body);
    else if (tab === "mod") await renderModQueueTab(body);
  };
  el.querySelectorAll("[data-ctab]").forEach(b => b.onclick = () => openTab(b.dataset.ctab));
  await openTab("chat");
}

async function renderChatTab(el) {
  const chRes = await api.get("/api/chat/channels");
  const channels = chRes.channels;
  if (!state.activeChannelId && channels.length) state.activeChannelId = channels[0].id;
  const acad = channels.filter(c => c.category === "ACADEMIC");
  const nonAcad = channels.filter(c => c.category === "NON-ACADEMIC");

  el.innerHTML = `
    <div class="chat-shell">
      <div class="chat-channels stack">
        <div class="muted" style="font-weight:700;font-size:11px">A. ACADEMIC</div>
        ${acad.map(c => `<button class="nav-item ${c.id === state.activeChannelId ? "active" : ""}" data-ch="${c.id}"># ${esc(c.name)} ${c.unread_count ? `<span class="badge badge-indigo">${c.unread_count}</span>` : ""}</button>`).join("")}
        <div class="muted" style="font-weight:700;font-size:11px;margin-top:8px">B. NON-ACADEMIC</div>
        ${nonAcad.map(c => `<button class="nav-item ${c.id === state.activeChannelId ? "active" : ""}" data-ch="${c.id}"># ${esc(c.name)} ${c.unread_count ? `<span class="badge badge-purple">${c.unread_count}</span>` : ""}</button>`).join("")}
      </div>
      <div class="chat-main">
        <div class="between" style="padding:12px 16px;border-bottom:1px solid var(--border)">
          <strong id="chHeader">Channel</strong>
          <input class="input" id="chatSearch" placeholder="Search messages..." style="width:200px" />
        </div>
        <div id="pinnedBar" class="hidden" style="padding:8px 16px;background:var(--indigo-soft);font-size:12px"></div>
        <div class="chat-messages" id="chatMsgs"></div>
        <form id="chatForm" class="row" style="padding:12px;border-top:1px solid var(--border)">
          <input class="input" id="chatInput" placeholder="Write a respectful message..." required />
          <button class="btn btn-primary" type="submit">Send</button>
        </form>
      </div>
    </div>
  `;

  el.querySelectorAll("[data-ch]").forEach(b => {
    b.onclick = () => { state.activeChannelId = Number(b.dataset.ch); renderChatTab(el); };
  });

  const loadMsgs = async () => {
    const q = document.getElementById("chatSearch").value;
    const data = await api.get(`/api/chat/channels/${state.activeChannelId}/messages?q=${encodeURIComponent(q)}`);
    document.getElementById("chHeader").textContent = `# ${data.channel.name} (${data.channel.category}) ${data.channel.is_locked ? "🔒 Locked" : ""}`;
    const pinBar = document.getElementById("pinnedBar");
    if (data.pinned.length) {
      pinBar.classList.remove("hidden");
      pinBar.innerHTML = `📌 <strong>Pinned:</strong> ${esc(data.pinned[0].username)}: ${esc(data.pinned[0].content)}`;
    } else pinBar.classList.add("hidden");

    const box = document.getElementById("chatMsgs");
    box.innerHTML = data.messages.map(m => renderSingleMsg(m)).join("") || `<div class="muted">No messages yet. Start the discussion!</div>`;
    box.scrollTop = box.scrollHeight;
    bindMsgActions(box, loadMsgs);
  };

  if (state.chatWs) state.chatWs.close();
  state.chatWs = api.connectChatWS(state.activeChannelId, (ev) => {
    if (ev.type === "new_message" || ev.type === "edit_message" || ev.type === "delete_message" || ev.type === "reactions") {
      if (document.getElementById("chatMsgs")) loadMsgs();
    }
  });

  document.getElementById("chatSearch").oninput = loadMsgs;
  document.getElementById("chatForm").onsubmit = async (e) => {
    e.preventDefault();
    const inp = document.getElementById("chatInput");
    try {
      const r = await api.post(`/api/chat/channels/${state.activeChannelId}/messages`, { content: inp.value });
      if (r.flagged) toast("Message flagged for moderator review. Please keep discussions respectful.", true);
      inp.value = "";
      loadMsgs();
    } catch (err) { toast(err.message, true); }
  };
  await loadMsgs();
}

function renderSingleMsg(m) {
  const mine = m.user_id === state.user.id;
  const canMod = ["moderator", "admin"].includes(state.user.role);
  return `
    <div class="chat-msg">
      ${avatarHtml(m)}
      <div style="flex:1;min-width:0">
        <div class="between">
          <div class="row" style="gap:6px">
            <strong>${esc(m.full_name)}</strong>
            <span class="muted">@${esc(m.username)}</span>
            ${m.user_role === "moderator" ? `<span class="badge badge-purple">Mod</span>` : ""}
            ${m.user_role === "admin" ? `<span class="badge badge-indigo">Admin</span>` : ""}
            <span class="muted" style="font-size:11px">${(m.created_at || "").slice(11, 16)} ${m.is_edited ? "(edited)" : ""}</span>
          </div>
          <div class="row" style="gap:4px">
            <button class="btn btn-ghost btn-sm" data-react="${m.id}">👍</button>
            ${mine ? `<button class="btn btn-ghost btn-sm" data-edit="${m.id}">Edit</button>` : ""}
            ${mine || canMod ? `<button class="btn btn-ghost btn-sm" data-del="${m.id}">Del</button>` : ""}
            ${!mine ? `<button class="btn btn-ghost btn-sm" data-rep="${m.id}">Report</button>` : ""}
          </div>
        </div>
        <div style="margin-top:2px;word-break:break-word">${esc(m.content)}</div>
        ${m.reactions && m.reactions.length ? `<div class="row" style="margin-top:4px">${m.reactions.map(r => `<span class="badge badge-indigo">${esc(r.emoji)} ${r.cnt}</span>`).join("")}</div>` : ""}
      </div>
    </div>
  `;
}

function bindMsgActions(box, reload) {
  box.querySelectorAll("[data-react]").forEach(b => b.onclick = async () => { await api.post(`/api/chat/messages/${b.dataset.react}/react`, { emoji: "👍" }); reload(); });
  box.querySelectorAll("[data-edit]").forEach(b => b.onclick = async () => {
    const txt = prompt("Edit your message:");
    if (txt) { await api.put(`/api/chat/messages/${b.dataset.edit}`, { content: txt }); reload(); }
  });
  box.querySelectorAll("[data-del]").forEach(b => b.onclick = async () => { await api.del(`/api/chat/messages/${b.dataset.del}`); reload(); });
  box.querySelectorAll("[data-rep]").forEach(b => b.onclick = async () => {
    const reason = prompt("Reason for reporting this message:");
    if (reason) { await api.post(`/api/chat/messages/${b.dataset.rep}/report`, { reason }); toast("Reported to moderators."); }
  });
}

async function renderAccountabilityTab(el) {
  const [st, feed] = await Promise.all([api.get("/api/accountability/status"), api.get("/api/accountability/posts")]);
  el.innerHTML = `
    <div class="card" style="margin-bottom:16px">
      <h3>6-Hour Challenge Accountability</h3>
      <p class="muted" style="margin:4px 0 12px">${esc(st.prompt_message)} (Studied: ${fmtMinutes(st.studied_minutes)} / Target: ${fmtMinutes(st.target_minutes)})</p>
      <form id="accForm" class="stack">
        <select class="select" name="reason_category">
          <option>Lack of concentration</option>
          <option>Procrastination</option>
          <option>Health or personal issues</option>
          <option>Family responsibilities</option>
          <option>School or coaching workload</option>
          <option>Low motivation</option>
          <option>Unexpected circumstances</option>
          <option>Other</option>
        </select>
        <textarea class="textarea" name="explanation" rows="2" placeholder="Write your honest reflection (optional)..."></textarea>
        <div class="between" style="flex-wrap:wrap">
          <label class="row"><input type="checkbox" name="no_explanation" /> <span class="muted">No explanation today (respect privacy)</span></label>
          <label class="row"><input type="checkbox" name="request_suggestions" checked /> <span class="muted">Request suggestions from peers</span></label>
          <button class="btn btn-primary" type="submit">Publish Accountability Post</button>
        </div>
      </form>
    </div>
    <div class="stack">
      ${feed.posts.map(p => `
        <div class="card">
          <div class="between">
            <div class="row">${avatarHtml(p)}<div><strong>${esc(p.full_name)}</strong> <span class="muted">@${esc(p.username)}</span><div class="muted">${fmtMinutes(p.studied_minutes)} / ${fmtMinutes(p.target_minutes)} • ${esc(p.post_date)}</div></div></div>
            <span class="badge badge-amber">${esc(p.reason_category)}</span>
          </div>
          ${!p.no_explanation && p.explanation ? `<p style="margin:10px 0">${esc(p.explanation)}</p>` : `<p class="muted" style="margin:8px 0">Student chose not to share details today.</p>`}
          <div class="stack" style="margin-top:8px;padding-left:12px;border-left:2px solid var(--border)">
            ${p.replies.map(r => `<div class="muted"><strong style="color:var(--text)">${esc(r.full_name)}</strong> (${esc(r.reply_type)}): ${esc(r.message)}</div>`).join("")}
            <form class="row acc-reply-form" data-pid="${p.id}">
              <input class="input" name="msg" placeholder="Encourage, suggest a technique, or invite to study..." required />
              <button class="btn btn-secondary btn-sm" type="submit">Reply</button>
            </form>
          </div>
        </div>
      `).join("")}
    </div>
  `;
  document.getElementById("accForm").onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    await api.post("/api/accountability/posts", {
      reason_category: fd.get("reason_category"),
      explanation: fd.get("explanation") || "",
      no_explanation: fd.get("no_explanation") === "on",
      request_suggestions: fd.get("request_suggestions") === "on",
    });
    toast("Accountability post published!");
    renderAccountabilityTab(el);
  };
  el.querySelectorAll(".acc-reply-form").forEach(f => {
    f.onsubmit = async (e) => {
      e.preventDefault();
      await api.post(`/api/accountability/posts/${f.dataset.pid}/replies`, { message: f.msg.value, reply_type: "suggestion" });
      renderAccountabilityTab(el);
    };
  });
}

async function renderSupportBuddiesTab(el) {
  const [sup, bud] = await Promise.all([api.get("/api/support/posts"), api.get("/api/buddies")]);
  el.innerHTML = `
    <div class="grid grid-2">
      <div class="stack">
        <div class="card">
          <h3>Student Support Feed</h3>
          <form id="supForm" class="stack" style="margin-top:10px">
            <select class="select" name="topic">
              <option>Study Problems</option><option>Lack of Motivation</option><option>Backlogs</option><option>Time Management</option><option>Exam Stress</option>
            </select>
            <input class="input" name="title" required placeholder="Post title..." />
            <textarea class="textarea" name="content" rows="2" required placeholder="Share what you're facing..."></textarea>
            <button class="btn btn-primary btn-sm" type="submit">Post to Support Feed</button>
          </form>
        </div>
        ${sup.posts.map(p => `
          <div class="card">
            <div class="between"><strong>${esc(p.title)}</strong><span class="badge badge-indigo">${esc(p.topic)}</span></div>
            <div class="muted">${esc(p.full_name)} (@${esc(p.username)})</div>
            <p style="margin:8px 0">${esc(p.content)}</p>
            ${p.comments.map(c => `<div class="muted" style="margin-top:4px"><strong style="color:var(--text)">${esc(c.full_name)}:</strong> ${esc(c.content)}</div>`).join("")}
            <form class="row sup-cmt" data-sid="${p.id}" style="margin-top:8px">
              <input class="input" name="c" placeholder="Write a helpful comment..." required />
              <button class="btn btn-secondary btn-sm" type="submit">Comment</button>
            </form>
          </div>
        `).join("")}
      </div>

      <div class="stack">
        <div class="card">
          <h3>Find a Study Buddy</h3>
          <div class="row" style="margin-top:10px">
            <input class="input" id="budSearch" placeholder="Search by name or username..." />
            <button class="btn btn-secondary btn-sm" id="budSearchBtn">Search</button>
          </div>
          <div id="budResults" class="stack" style="margin-top:10px"></div>
        </div>
        <div class="card">
          <h3>My Study Buddies &amp; Requests</h3>
          <div class="stack" style="margin-top:10px">
            ${bud.buddies.map(b => {
              const incoming = b.receiver_id === state.user.id && b.status === "pending";
              const partnerName = b.requester_id === state.user.id ? b.rec_name : b.req_name;
              return `
                <div class="between" style="padding:10px;border:1px solid var(--border);border-radius:10px">
                  <div>
                    <strong>${esc(partnerName)}</strong>
                    <div class="muted">Status: ${esc(b.status)} • Partner today: ${fmtMinutes(b.partner_today_minutes)}</div>
                  </div>
                  ${incoming ? `
                    <div class="row">
                      <button class="btn btn-success btn-sm" data-bresp="${b.id}" data-act="accepted">Accept</button>
                      <button class="btn btn-ghost btn-sm" data-bresp="${b.id}" data-act="rejected">Reject</button>
                    </div>
                  ` : `<span class="badge badge-indigo">${esc(b.status)}</span>`}
                </div>
              `;
            }).join("") || `<div class="muted">No study buddies yet.</div>`}
          </div>
        </div>
      </div>
    </div>
  `;
  document.getElementById("supForm").onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    await api.post("/api/support/posts", { topic: fd.get("topic"), title: fd.get("title"), content: fd.get("content") });
    renderSupportBuddiesTab(el);
  };
  el.querySelectorAll(".sup-cmt").forEach(f => f.onsubmit = async (e) => {
    e.preventDefault();
    await api.post(`/api/support/posts/${f.dataset.sid}/comments`, { content: f.c.value });
    renderSupportBuddiesTab(el);
  });
  document.getElementById("budSearchBtn").onclick = async () => {
    const q = document.getElementById("budSearch").value;
    const r = await api.get(`/api/buddies/search?q=${encodeURIComponent(q)}`);
    const box = document.getElementById("budResults");
    box.innerHTML = r.students.map(s => `
      <div class="between" style="padding:8px;border:1px solid var(--border);border-radius:8px">
        <div><strong>${esc(s.full_name)}</strong> <span class="muted">@${esc(s.username)} (${esc(s.board || "")})</span></div>
        <button class="btn btn-primary btn-sm" data-breq="${s.id}">Invite</button>
      </div>
    `).join("") || `<div class="muted">No matching students.</div>`;
    box.querySelectorAll("[data-breq]").forEach(b => b.onclick = async () => {
      await api.post("/api/buddies/request", { receiver_id: Number(b.dataset.breq) });
      toast("Study buddy request sent!");
      renderSupportBuddiesTab(el);
    });
  };
  el.querySelectorAll("[data-bresp]").forEach(b => b.onclick = async () => {
    await api.post(`/api/buddies/${b.dataset.bresp}/respond`, { action: b.dataset.act });
    renderSupportBuddiesTab(el);
  });
}

async function renderModQueueTab(el) {
  const res = await api.get("/api/mod/reports");
  el.innerHTML = `
    <div class="card stack">
      <h3>Moderator Review Queue</h3>
      ${res.reports.map(r => `
        <div class="between" style="padding:12px;border:1px solid var(--border);border-radius:10px;flex-wrap:wrap;gap:10px">
          <div>
            <strong>@${esc(r.author_username)}</strong> in #${esc(r.channel_name)}: “${esc(r.message_content)}”
            <div class="muted">Reported by @${esc(r.reporter_username)} — ${esc(r.reason)} (${esc(r.status)})</div>
          </div>
          ${r.status === "pending" ? `
            <div class="row">
              <button class="btn btn-danger btn-sm" data-mact="delete_message" data-rid="${r.id}">Delete</button>
              <button class="btn btn-secondary btn-sm" data-mact="warn" data-rid="${r.id}">Warn</button>
              <button class="btn btn-secondary btn-sm" data-mact="mute_24h" data-rid="${r.id}">Mute 24h</button>
              <button class="btn btn-ghost btn-sm" data-mact="dismiss" data-rid="${r.id}">Dismiss</button>
            </div>
          ` : ""}
        </div>
      `).join("") || `<div class="muted">No reports in queue.</div>`}
    </div>
  `;
  el.querySelectorAll("[data-mact]").forEach(b => b.onclick = async () => {
    await api.post(`/api/mod/reports/${b.dataset.rid}/resolve`, { action: b.dataset.mact });
    toast("Moderation action applied.");
    renderModQueueTab(el);
  });
}

// ---------------- VIEW 7: LEADERBOARD & DAILY 11 PM REPORT ----------------
async function viewLeaderboard(el) {
  const [lb, hist, daily] = await Promise.all([
    api.get("/api/leaderboard"),
    api.get("/api/leaderboard/history"),
    api.get("/api/reports/daily"),
  ]);
  const me = lb.my_entry;
  const top3 = lb.leaderboard.slice(0, 3);

  el.innerHTML = `
    <div class="card" style="margin-bottom:16px">
      <div class="between" style="flex-wrap:wrap;gap:12px">
        <div>
          <h3>Weekly Leaderboard (${esc(lb.week_start)} to ${esc(lb.week_end)} IST)</h3>
          <p class="muted">${lb.public_unlocked ? "Sunday Public Leaderboard Unlocked!" : "Monday–Saturday Privacy Mode: Only your personal rank and weekly progress are visible until Sunday."}</p>
        </div>
        ${me ? `<span class="badge badge-indigo" style="font-size:14px">Your Rank: #${me.rank} of ${lb.total_participants}</span>` : ""}
      </div>
      ${me ? `
        <div class="grid grid-4" style="margin-top:14px">
          <div><div class="muted">Weekly Study</div><strong>${fmtMinutes(me.weekly_minutes)}</strong></div>
          <div><div class="muted">vs Last Week</div><strong>${fmtMinutes(me.prev_weekly_minutes)}</strong></div>
          <div><div class="muted">Consistency</div><strong>${me.consistency_pct}% (${me.days_met_goal}/7d)</strong></div>
          <div><div class="muted">Syllabus</div><strong>${me.syllabus_pct}%</strong></div>
        </div>
      ` : ""}
    </div>

    ${lb.public_unlocked && lb.leaderboard.length ? `
      <div class="podium">
        ${top3.map((e, i) => `
          <div class="podium-card ${i === 0 ? "podium-1" : ""}">
            <div style="font-size:20px;font-weight:800">#${e.rank}</div>
            <div style="margin:8px auto">${avatarHtml(e)}</div>
            <strong>${esc(e.full_name)}</strong>
            <div class="muted">${fmtMinutes(e.weekly_minutes)} • ${e.syllabus_pct}% syllabus</div>
            <div class="badge badge-green" style="margin-top:6px">Score: ${e.combined_score}</div>
          </div>
        `).join("")}
      </div>
      <div class="card" style="margin-bottom:16px">
        <h3>Weekly Combined Score Breakdown</h3>
        <div id="lbBars" style="margin-top:12px"></div>
      </div>
    ` : ""}

    <div class="grid grid-2">
      <div class="card">
        <h3>11:00 PM IST Daily Community Report (${esc(daily.report_date)})</h3>
        <div class="row" style="margin:10px 0">
          <span class="badge badge-indigo">Total: ${fmtMinutes(daily.total_minutes)}</span>
          <span class="badge badge-purple">Avg: ${fmtMinutes(daily.avg_minutes)}</span>
          <span class="badge badge-green">Highest: ${fmtMinutes(daily.highest_minutes)}</span>
        </div>
        <div class="stack" style="max-height:260px;overflow-y:auto">
          ${daily.students.map(s => `
            <div class="between" style="padding:8px;border:1px solid var(--border);border-radius:8px">
              <div class="row">${avatarHtml(s)}<span>${esc(s.full_name)}</span></div>
              <span class="badge ${s.reported ? (s.target_achieved ? "badge-green" : "badge-indigo") : "badge-amber"}">${esc(s.status_label)}</span>
            </div>
          `).join("")}
        </div>
      </div>

      <div class="card">
        <h3>Historical Weekly Archives</h3>
        <div class="stack" style="margin-top:10px">
          ${hist.archives.map(a => `
            <div style="padding:10px;border:1px solid var(--border);border-radius:8px">
              <strong>Week ${esc(a.week_start)} to ${esc(a.week_end)}</strong>
              <div class="muted">${a.data.entries.slice(0, 3).map(e => `#${e.rank} ${esc(e.full_name)} (${fmtMinutes(e.weekly_minutes)})`).join(" • ")}</div>
            </div>
          `).join("") || `<div class="muted">Archives will appear after Sunday's weekly reset.</div>`}
        </div>
      </div>
    </div>
  `;

  if (lb.public_unlocked && lb.leaderboard.length) {
    renderHorizontalBars(
      document.getElementById("lbBars"),
      lb.leaderboard.slice(0, 10).map(e => ({ label: `#${e.rank} ${esc(e.full_name)} (${fmtMinutes(e.weekly_minutes)})`, value: e.combined_score })),
      { maxVal: Math.max(10, ...lb.leaderboard.map(e => e.combined_score)), suffix: " pts" }
    );
  }
}

// ---------------- VIEW 8: CALENDAR & PLANNER ----------------
async function viewCalendar(el) {
  const [cal, plan, subjs] = await Promise.all([api.get("/api/calendar"), api.get("/api/planner"), api.get("/api/subjects")]);
  el.innerHTML = `
    <div class="grid grid-2">
      <div class="card stack">
        <h3>Daily Study Planner (${esc(plan.date)})</h3>
        <form id="planForm" class="stack">
          <div class="row">
            <select class="select" name="subject_name">
              ${subjs.subjects.map(s => `<option value="${esc(s.name)}">${esc(s.name)}</option>`).join("")}
            </select>
            <input class="input" name="chapter_title" required placeholder="Chapter / Topic..." />
          </div>
          <div class="row">
            <input class="input" name="goal_text" placeholder="Goal (e.g. Solve 20 PYQs)..." />
            <button class="btn btn-primary" type="submit">Add Goal</button>
          </div>
        </form>
        <div class="stack">
          ${plan.plans.map(p => `
            <div class="between" style="padding:10px;border:1px solid var(--border);border-radius:8px">
              <label class="row">
                <input type="checkbox" data-tp="${p.id}" ${p.is_completed ? "checked" : ""} />
                <span><strong>${esc(p.subject_name)}</strong>: ${esc(p.chapter_title)} ${p.goal_text ? "— " + esc(p.goal_text) : ""}</span>
              </label>
              <button class="btn btn-ghost btn-sm" data-dp="${p.id}">✕</button>
            </div>
          `).join("") || `<div class="muted">No goals planned for today.</div>`}
        </div>
      </div>

      <div class="card stack">
        <h3>Study Calendar (${esc(cal.month)})</h3>
        <div><strong>Study Days Logged:</strong> ${cal.study_days.length} day(s)</div>
        <div><strong>Exams This Month:</strong> ${cal.exams.map(e => `${esc(e.name)} (${esc(e.date)})`).join(", ") || "None this month"}</div>
        <div><strong>Backlog Deadlines:</strong> ${cal.backlogs.map(b => `${esc(b.chapter_title)} (${esc(b.date)})`).join(", ") || "None"}</div>
        <div><strong>Pending Revisions:</strong> ${cal.revisions.map(r => `${esc(r.chapter_title)} (${esc(r.date)})`).join(", ") || "None"}</div>
      </div>
    </div>
  `;
  document.getElementById("planForm").onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    await api.post("/api/planner", { subject_name: fd.get("subject_name"), chapter_title: fd.get("chapter_title"), goal_text: fd.get("goal_text") });
    viewCalendar(el);
  };
  el.querySelectorAll("[data-tp]").forEach(c => c.onchange = async () => { await api.put(`/api/planner/${c.dataset.tp}`, { is_completed: c.checked }); viewCalendar(el); });
  el.querySelectorAll("[data-dp]").forEach(b => b.onclick = async () => { await api.del(`/api/planner/${b.dataset.dp}`); viewCalendar(el); });
}

// ---------------- VIEW 9: NOTIFICATIONS ----------------
async function viewNotifications(el) {
  const res = await api.get("/api/notifications");
  el.innerHTML = `
    <div class="card">
      <div class="between" style="margin-bottom:14px">
        <h3>Notifications (${res.unread_count} unread)</h3>
        <button class="btn btn-secondary btn-sm" id="readAllBtn">Mark All Read</button>
      </div>
      <div class="stack">
        ${res.notifications.map(n => `
          <div style="padding:12px;border:1px solid var(--border);border-radius:10px;opacity:${n.is_read ? 0.7 : 1}">
            <div class="between"><strong>${esc(n.title)}</strong><span class="muted">${(n.created_at || "").slice(0, 16).replace("T", " ")}</span></div>
            <div class="muted" style="margin-top:4px">${esc(n.body)}</div>
          </div>
        `).join("") || `<div class="muted">You're all caught up!</div>`}
      </div>
    </div>
  `;
  document.getElementById("readAllBtn").onclick = async () => {
    await api.post("/api/notifications/read-all", {});
    viewNotifications(el);
  };
}

// ---------------- VIEW 10: PROFILE ----------------
async function viewProfile(el) {
  const res = await api.get(`/api/profile/${encodeURIComponent(state.user.username)}`);
  const p = res.profile;
  const st = res.stats;
  const BADGE_NAMES = {
    "7_day_streak": "🔥 7-Day Streak",
    "consistency_champion": "⚡ Consistency Champion",
    "pyq_master": "📝 PYQ Master",
    "chapter_crusher": "📘 Chapter Crusher",
    "weekly_topper": "🏆 Weekly Topper",
    "backlog_finisher": "📌 Backlog Finisher",
    "study_buddy": "🤝 Study Buddy",
  };

  el.innerHTML = `
    <div class="grid grid-2">
      <div class="card stack">
        <div class="row">
          ${avatarHtml(p, true)}
          <div>
            <h2>${esc(p.full_name)}</h2>
            <div class="muted">@${esc(p.username)} • ${esc(p.board)} • ${esc(p.stream)}</div>
            <div class="row" style="margin-top:8px">
              <label class="btn btn-secondary btn-sm" style="cursor:pointer">
                Upload Photo <input type="file" id="avUpload" accept="image/*" class="hidden" />
              </label>
              ${p.profile_pic ? `<button class="btn btn-ghost btn-sm" id="avRemove">Remove</button>` : ""}
            </div>
          </div>
        </div>
        <form id="profForm" class="stack">
          <div><label class="muted">Full Name</label><input class="input" name="full_name" value="${esc(p.full_name)}" /></div>
          <div><label class="muted">Username</label><input class="input" name="username" value="${esc(p.username)}" /></div>
          <div><label class="muted">Daily Study Target (minutes)</label><input class="input" type="number" name="daily_target_minutes" min="60" max="960" value="${p.daily_target_minutes}" /></div>
          <div><label class="muted">Bio</label><textarea class="textarea" name="bio" rows="2">${esc(p.bio || "")}</textarea></div>
          <label class="row"><input type="checkbox" name="allow_buddy_requests" ${p.allow_buddy_requests ? "checked" : ""} /> <span class="muted">Allow Study Buddy requests</span></label>
          <button class="btn btn-primary" type="submit">Save Profile</button>
        </form>
      </div>

      <div class="stack">
        <div class="card">
          <h3>Academic &amp; Study Summary</h3>
          <div class="grid grid-2" style="margin-top:12px">
            <div><div class="muted">Total Study</div><strong>${fmtMinutes(st.total_minutes)}</strong></div>
            <div><div class="muted">Weekly Study</div><strong>${fmtMinutes(st.weekly_minutes)}</strong></div>
            <div><div class="muted">Streak</div><strong>${st.current_streak}d (Best ${st.longest_streak}d)</strong></div>
            <div><div class="muted">Syllabus</div><strong>${st.syllabus_pct}% (${st.completed_chapters}/${st.total_chapters})</strong></div>
          </div>
        </div>
        <div class="card">
          <h3>Achievement Badges</h3>
          <div class="row" style="flex-wrap:wrap;margin-top:10px">
            ${res.badges.map(b => `<span class="badge badge-green">${BADGE_NAMES[b.badge_key] || esc(b.badge_key)}</span>`).join("") || `<div class="muted">Keep studying to unlock your first badge!</div>`}
          </div>
        </div>
        <div class="card">
          <h3>Change Password</h3>
          <form id="pwForm" class="stack" style="margin-top:10px">
            <input class="input" type="password" name="current_password" required placeholder="Current password" />
            <input class="input" type="password" name="new_password" required minlength="6" placeholder="New password" />
            <button class="btn btn-secondary" type="submit">Update Password</button>
          </form>
        </div>
      </div>
    </div>
  `;

  document.getElementById("avUpload").onchange = async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    const fd = new FormData();
    fd.append("file", f);
    const r = await api.req("/api/profile/avatar", { method: "POST", body: fd });
    state.user.profile_pic = r.profile_pic;
    viewProfile(el);
  };
  const remBtn = document.getElementById("avRemove");
  if (remBtn) remBtn.onclick = async () => { await api.del("/api/profile/avatar"); state.user.profile_pic = ""; viewProfile(el); };

  document.getElementById("profForm").onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    try {
      const r = await api.put("/api/profile", {
        full_name: fd.get("full_name"),
        username: fd.get("username"),
        daily_target_minutes: Number(fd.get("daily_target_minutes")),
        bio: fd.get("bio"),
        allow_buddy_requests: fd.get("allow_buddy_requests") === "on",
      });
      state.user = r.user;
      toast("Profile updated!");
      renderShell();
    } catch (err) { toast(err.message, true); }
  };
  document.getElementById("pwForm").onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    try {
      await api.post("/api/auth/change-password", { current_password: fd.get("current_password"), new_password: fd.get("new_password") });
      toast("Password changed!");
      e.target.reset();
    } catch (err) { toast(err.message, true); }
  };
}

boot();
