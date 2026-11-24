import asyncio
import json
import re
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from fastapi import (
    Body,
    Depends,
    FastAPI,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.auth import (
    create_session,
    generate_recovery_code,
    get_current_user_optional,
    get_user_from_token,
    hash_password,
    require_admin,
    require_mod_or_admin,
    require_user,
    revoke_session,
    verify_password,
)
from app.config import IST, STATIC_DIR, UPLOADS_DIR
from app.database import STREAM_SUBJECTS, get_db, init_db
from app.scheduler import (
    background_scheduler_loop,
    compute_leaderboard_entries,
    generate_daily_report,
    get_quote_of_the_day,
    get_week_bounds,
    ist_now,
    ist_today_str,
    publish_weekly_archive,
)

# --- Moderation & Anti-Spam Helpers ---
OFFENSIVE_PATTERNS = [
    r"\b(idiot|stupid|loser|moron|shut\s*up|dumbass|bastard|bitch|fuck|shit|asshole)\b",
    r"\b(hate\s+you|kill\s+yourself|kys)\b",
]
_RECENT_USER_MSGS: dict[int, list[tuple[str, float]]] = {}


def check_message_safety(user_id: int, content: str) -> tuple[bool, str, bool]:
    """Returns (allowed, error_reason, flagged_offensive)."""
    text = content.strip()
    if not text:
        return False, "Message cannot be empty.", False
    if len(text) > 2000:
        return False, "Message exceeds 2000 character limit.", False

    now_ts = datetime.now(IST).timestamp()
    hist = [item for item in _RECENT_USER_MSGS.get(user_id, []) if now_ts - item[1] < 20]
    # Repeated message prevention
    if any(prev_txt.lower() == text.lower() for prev_txt, _ in hist):
        return False, "Repeated identical message detected. Please avoid spamming.", False
    # Rate limit: max 6 messages per 20s
    if len(hist) >= 6:
        return False, "You are sending messages too quickly. Please wait a few seconds.", False

    hist.append((text, now_ts))
    _RECENT_USER_MSGS[user_id] = hist

    flagged = any(re.search(pat, text, re.IGNORECASE) for pat in OFFENSIVE_PATTERNS)
    return True, "", flagged


# --- WebSocket Connection Manager ---
class ChatConnectionManager:
    def __init__(self):
        self.connections: dict[int, set[WebSocket]] = {}

    async def connect(self, channel_id: int, ws: WebSocket):
        await ws.accept()
        self.connections.setdefault(channel_id, set()).add(ws)

    def disconnect(self, channel_id: int, ws: WebSocket):
        if channel_id in self.connections:
            self.connections[channel_id].discard(ws)

    async def broadcast(self, channel_id: int, payload: dict):
        dead = []
        for ws in list(self.connections.get(channel_id, set())):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(channel_id, ws)


ws_manager = ChatConnectionManager()


app = FastAPI(title="STUDYVERSE", version="1.0.0")
_bg_task = None


@app.on_event("startup")
async def on_startup():
    global _bg_task
    init_db()
    _bg_task = asyncio.create_task(background_scheduler_loop())


@app.on_event("shutdown")
async def on_shutdown():
    if _bg_task:
        _bg_task.cancel()
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(UPLOADS_DIR)), name="uploads")


# --- Helpers ---
def log_audit(actor_id: Optional[int], actor_role: str, action: str, target_type: str, target_id: str = "", details: str = ""):
    with get_db() as db:
        db.execute(
            """INSERT INTO audit_logs (actor_id, actor_role, action, target_type, target_id, details, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (actor_id, actor_role, action, target_type, str(target_id), details, ist_now().isoformat()),
        )


def create_notification(user_id: int, ntype: str, title: str, body: str, link: str = ""):
    with get_db() as db:
        db.execute(
            """INSERT INTO notifications (user_id, type, title, body, link, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, ntype, title, body, link, ist_now().isoformat()),
        )


def evaluate_badges(user_id: int):
    now_iso = ist_now().isoformat()
    with get_db() as db:
        # 1. Streak calculation
        rows = db.execute(
            """SELECT session_date, SUM(duration_minutes) as mins
               FROM study_sessions WHERE user_id = ?
               GROUP BY session_date ORDER BY session_date DESC""",
            (user_id,),
        ).fetchall()
        dates_with_study = {r["session_date"] for r in rows if r["mins"] > 0}
        streak = 0
        cursor = ist_now().date()
        while cursor.strftime("%Y-%m-%d") in dates_with_study:
            streak += 1
            cursor -= timedelta(days=1)
        if streak >= 7:
            db.execute("INSERT OR IGNORE INTO user_badges (user_id, badge_key, earned_at) VALUES (?, '7_day_streak', ?)", (user_id, now_iso))

        # 2. Consistency Champion (14 days meeting target)
        user_row = db.execute("SELECT daily_target_minutes FROM users WHERE id = ?", (user_id,)).fetchone()
        target = (user_row["daily_target_minutes"] if user_row else 360) or 360
        met_days = sum(1 for r in rows if r["mins"] >= target)
        if met_days >= 14:
            db.execute("INSERT OR IGNORE INTO user_badges (user_id, badge_key, earned_at) VALUES (?, 'consistency_champion', ?)", (user_id, now_iso))

        # 3. PYQ Master (10 chapters with PYQs completed)
        pyq_cnt = db.execute("SELECT COUNT(*) as c FROM chapter_progress WHERE user_id = ? AND pyqs_done = 1", (user_id,)).fetchone()["c"]
        if pyq_cnt >= 10:
            db.execute("INSERT OR IGNORE INTO user_badges (user_id, badge_key, earned_at) VALUES (?, 'pyq_master', ?)", (user_id, now_iso))

        # 4. Chapter Crusher (10 full chapters completed)
        full_cnt = db.execute("SELECT COUNT(*) as c FROM chapter_progress WHERE user_id = ? AND full_done = 1", (user_id,)).fetchone()["c"]
        if full_cnt >= 10:
            db.execute("INSERT OR IGNORE INTO user_badges (user_id, badge_key, earned_at) VALUES (?, 'chapter_crusher', ?)", (user_id, now_iso))

        # 5. Backlog Finisher (5 completed backlogs)
        bl_cnt = db.execute("SELECT COUNT(*) as c FROM backlogs WHERE user_id = ? AND is_completed = 1", (user_id,)).fetchone()["c"]
        if bl_cnt >= 5:
            db.execute("INSERT OR IGNORE INTO user_badges (user_id, badge_key, earned_at) VALUES (?, 'backlog_finisher', ?)", (user_id, now_iso))

        # 6. Study Buddy (at least 1 accepted buddy)
        sb_cnt = db.execute(
            "SELECT COUNT(*) as c FROM study_buddies WHERE (requester_id = ? OR receiver_id = ?) AND status = 'accepted'",
            (user_id, user_id),
        ).fetchone()["c"]
        if sb_cnt >= 1:
            db.execute("INSERT OR IGNORE INTO user_badges (user_id, badge_key, earned_at) VALUES (?, 'study_buddy', ?)", (user_id, now_iso))


def compute_study_streaks(user_id: int) -> tuple[int, int]:
    """Returns (current_streak, longest_streak)."""
    with get_db() as db:
        rows = db.execute(
            """SELECT session_date, SUM(duration_minutes) as mins
               FROM study_sessions WHERE user_id = ?
               GROUP BY session_date ORDER BY session_date ASC""",
            (user_id,),
        ).fetchall()
    active_dates = sorted([datetime.strptime(r["session_date"], "%Y-%m-%d").date() for r in rows if r["mins"] > 0])
    if not active_dates:
        return 0, 0
    longest = 1
    run = 1
    for i in range(1, len(active_dates)):
        if (active_dates[i] - active_dates[i - 1]).days == 1:
            run += 1
            longest = max(longest, run)
        else:
            run = 1
    active_set = set(active_dates)
    cur = 0
    d = ist_now().date()
    if d not in active_set:
        d = d - timedelta(days=1)
    while d in active_set:
        cur += 1
        d -= timedelta(days=1)
    return cur, longest


# --- Page Routes ---
@app.get("/", response_class=HTMLResponse)
async def serve_home():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/admin", response_class=HTMLResponse)
async def serve_admin():
    return FileResponse(str(STATIC_DIR / "admin.html"))


# --- Auth Endpoints ---
class RegisterReq(BaseModel):
    full_name: str = Field(min_length=2, max_length=80)
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=6, max_length=128)


class LoginReq(BaseModel):
    username: str
    password: str


class OnboardingReq(BaseModel):
    board: str
    stream: str


class ChangePasswordReq(BaseModel):
    current_password: str
    new_password: str = Field(min_length=6, max_length=128)


class RecoverReq(BaseModel):
    username: str
    recovery_code: str
    new_password: str = Field(min_length=6, max_length=128)


@app.post("/api/auth/register")
async def register(req: RegisterReq, request: Request, response: Response):
    uname = req.username.strip()
    if not re.match(r"^[A-Za-z0-9_.-]{3,32}$", uname):
        raise HTTPException(400, "Username must be 3-32 characters (letters, numbers, _, ., -).")

    pw_hash = hash_password(req.password)
    recovery_code = generate_recovery_code()
    rec_hash = hash_password(recovery_code)
    now_iso = ist_now().isoformat()

    with get_db() as db:
        exists = db.execute("SELECT id FROM users WHERE username = ?", (uname,)).fetchone()
        if exists:
            raise HTTPException(409, "Username already taken.")
        cur = db.execute(
            """INSERT INTO users (full_name, username, password_hash, role, class_level, recovery_hash, onboarding_complete, last_active_at, created_at)
               VALUES (?, ?, ?, 'student', 'Class 12', ?, 0, ?, ?)""",
            (req.full_name.strip(), uname, pw_hash, rec_hash, now_iso, now_iso),
        )
        uid = cur.lastrowid
        db.execute("INSERT OR IGNORE INTO revision_settings (user_id) VALUES (?)", (uid,))
        db.execute("INSERT OR IGNORE INTO notification_prefs (user_id) VALUES (?)", (uid,))
        user_row = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()

    token = create_session(uid, request.headers.get("user-agent", "Web Browser"))
    response.set_cookie("sv_session", token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 14)
    user_dict = dict(user_row)
    user_dict.pop("password_hash", None)
    user_dict.pop("recovery_hash", None)
    return {"user": user_dict, "token": token, "recovery_code": recovery_code}


@app.post("/api/auth/login")
async def login(req: LoginReq, request: Request, response: Response):
    with get_db() as db:
        row = db.execute("SELECT * FROM users WHERE username = ?", (req.username.strip(),)).fetchone()
    if not row or not verify_password(req.password, row["password_hash"]):
        raise HTTPException(401, "Invalid username or password.")

    user = dict(row)
    if user["ban_type"] == "perm":
        raise HTTPException(403, f"Account permanently banned: {user.get('ban_reason') or 'Policy violation'}")
    if user["ban_type"] == "temp" and user.get("ban_until"):
        if user["ban_until"] > ist_now().isoformat():
            raise HTTPException(403, f"Account temporarily banned until {user['ban_until']}: {user.get('ban_reason') or ''}")
        else:
            with get_db() as db:
                db.execute("UPDATE users SET ban_type='none', ban_reason='', ban_until=NULL WHERE id=?", (user["id"],))
            user["ban_type"] = "none"
    if user["is_suspended"]:
        raise HTTPException(403, "Account suspended. Please contact administrator.")

    with get_db() as db:
        db.execute("UPDATE users SET last_active_at = ? WHERE id = ?", (ist_now().isoformat(), user["id"]))

    token = create_session(user["id"], request.headers.get("user-agent", "Web Browser"))
    response.set_cookie("sv_session", token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 14)
    user.pop("password_hash", None)
    user.pop("recovery_hash", None)
    return {"user": user, "token": token}


@app.post("/api/auth/logout")
async def logout(request: Request, response: Response):
    token = request.cookies.get("sv_session")
    auth_h = request.headers.get("authorization")
    if auth_h and auth_h.lower().startswith("bearer "):
        token = auth_h.split(" ", 1)[1].strip()
    if token:
        revoke_session(token)
    response.delete_cookie("sv_session")
    return {"ok": True}


@app.get("/api/auth/me")
async def get_me(user: Optional[dict] = Depends(get_current_user_optional)):
    return {"user": user}


@app.post("/api/auth/onboarding")
async def complete_onboarding(req: OnboardingReq, user: dict = Depends(require_user)):
    if req.board not in ("MP Board", "CBSE"):
        raise HTTPException(400, "Board must be 'MP Board' or 'CBSE'.")
    if req.stream not in ("PCM", "PCB", "Commerce", "Arts / Humanities"):
        raise HTTPException(400, "Invalid stream.")
    with get_db() as db:
        db.execute(
            "UPDATE users SET board = ?, class_level = 'Class 12', stream = ?, onboarding_complete = 1 WHERE id = ?",
            (req.board, req.stream, user["id"]),
        )
        updated = db.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
    u = dict(updated)
    u.pop("password_hash", None)
    u.pop("recovery_hash", None)
    return {"user": u}


@app.post("/api/auth/change-password")
async def change_password(req: ChangePasswordReq, user: dict = Depends(require_user)):
    with get_db() as db:
        row = db.execute("SELECT password_hash FROM users WHERE id = ?", (user["id"],)).fetchone()
        if not row or not verify_password(req.current_password, row["password_hash"]):
            raise HTTPException(400, "Current password is incorrect.")
        db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(req.new_password), user["id"]))
    return {"ok": True}


@app.post("/api/auth/recover")
async def recover_account(req: RecoverReq):
    with get_db() as db:
        row = db.execute("SELECT id, recovery_hash FROM users WHERE username = ?", (req.username.strip(),)).fetchone()
        if not row or not row["recovery_hash"] or not verify_password(req.recovery_code.strip(), row["recovery_hash"]):
            raise HTTPException(400, "Invalid username or recovery code.")
        new_code = generate_recovery_code()
        db.execute(
            "UPDATE users SET password_hash = ?, recovery_hash = ? WHERE id = ?",
            (hash_password(req.new_password), hash_password(new_code), row["id"]),
        )
        db.execute("UPDATE sessions SET revoked = 1 WHERE user_id = ?", (row["id"],))
    return {"ok": True, "new_recovery_code": new_code}


# --- Profile Endpoints ---
class ProfileUpdateReq(BaseModel):
    full_name: Optional[str] = None
    username: Optional[str] = None
    bio: Optional[str] = None
    daily_target_minutes: Optional[int] = Field(default=None, ge=60, le=960)
    allow_buddy_requests: Optional[bool] = None


@app.put("/api/profile")
async def update_profile(req: ProfileUpdateReq, user: dict = Depends(require_user)):
    with get_db() as db:
        fields = []
        vals: list[Any] = []
        if req.full_name is not None and len(req.full_name.strip()) >= 2:
            fields.append("full_name = ?")
            vals.append(req.full_name.strip()[:80])
        if req.username is not None:
            new_u = req.username.strip()
            if new_u.lower() != user["username"].lower():
                if not re.match(r"^[A-Za-z0-9_.-]{3,32}$", new_u):
                    raise HTTPException(400, "Invalid username format.")
                dup = db.execute("SELECT id FROM users WHERE username = ? AND id != ?", (new_u, user["id"])).fetchone()
                if dup:
                    raise HTTPException(409, "Username already taken.")
                fields.append("username = ?")
                vals.append(new_u)
        if req.bio is not None:
            fields.append("bio = ?")
            vals.append(req.bio.strip()[:300])
        if req.daily_target_minutes is not None:
            fields.append("daily_target_minutes = ?")
            vals.append(req.daily_target_minutes)
        if req.allow_buddy_requests is not None:
            fields.append("allow_buddy_requests = ?")
            vals.append(1 if req.allow_buddy_requests else 0)
        # Never allow role escalation from profile endpoint!
        if fields:
            vals.append(user["id"])
            db.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ?", tuple(vals))
        updated = db.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
    u = dict(updated)
    u.pop("password_hash", None)
    u.pop("recovery_hash", None)
    return {"user": u}


@app.post("/api/profile/avatar")
async def upload_avatar(file: UploadFile = File(...), user: dict = Depends(require_user)):
    ext = Path(file.filename or "avatar.png").suffix.lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        raise HTTPException(400, "Only PNG, JPG, WEBP, or GIF images are supported.")
    data = await file.read()
    if len(data) > 3 * 1024 * 1024:
        raise HTTPException(400, "Image must be under 3 MB.")
    fname = f"avatar_{user['id']}_{secrets.token_hex(6)}{ext}"
    (UPLOADS_DIR / fname).write_bytes(data)
    url = f"/uploads/{fname}"
    with get_db() as db:
        db.execute("UPDATE users SET profile_pic = ? WHERE id = ?", (url, user["id"]))
    return {"profile_pic": url}


@app.delete("/api/profile/avatar")
async def remove_avatar(user: dict = Depends(require_user)):
    with get_db() as db:
        db.execute("UPDATE users SET profile_pic = '' WHERE id = ?", (user["id"],))
    return {"profile_pic": ""}


@app.get("/api/profile/{username}")
async def get_public_profile(username: str, user: dict = Depends(require_user)):
    with get_db() as db:
        target = db.execute(
            """SELECT id, full_name, username, role, board, class_level, stream, profile_pic, bio, daily_target_minutes, allow_buddy_requests, created_at
               FROM users WHERE username = ?""",
            (username,),
        ).fetchone()
        if not target:
            raise HTTPException(404, "Student not found.")
        t = dict(target)
        uid = t["id"]

        total_mins = db.execute("SELECT COALESCE(SUM(duration_minutes), 0) as m FROM study_sessions WHERE user_id = ?", (uid,)).fetchone()["m"]
        w_start, w_end = get_week_bounds()
        week_mins = db.execute(
            "SELECT COALESCE(SUM(duration_minutes), 0) as m FROM study_sessions WHERE user_id = ? AND session_date BETWEEN ? AND ?",
            (uid, w_start, w_end),
        ).fetchone()["m"]

        total_ch = db.execute(
            "SELECT COUNT(*) as c FROM chapters ch JOIN subjects s ON s.id = ch.subject_id WHERE s.board = ? AND s.stream = ?",
            (t["board"], t["stream"]),
        ).fetchone()["c"]
        done_ch = db.execute(
            "SELECT COUNT(*) as c FROM chapter_progress cp JOIN chapters ch ON ch.id = cp.chapter_id JOIN subjects s ON s.id = ch.subject_id WHERE cp.user_id = ? AND cp.full_done = 1 AND s.board = ? AND s.stream = ?",
            (uid, t["board"], t["stream"]),
        ).fetchone()["c"]
        badges = [dict(r) for r in db.execute("SELECT badge_key, earned_at FROM user_badges WHERE user_id = ?", (uid,)).fetchall()]
        archives = [dict(r) for r in db.execute("SELECT week_start, week_end, published_at FROM weekly_archives ORDER BY week_start DESC LIMIT 8").fetchall()]

    cur_streak, longest_streak = compute_study_streaks(uid)
    return {
        "profile": t,
        "stats": {
            "total_minutes": total_mins,
            "weekly_minutes": week_mins,
            "current_streak": cur_streak,
            "longest_streak": longest_streak,
            "completed_chapters": done_ch,
            "total_chapters": total_ch,
            "syllabus_pct": round((done_ch / total_ch) * 100.0, 1) if total_ch > 0 else 0.0,
        },
        "badges": badges,
        "weekly_history": archives,
    }


# --- Home Dashboard & Exam Countdown ---
@app.get("/api/dashboard/home")
async def get_home_dashboard(user: dict = Depends(require_user)):
    today = ist_today_str()
    w_start, w_end = get_week_bounds()
    board = user.get("board") or "CBSE"
    stream = user.get("stream") or "PCM"
    quote = get_quote_of_the_day()

    with get_db() as db:
        tt = db.execute("SELECT * FROM exam_timetables WHERE board = ?", (board,)).fetchone()
        subjects = db.execute(
            "SELECT * FROM subjects WHERE board = ? AND stream = ? ORDER BY display_order ASC",
            (board, stream),
        ).fetchall()

        today_mins = db.execute(
            "SELECT COALESCE(SUM(duration_minutes), 0) as m FROM study_sessions WHERE user_id = ? AND session_date = ?",
            (user["id"], today),
        ).fetchone()["m"]
        week_mins = db.execute(
            "SELECT COALESCE(SUM(duration_minutes), 0) as m FROM study_sessions WHERE user_id = ? AND session_date BETWEEN ? AND ?",
            (user["id"], w_start, w_end),
        ).fetchone()["m"]

        total_ch = db.execute(
            "SELECT COUNT(*) as c FROM chapters ch JOIN subjects s ON s.id = ch.subject_id WHERE s.board = ? AND s.stream = ?",
            (board, stream),
        ).fetchone()["c"]
        done_ch = db.execute(
            "SELECT COUNT(*) as c FROM chapter_progress cp JOIN chapters ch ON ch.id = cp.chapter_id JOIN subjects s ON s.id = ch.subject_id WHERE cp.user_id = ? AND cp.full_done = 1 AND s.board = ? AND s.stream = ?",
            (user["id"], board, stream),
        ).fetchone()["c"]

        pending_backlogs = db.execute(
            "SELECT COUNT(*) as c FROM backlogs WHERE user_id = ? AND is_completed = 0",
            (user["id"],),
        ).fetchone()["c"]
        due_revisions = db.execute(
            "SELECT COUNT(*) as c FROM revisions WHERE user_id = ? AND status = 'pending' AND due_date <= ?",
            (user["id"], today),
        ).fetchone()["c"]
        announcements = [dict(r) for r in db.execute("SELECT * FROM announcements WHERE is_active = 1 ORDER BY id DESC LIMIT 5").fetchall()]
        acc_posted = db.execute("SELECT 1 FROM accountability_posts WHERE user_id = ? AND post_date = ?", (user["id"], today)).fetchone() is not None

    cur_streak, longest_streak = compute_study_streaks(user["id"])
    return {
        "quote": quote,
        "timetable": dict(tt) if tt else None,
        "subject_exams": [dict(s) for s in subjects],
        "stats": {
            "today_minutes": today_mins,
            "weekly_minutes": week_mins,
            "daily_target_minutes": user.get("daily_target_minutes") or 360,
            "current_streak": cur_streak,
            "longest_streak": longest_streak,
            "completed_chapters": done_ch,
            "total_chapters": total_ch,
            "syllabus_pct": round((done_ch / total_ch) * 100.0, 1) if total_ch > 0 else 0.0,
            "pending_backlogs": pending_backlogs,
            "due_revisions": due_revisions,
            "accountability_posted_today": acc_posted,
        },
        "announcements": announcements,
    }


# --- Subjects & Chapter-wise 5-Checkbox Tracker ---
@app.get("/api/subjects")
async def list_subjects(user: dict = Depends(require_user)):
    board = user.get("board") or "CBSE"
    stream = user.get("stream") or "PCM"
    with get_db() as db:
        subjects = db.execute(
            "SELECT * FROM subjects WHERE board = ? AND stream = ? ORDER BY display_order ASC",
            (board, stream),
        ).fetchall()
        out = []
        total_chapters = 0
        total_completed = 0
        for s in subjects:
            chs = db.execute("SELECT id FROM chapters WHERE subject_id = ?", (s["id"],)).fetchall()
            ch_ids = [c["id"] for c in chs]
            done = 0
            weighted_sum = 0.0
            if ch_ids:
                placeholders = ",".join("?" for _ in ch_ids)
                progs = db.execute(
                    f"SELECT * FROM chapter_progress WHERE user_id = ? AND chapter_id IN ({placeholders})",
                    (user["id"], *ch_ids),
                ).fetchall()
                for p in progs:
                    if p["full_done"]:
                        done += 1
                    checks = p["notes_done"] + p["lectures_done"] + p["theory_done"] + p["pyqs_done"] + p["full_done"]
                    weighted_sum += (checks / 5.0) * 100.0
            cnt = len(ch_ids)
            total_chapters += cnt
            total_completed += done
            sd = dict(s)
            sd["total_chapters"] = cnt
            sd["completed_chapters"] = done
            sd["pending_chapters"] = cnt - done
            sd["progress_pct"] = round(weighted_sum / cnt, 1) if cnt > 0 else 0.0
            out.append(sd)

    return {
        "board": board,
        "stream": stream,
        "subjects": out,
        "overall": {
            "total_chapters": total_chapters,
            "completed_chapters": total_completed,
            "pending_chapters": total_chapters - total_completed,
            "progress_pct": round((total_completed / total_chapters) * 100.0, 1) if total_chapters > 0 else 0.0,
        },
    }


@app.get("/api/subjects/{subject_id}/chapters")
async def get_subject_chapters(
    subject_id: int,
    q: str = Query(default=""),
    status_filter: str = Query(default="all"),
    user: dict = Depends(require_user),
):
    with get_db() as db:
        subj = db.execute("SELECT * FROM subjects WHERE id = ?", (subject_id,)).fetchone()
        if not subj:
            raise HTTPException(404, "Subject not found.")
        rows = db.execute(
            """SELECT c.*,
                      COALESCE(cp.notes_done, 0) as notes_done,
                      COALESCE(cp.lectures_done, 0) as lectures_done,
                      COALESCE(cp.theory_done, 0) as theory_done,
                      COALESCE(cp.pyqs_done, 0) as pyqs_done,
                      COALESCE(cp.full_done, 0) as full_done
               FROM chapters c
               LEFT JOIN chapter_progress cp ON cp.chapter_id = c.id AND cp.user_id = ?
               WHERE c.subject_id = ?
               ORDER BY c.chapter_number ASC""",
            (user["id"], subject_id),
        ).fetchall()

    items = []
    for r in rows:
        d = dict(r)
        checks = d["notes_done"] + d["lectures_done"] + d["theory_done"] + d["pyqs_done"] + d["full_done"]
        d["chapter_progress_pct"] = round((checks / 5.0) * 100.0, 0)
        if q and q.lower() not in d["title"].lower() and q.lower() not in d["unit_name"].lower():
            continue
        if status_filter == "completed" and not d["full_done"]:
            continue
        if status_filter == "incomplete" and d["full_done"]:
            continue
        items.append(d)

    return {"subject": dict(subj), "chapters": items}


class ChapterProgressReq(BaseModel):
    notes_done: Optional[bool] = None
    lectures_done: Optional[bool] = None
    theory_done: Optional[bool] = None
    pyqs_done: Optional[bool] = None
    full_done: Optional[bool] = None


@app.put("/api/chapters/{chapter_id}/progress")
async def update_chapter_progress(chapter_id: int, req: ChapterProgressReq, user: dict = Depends(require_user)):
    now_iso = ist_now().isoformat()
    today = ist_now().date()
    revisions_created = 0

    with get_db() as db:
        ch = db.execute("SELECT * FROM chapters WHERE id = ?", (chapter_id,)).fetchone()
        if not ch:
            raise HTTPException(404, "Chapter not found.")

        cur = db.execute(
            "SELECT * FROM chapter_progress WHERE user_id = ? AND chapter_id = ?",
            (user["id"], chapter_id),
        ).fetchone()
        state = {
            "notes_done": cur["notes_done"] if cur else 0,
            "lectures_done": cur["lectures_done"] if cur else 0,
            "theory_done": cur["theory_done"] if cur else 0,
            "pyqs_done": cur["pyqs_done"] if cur else 0,
            "full_done": cur["full_done"] if cur else 0,
        }
        prev_full = state["full_done"]

        if req.notes_done is not None:
            state["notes_done"] = 1 if req.notes_done else 0
        if req.lectures_done is not None:
            state["lectures_done"] = 1 if req.lectures_done else 0
        if req.theory_done is not None:
            state["theory_done"] = 1 if req.theory_done else 0
        if req.pyqs_done is not None:
            state["pyqs_done"] = 1 if req.pyqs_done else 0
        # full_done is NEVER auto-ticked by the other 4 checkboxes; student must explicitly confirm it
        if req.full_done is not None:
            state["full_done"] = 1 if req.full_done else 0

        db.execute(
            """INSERT INTO chapter_progress (user_id, chapter_id, notes_done, lectures_done, theory_done, pyqs_done, full_done, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id, chapter_id) DO UPDATE SET
                 notes_done = excluded.notes_done,
                 lectures_done = excluded.lectures_done,
                 theory_done = excluded.theory_done,
                 pyqs_done = excluded.pyqs_done,
                 full_done = excluded.full_done,
                 updated_at = excluded.updated_at""",
            (user["id"], chapter_id, state["notes_done"], state["lectures_done"], state["theory_done"], state["pyqs_done"], state["full_done"], now_iso),
        )

        # Schedule Spaced Repetition Revisions when full_done transitions 0 -> 1
        if prev_full == 0 and state["full_done"] == 1:
            existing_rev = db.execute(
                "SELECT COUNT(*) as c FROM revisions WHERE user_id = ? AND chapter_id = ?",
                (user["id"], chapter_id),
            ).fetchone()["c"]
            if existing_rev == 0:
                rset = db.execute("SELECT intervals_json FROM revision_settings WHERE user_id = ?", (user["id"],)).fetchone()
                intervals = json.loads(rset["intervals_json"]) if rset else [3, 7, 15, 30]
                cum_days = 0
                for idx, gap in enumerate(intervals, start=1):
                    cum_days += int(gap)
                    due_d = (today + timedelta(days=cum_days)).strftime("%Y-%m-%d")
                    db.execute(
                        """INSERT INTO revisions (user_id, chapter_id, subject_id, revision_number, due_date, status)
                           VALUES (?, ?, ?, ?, ?, 'pending')""",
                        (user["id"], chapter_id, ch["subject_id"], idx, due_d),
                    )
                    revisions_created += 1

    evaluate_badges(user["id"])
    checks = sum(state.values())
    return {"chapter_id": chapter_id, **state, "chapter_progress_pct": round((checks / 5.0) * 100.0, 0), "revisions_scheduled": revisions_created}


# --- Personal Study Tracker, Live Timer, & Pomodoro ---
class StudySessionReq(BaseModel):
    subject_id: Optional[int] = None
    duration_minutes: int = Field(ge=1, le=960)
    session_date: Optional[str] = None
    source: str = Field(default="manual")
    note: str = Field(default="", max_length=500)


@app.get("/api/study/stats")
async def get_study_stats(user: dict = Depends(require_user)):
    now = ist_now()
    today_str = now.strftime("%Y-%m-%d")
    w_start, w_end = get_week_bounds(now)
    month_prefix = now.strftime("%Y-%m")

    with get_db() as db:
        today_m = db.execute("SELECT COALESCE(SUM(duration_minutes),0) as m FROM study_sessions WHERE user_id=? AND session_date=?", (user["id"], today_str)).fetchone()["m"]
        week_m = db.execute("SELECT COALESCE(SUM(duration_minutes),0) as m FROM study_sessions WHERE user_id=? AND session_date BETWEEN ? AND ?", (user["id"], w_start, w_end)).fetchone()["m"]
        month_m = db.execute("SELECT COALESCE(SUM(duration_minutes),0) as m FROM study_sessions WHERE user_id=? AND session_date LIKE ?", (user["id"], f"{month_prefix}%")).fetchone()["m"]
        life_m = db.execute("SELECT COALESCE(SUM(duration_minutes),0) as m FROM study_sessions WHERE user_id=?", (user["id"],)).fetchone()["m"]
        active_days = db.execute("SELECT COUNT(DISTINCT session_date) as c FROM study_sessions WHERE user_id=?", (user["id"],)).fetchone()["c"]

        # Subject-wise breakdown
        subj_rows = db.execute(
            """SELECT COALESCE(s.name, 'General') as subject_name, SUM(ss.duration_minutes) as minutes
               FROM study_sessions ss
               LEFT JOIN subjects s ON s.id = ss.subject_id
               WHERE ss.user_id = ?
               GROUP BY subject_name ORDER BY minutes DESC""",
            (user["id"],),
        ).fetchall()

        # Last 14 days daily trend
        start_14 = (now.date() - timedelta(days=13)).strftime("%Y-%m-%d")
        daily_rows = db.execute(
            """SELECT session_date, SUM(duration_minutes) as minutes
               FROM study_sessions WHERE user_id = ? AND session_date >= ?
               GROUP BY session_date""",
            (user["id"], start_14),
        ).fetchall()
        dmap = {r["session_date"]: r["minutes"] for r in daily_rows}
        daily_trend = []
        for i in range(14):
            d = (now.date() - timedelta(days=13 - i)).strftime("%Y-%m-%d")
            daily_trend.append({"date": d, "minutes": dmap.get(d, 0)})

        recent_sessions = db.execute(
            """SELECT ss.*, COALESCE(s.name, 'General') as subject_name
               FROM study_sessions ss
               LEFT JOIN subjects s ON s.id = ss.subject_id
               WHERE ss.user_id = ?
               ORDER BY ss.session_date DESC, ss.id DESC LIMIT 30""",
            (user["id"],),
        ).fetchall()

    cur_streak, longest_streak = compute_study_streaks(user["id"])
    target = user.get("daily_target_minutes") or 360
    daily_avg = round(life_m / active_days) if active_days > 0 else 0

    return {
        "today_minutes": today_m,
        "weekly_minutes": week_m,
        "monthly_minutes": month_m,
        "lifetime_minutes": life_m,
        "daily_average_minutes": daily_avg,
        "current_streak": cur_streak,
        "longest_streak": longest_streak,
        "daily_target_minutes": target,
        "target_achievement_pct": round(min(100.0, (today_m / target) * 100.0), 1),
        "subject_breakdown": [dict(r) for r in subj_rows],
        "daily_trend": daily_trend,
        "recent_sessions": [dict(r) for r in recent_sessions],
    }


@app.post("/api/study/sessions")
async def add_study_session(req: StudySessionReq, user: dict = Depends(require_user)):
    s_date = req.session_date or ist_today_str()
    if s_date > ist_today_str():
        raise HTTPException(400, "Cannot log study sessions for future dates.")
    if req.source not in ("manual", "timer", "pomodoro"):
        raise HTTPException(400, "Invalid session source.")

    now_iso = ist_now().isoformat()
    with get_db() as db:
        # Anti-manipulation: prevent > 16 hours (960 mins) in a single day
        day_total = db.execute(
            "SELECT COALESCE(SUM(duration_minutes), 0) as m FROM study_sessions WHERE user_id = ? AND session_date = ?",
            (user["id"], s_date),
        ).fetchone()["m"]
        if day_total + req.duration_minutes > 960:
            raise HTTPException(400, "Daily logged study time cannot exceed 16 hours (960 minutes).")

        # Anti-duplicate: reject identical session logged within last 60 seconds
        cutoff = (ist_now() - timedelta(seconds=60)).isoformat()
        dup = db.execute(
            """SELECT id FROM study_sessions
               WHERE user_id = ? AND session_date = ? AND duration_minutes = ? AND COALESCE(subject_id, 0) = ? AND created_at > ?""",
            (user["id"], s_date, req.duration_minutes, req.subject_id or 0, cutoff),
        ).fetchone()
        if dup:
            raise HTTPException(409, "Duplicate study session detected.")

        cur = db.execute(
            """INSERT INTO study_sessions (user_id, subject_id, duration_minutes, session_date, source, note, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (user["id"], req.subject_id, req.duration_minutes, s_date, req.source, req.note.strip(), now_iso, now_iso),
        )
        sid = cur.lastrowid

    evaluate_badges(user["id"])
    return {"id": sid, "ok": True}


@app.put("/api/study/sessions/{session_id}")
async def update_study_session(session_id: int, req: StudySessionReq, user: dict = Depends(require_user)):
    with get_db() as db:
        row = db.execute("SELECT * FROM study_sessions WHERE id = ? AND user_id = ?", (session_id, user["id"])).fetchone()
        if not row:
            raise HTTPException(404, "Study session not found.")
        s_date = req.session_date or row["session_date"]
        other_total = db.execute(
            "SELECT COALESCE(SUM(duration_minutes), 0) as m FROM study_sessions WHERE user_id = ? AND session_date = ? AND id != ?",
            (user["id"], s_date, session_id),
        ).fetchone()["m"]
        if other_total + req.duration_minutes > 960:
            raise HTTPException(400, "Daily study time cannot exceed 16 hours.")
        db.execute(
            "UPDATE study_sessions SET subject_id = ?, duration_minutes = ?, session_date = ?, note = ?, updated_at = ? WHERE id = ?",
            (req.subject_id, req.duration_minutes, s_date, req.note.strip(), ist_now().isoformat(), session_id),
        )
    return {"ok": True}


@app.delete("/api/study/sessions/{session_id}")
async def delete_study_session(session_id: int, user: dict = Depends(require_user)):
    with get_db() as db:
        res = db.execute("DELETE FROM study_sessions WHERE id = ? AND user_id = ?", (session_id, user["id"]))
        if res.rowcount == 0:
            raise HTTPException(404, "Study session not found.")
    return {"ok": True}


@app.get("/api/study/timer")
async def get_timer(user: dict = Depends(require_user)):
    with get_db() as db:
        row = db.execute("SELECT * FROM active_timers WHERE user_id = ?", (user["id"],)).fetchone()
    if not row:
        return {"timer": None}
    t = dict(row)
    elapsed = t["accumulated_seconds"]
    if not t["is_paused"] and t["started_at"]:
        elapsed += max(0, int((ist_now() - datetime.fromisoformat(t["started_at"])).total_seconds()))
    t["elapsed_seconds"] = elapsed
    return {"timer": t}


@app.post("/api/study/timer/start")
async def start_timer(subject_id: Optional[int] = Body(default=None, embed=True), user: dict = Depends(require_user)):
    now_iso = ist_now().isoformat()
    with get_db() as db:
        db.execute(
            """INSERT INTO active_timers (user_id, subject_id, started_at, accumulated_seconds, is_paused, updated_at)
               VALUES (?, ?, ?, 0, 0, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                 subject_id = excluded.subject_id,
                 started_at = excluded.started_at,
                 accumulated_seconds = 0,
                 is_paused = 0,
                 updated_at = excluded.updated_at""",
            (user["id"], subject_id, now_iso, now_iso),
        )
    return {"ok": True, "started_at": now_iso}


@app.post("/api/study/timer/pause")
async def pause_timer(user: dict = Depends(require_user)):
    now = ist_now()
    with get_db() as db:
        row = db.execute("SELECT * FROM active_timers WHERE user_id = ?", (user["id"],)).fetchone()
        if not row or row["is_paused"]:
            return {"ok": True}
        extra = max(0, int((now - datetime.fromisoformat(row["started_at"])).total_seconds()))
        total = row["accumulated_seconds"] + extra
        db.execute(
            "UPDATE active_timers SET accumulated_seconds = ?, is_paused = 1, started_at = NULL, updated_at = ? WHERE user_id = ?",
            (total, now.isoformat(), user["id"]),
        )
    return {"ok": True, "accumulated_seconds": total}


@app.post("/api/study/timer/resume")
async def resume_timer(user: dict = Depends(require_user)):
    now_iso = ist_now().isoformat()
    with get_db() as db:
        db.execute(
            "UPDATE active_timers SET is_paused = 0, started_at = ?, updated_at = ? WHERE user_id = ? AND is_paused = 1",
            (now_iso, now_iso, user["id"]),
        )
    return {"ok": True}


@app.post("/api/study/timer/stop")
async def stop_timer(note: str = Body(default="", embed=True), user: dict = Depends(require_user)):
    now = ist_now()
    with get_db() as db:
        row = db.execute("SELECT * FROM active_timers WHERE user_id = ?", (user["id"],)).fetchone()
        if not row:
            raise HTTPException(400, "No active timer found.")
        total_sec = row["accumulated_seconds"]
        if not row["is_paused"] and row["started_at"]:
            total_sec += max(0, int((now - datetime.fromisoformat(row["started_at"])).total_seconds()))
        mins = max(1, round(total_sec / 60)) if total_sec >= 30 else 0
        db.execute("DELETE FROM active_timers WHERE user_id = ?", (user["id"],))
        if mins > 0:
            db.execute(
                """INSERT INTO study_sessions (user_id, subject_id, duration_minutes, session_date, source, note, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'timer', ?, ?, ?)""",
                (user["id"], row["subject_id"], mins, now.strftime("%Y-%m-%d"), note.strip(), now.isoformat(), now.isoformat()),
            )
    if mins > 0:
        evaluate_badges(user["id"])
    return {"ok": True, "logged_minutes": mins}


# --- 6-Hour Challenge Accountability ---
class AccountabilityPostReq(BaseModel):
    reason_category: str
    explanation: str = Field(default="", max_length=600)
    no_explanation: bool = False
    request_suggestions: bool = True


class AccountabilityReplyReq(BaseModel):
    message: str = Field(min_length=1, max_length=600)
    reply_type: str = Field(default="suggestion")


@app.get("/api/accountability/status")
async def accountability_status(user: dict = Depends(require_user)):
    today = ist_today_str()
    target = user.get("daily_target_minutes") or 360
    with get_db() as db:
        studied = db.execute(
            "SELECT COALESCE(SUM(duration_minutes), 0) as m FROM study_sessions WHERE user_id = ? AND session_date = ?",
            (user["id"], today),
        ).fetchone()["m"]
        existing = db.execute("SELECT * FROM accountability_posts WHERE user_id = ? AND post_date = ?", (user["id"], today)).fetchone()
    below_target = studied < target
    return {
        "date": today,
        "studied_minutes": studied,
        "target_minutes": target,
        "below_target": below_target,
        "prompt_message": "You haven't completed your 6-hour goal today. What stopped you?" if below_target else "You completed your daily study goal!",
        "already_posted": existing is not None,
        "post": dict(existing) if existing else None,
    }


@app.post("/api/accountability/posts")
async def create_accountability_post(req: AccountabilityPostReq, user: dict = Depends(require_user)):
    today = ist_today_str()
    target = user.get("daily_target_minutes") or 360
    with get_db() as db:
        studied = db.execute(
            "SELECT COALESCE(SUM(duration_minutes), 0) as m FROM study_sessions WHERE user_id = ? AND session_date = ?",
            (user["id"], today),
        ).fetchone()["m"]
        reason = "No explanation today" if req.no_explanation else req.reason_category
        expl = "" if req.no_explanation else req.explanation.strip()
        db.execute(
            """INSERT INTO accountability_posts (user_id, post_date, studied_minutes, target_minutes, reason_category, explanation, no_explanation, request_suggestions, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id, post_date) DO UPDATE SET
                 studied_minutes = excluded.studied_minutes,
                 reason_category = excluded.reason_category,
                 explanation = excluded.explanation,
                 no_explanation = excluded.no_explanation,
                 request_suggestions = excluded.request_suggestions""",
            (user["id"], today, studied, target, reason, expl, 1 if req.no_explanation else 0, 1 if req.request_suggestions else 0, ist_now().isoformat()),
        )
    return {"ok": True}


@app.get("/api/accountability/posts")
async def list_accountability_posts(user: dict = Depends(require_user)):
    with get_db() as db:
        posts = db.execute(
            """SELECT ap.*, u.full_name, u.username, u.profile_pic, u.board, u.stream
               FROM accountability_posts ap
               JOIN users u ON u.id = ap.user_id
               ORDER BY ap.id DESC LIMIT 40"""
        ).fetchall()
        out = []
        for p in posts:
            pd = dict(p)
            replies = db.execute(
                """SELECT ar.*, u.full_name, u.username, u.profile_pic
                   FROM accountability_replies ar
                   JOIN users u ON u.id = ar.user_id
                   WHERE ar.post_id = ? ORDER BY ar.id ASC""",
                (p["id"],),
            ).fetchall()
            pd["replies"] = [dict(r) for r in replies]
            out.append(pd)
    return {"posts": out}


@app.post("/api/accountability/posts/{post_id}/replies")
async def reply_accountability_post(post_id: int, req: AccountabilityReplyReq, user: dict = Depends(require_user)):
    if req.reply_type not in ("reply", "suggestion", "encourage", "invite"):
        raise HTTPException(400, "Invalid reply type.")
    with get_db() as db:
        post = db.execute("SELECT user_id FROM accountability_posts WHERE id = ?", (post_id,)).fetchone()
        if not post:
            raise HTTPException(404, "Post not found.")
        db.execute(
            "INSERT INTO accountability_replies (post_id, user_id, message, reply_type, created_at) VALUES (?, ?, ?, ?, ?)",
            (post_id, user["id"], req.message.strip(), req.reply_type, ist_now().isoformat()),
        )
    if post["user_id"] != user["id"]:
        create_notification(post["user_id"], "community", "New Accountability Reply", f"{user['full_name']} replied to your 6-Hour Challenge post.", "#community")
    return {"ok": True}


# --- Community Chat (WebSockets + REST + Moderation) ---
@app.get("/api/chat/channels")
async def list_chat_channels(user: dict = Depends(require_user)):
    with get_db() as db:
        channels = db.execute("SELECT * FROM chat_channels ORDER BY category ASC, display_order ASC").fetchall()
        out = []
        for ch in channels:
            cd = dict(ch)
            read_row = db.execute("SELECT last_read_id FROM chat_reads WHERE user_id = ? AND channel_id = ?", (user["id"], ch["id"])).fetchone()
            last_read = read_row["last_read_id"] if read_row else 0
            unread = db.execute(
                "SELECT COUNT(*) as c FROM chat_messages WHERE channel_id = ? AND id > ? AND is_deleted = 0",
                (ch["id"], last_read),
            ).fetchone()["c"]
            cd["unread_count"] = unread
            out.append(cd)
    return {"channels": out}


@app.get("/api/chat/channels/{channel_id}/messages")
async def get_channel_messages(
    channel_id: int,
    before_id: Optional[int] = None,
    q: str = Query(default=""),
    limit: int = Query(default=40, ge=1, le=100),
    user: dict = Depends(require_user),
):
    with get_db() as db:
        ch = db.execute("SELECT * FROM chat_channels WHERE id = ?", (channel_id,)).fetchone()
        if not ch:
            raise HTTPException(404, "Channel not found.")
        blocked_ids = {r["blocked_id"] for r in db.execute("SELECT blocked_id FROM user_blocks WHERE blocker_id = ?", (user["id"],)).fetchall()}

        sql = """SELECT m.*, u.full_name, u.username, u.profile_pic, u.role as user_role
                 FROM chat_messages m
                 JOIN users u ON u.id = m.user_id
                 WHERE m.channel_id = ? AND m.is_deleted = 0"""
        params: list[Any] = [channel_id]
        if before_id:
            sql += " AND m.id < ?"
            params.append(before_id)
        if q.strip():
            sql += " AND m.content LIKE ?"
            params.append(f"%{q.strip()}%")
        sql += " ORDER BY m.id DESC LIMIT ?"
        params.append(limit)
        rows = db.execute(sql, tuple(params)).fetchall()

        messages = []
        max_id = 0
        for r in reversed(rows):
            if r["user_id"] in blocked_ids:
                continue
            max_id = max(max_id, r["id"])
            md = dict(r)
            reacts = db.execute(
                "SELECT emoji, COUNT(*) as cnt FROM chat_reactions WHERE message_id = ? GROUP BY emoji",
                (r["id"],),
            ).fetchall()
            md["reactions"] = [dict(x) for x in reacts]
            if r["reply_to_id"]:
                rep = db.execute(
                    "SELECT m.id, m.content, u.username FROM chat_messages m JOIN users u ON u.id = m.user_id WHERE m.id = ?",
                    (r["reply_to_id"],),
                ).fetchone()
                md["reply_preview"] = dict(rep) if rep else None
            else:
                md["reply_preview"] = None
            messages.append(md)

        if max_id > 0:
            db.execute(
                """INSERT INTO chat_reads (user_id, channel_id, last_read_id) VALUES (?, ?, ?)
                   ON CONFLICT(user_id, channel_id) DO UPDATE SET last_read_id = MAX(last_read_id, excluded.last_read_id)""",
                (user["id"], channel_id, max_id),
            )

        pinned = db.execute(
            """SELECT m.id, m.content, u.username, m.created_at
               FROM chat_messages m JOIN users u ON u.id = m.user_id
               WHERE m.channel_id = ? AND m.is_pinned = 1 AND m.is_deleted = 0
               ORDER BY m.id DESC LIMIT 5""",
            (channel_id,),
        ).fetchall()

    return {"channel": dict(ch), "messages": messages, "pinned": [dict(p) for p in pinned]}


class SendMessageReq(BaseModel):
    content: str
    reply_to_id: Optional[int] = None


@app.post("/api/chat/channels/{channel_id}/messages")
async def send_chat_message(channel_id: int, req: SendMessageReq, user: dict = Depends(require_user)):
    if user.get("mute_until") and user["mute_until"] > ist_now().isoformat():
        raise HTTPException(403, f"You are temporarily muted until {user['mute_until']}.")

    with get_db() as db:
        ch = db.execute("SELECT * FROM chat_channels WHERE id = ?", (channel_id,)).fetchone()
        if not ch:
            raise HTTPException(404, "Channel not found.")
        if ch["is_locked"] and user["role"] not in ("moderator", "admin"):
            raise HTTPException(403, "This discussion channel is currently locked by moderators.")

    ok, err, flagged = check_message_safety(user["id"], req.content)
    if not ok:
        raise HTTPException(400, err)

    now_iso = ist_now().isoformat()
    with get_db() as db:
        cur = db.execute(
            """INSERT INTO chat_messages (channel_id, user_id, content, reply_to_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (channel_id, user["id"], req.content.strip(), req.reply_to_id, now_iso, now_iso),
        )
        msg_id = cur.lastrowid
        # Auto-flag for human review (never auto-ban!)
        if flagged:
            db.execute(
                "INSERT INTO message_reports (message_id, reporter_id, reason, status, created_at) VALUES (?, ?, 'Automated offensive-language flag (requires human review)', 'pending', ?)",
                (msg_id, user["id"], now_iso),
            )
        row = db.execute(
            """SELECT m.*, u.full_name, u.username, u.profile_pic, u.role as user_role
               FROM chat_messages m JOIN users u ON u.id = m.user_id WHERE m.id = ?""",
            (msg_id,),
        ).fetchone()
        msg_dict = dict(row)
        msg_dict["reactions"] = []
        msg_dict["reply_preview"] = None

    await ws_manager.broadcast(channel_id, {"type": "new_message", "message": msg_dict})
    return {"message": msg_dict, "flagged": flagged}


@app.put("/api/chat/messages/{msg_id}")
async def edit_chat_message(msg_id: int, content: str = Body(..., embed=True), user: dict = Depends(require_user)):
    if not content.strip():
        raise HTTPException(400, "Content cannot be empty.")
    with get_db() as db:
        row = db.execute("SELECT * FROM chat_messages WHERE id = ?", (msg_id,)).fetchone()
        if not row or row["is_deleted"]:
            raise HTTPException(404, "Message not found.")
        if row["user_id"] != user["id"]:
            raise HTTPException(403, "You can only edit your own messages.")
        db.execute(
            "UPDATE chat_messages SET content = ?, is_edited = 1, updated_at = ? WHERE id = ?",
            (content.strip()[:2000], ist_now().isoformat(), msg_id),
        )
        ch_id = row["channel_id"]
    await ws_manager.broadcast(ch_id, {"type": "edit_message", "message_id": msg_id, "content": content.strip()[:2000]})
    return {"ok": True}


@app.delete("/api/chat/messages/{msg_id}")
async def delete_chat_message(msg_id: int, user: dict = Depends(require_user)):
    with get_db() as db:
        row = db.execute("SELECT * FROM chat_messages WHERE id = ?", (msg_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Message not found.")
        if row["user_id"] != user["id"] and user["role"] not in ("moderator", "admin"):
            raise HTTPException(403, "Permission denied.")
        db.execute("UPDATE chat_messages SET is_deleted = 1 WHERE id = ?", (msg_id,))
        ch_id = row["channel_id"]
    if row["user_id"] != user["id"]:
        log_audit(user["id"], user["role"], "delete_message", "chat_message", str(msg_id), "Removed inappropriate/spam message")
    await ws_manager.broadcast(ch_id, {"type": "delete_message", "message_id": msg_id})
    return {"ok": True}


@app.post("/api/chat/messages/{msg_id}/react")
async def react_chat_message(msg_id: int, emoji: str = Body(..., embed=True), user: dict = Depends(require_user)):
    with get_db() as db:
        msg = db.execute("SELECT channel_id FROM chat_messages WHERE id = ?", (msg_id,)).fetchone()
        if not msg:
            raise HTTPException(404, "Message not found.")
        exists = db.execute(
            "SELECT 1 FROM chat_reactions WHERE message_id = ? AND user_id = ? AND emoji = ?",
            (msg_id, user["id"], emoji),
        ).fetchone()
        if exists:
            db.execute("DELETE FROM chat_reactions WHERE message_id = ? AND user_id = ? AND emoji = ?", (msg_id, user["id"], emoji))
        else:
            db.execute("INSERT INTO chat_reactions (message_id, user_id, emoji) VALUES (?, ?, ?)", (msg_id, user["id"], emoji[:8]))
        reacts = [dict(x) for x in db.execute("SELECT emoji, COUNT(*) as cnt FROM chat_reactions WHERE message_id = ? GROUP BY emoji", (msg_id,)).fetchall()]
        ch_id = msg["channel_id"]
    await ws_manager.broadcast(ch_id, {"type": "reactions", "message_id": msg_id, "reactions": reacts})
    return {"reactions": reacts}


@app.post("/api/chat/messages/{msg_id}/pin")
async def pin_chat_message(msg_id: int, user: dict = Depends(require_mod_or_admin)):
    with get_db() as db:
        msg = db.execute("SELECT channel_id, is_pinned FROM chat_messages WHERE id = ?", (msg_id,)).fetchone()
        if not msg:
            raise HTTPException(404, "Message not found.")
        new_pin = 0 if msg["is_pinned"] else 1
        db.execute("UPDATE chat_messages SET is_pinned = ? WHERE id = ?", (new_pin, msg_id))
    return {"is_pinned": bool(new_pin)}


@app.post("/api/chat/messages/{msg_id}/report")
async def report_chat_message(msg_id: int, reason: str = Body(..., embed=True), user: dict = Depends(require_user)):
    with get_db() as db:
        msg = db.execute("SELECT id FROM chat_messages WHERE id = ?", (msg_id,)).fetchone()
        if not msg:
            raise HTTPException(404, "Message not found.")
        db.execute(
            "INSERT INTO message_reports (message_id, reporter_id, reason, status, created_at) VALUES (?, ?, ?, 'pending', ?)",
            (msg_id, user["id"], reason.strip()[:300], ist_now().isoformat()),
        )
    return {"ok": True}


@app.post("/api/users/{target_id}/block")
async def block_user(target_id: int, user: dict = Depends(require_user)):
    if target_id == user["id"]:
        raise HTTPException(400, "Cannot block yourself.")
    with get_db() as db:
        db.execute("INSERT OR IGNORE INTO user_blocks (blocker_id, blocked_id, created_at) VALUES (?, ?, ?)", (user["id"], target_id, ist_now().isoformat()))
    return {"ok": True}


@app.delete("/api/users/{target_id}/block")
async def unblock_user(target_id: int, user: dict = Depends(require_user)):
    with get_db() as db:
        db.execute("DELETE FROM user_blocks WHERE blocker_id = ? AND blocked_id = ?", (user["id"], target_id))
    return {"ok": True}


@app.websocket("/ws/chat/{channel_id}")
async def websocket_chat(websocket: WebSocket, channel_id: int):
    token = websocket.cookies.get("sv_session") or websocket.query_params.get("token")
    user = get_user_from_token(token)
    if not user:
        await websocket.close(code=4401)
        return
    await ws_manager.connect(channel_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(channel_id, websocket)


# --- Community Support Feed & Study Buddies ---
class SupportPostReq(BaseModel):
    topic: str
    title: str = Field(min_length=3, max_length=150)
    content: str = Field(min_length=5, max_length=1500)


@app.get("/api/support/posts")
async def list_support_posts(user: dict = Depends(require_user)):
    with get_db() as db:
        posts = db.execute(
            """SELECT sp.*, u.full_name, u.username, u.profile_pic, u.board, u.stream
               FROM support_posts sp JOIN users u ON u.id = sp.user_id
               ORDER BY sp.id DESC LIMIT 40"""
        ).fetchall()
        out = []
        for p in posts:
            pd = dict(p)
            comments = db.execute(
                """SELECT sc.*, u.full_name, u.username, u.profile_pic
                   FROM support_comments sc JOIN users u ON u.id = sc.user_id
                   WHERE sc.post_id = ? ORDER BY sc.id ASC""",
                (p["id"],),
            ).fetchall()
            pd["comments"] = [dict(c) for c in comments]
            out.append(pd)
    return {"posts": out}


@app.post("/api/support/posts")
async def create_support_post(req: SupportPostReq, user: dict = Depends(require_user)):
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO support_posts (user_id, topic, title, content, created_at) VALUES (?, ?, ?, ?, ?)",
            (user["id"], req.topic, req.title.strip(), req.content.strip(), ist_now().isoformat()),
        )
    return {"id": cur.lastrowid, "ok": True}


@app.post("/api/support/posts/{post_id}/comments")
async def comment_support_post(post_id: int, content: str = Body(..., embed=True), user: dict = Depends(require_user)):
    with get_db() as db:
        post = db.execute("SELECT user_id FROM support_posts WHERE id = ?", (post_id,)).fetchone()
        if not post:
            raise HTTPException(404, "Support post not found.")
        db.execute(
            "INSERT INTO support_comments (post_id, user_id, content, created_at) VALUES (?, ?, ?, ?)",
            (post_id, user["id"], content.strip()[:800], ist_now().isoformat()),
        )
    if post["user_id"] != user["id"]:
        create_notification(post["user_id"], "community", "New Support Reply", f"{user['full_name']} commented on your support post.", "#community")
    return {"ok": True}


@app.get("/api/buddies")
async def get_buddies(user: dict = Depends(require_user)):
    today = ist_today_str()
    with get_db() as db:
        rows = db.execute(
            """SELECT sb.*,
                      req.full_name as req_name, req.username as req_username, req.profile_pic as req_pic, req.board as req_board, req.stream as req_stream,
                      rec.full_name as rec_name, rec.username as rec_username, rec.profile_pic as rec_pic, rec.board as rec_board, rec.stream as rec_stream
               FROM study_buddies sb
               JOIN users req ON req.id = sb.requester_id
               JOIN users rec ON rec.id = sb.receiver_id
               WHERE sb.requester_id = ? OR sb.receiver_id = ?
               ORDER BY sb.id DESC""",
            (user["id"], user["id"]),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            partner_id = r["receiver_id"] if r["requester_id"] == user["id"] else r["requester_id"]
            p_mins = db.execute("SELECT COALESCE(SUM(duration_minutes), 0) as m FROM study_sessions WHERE user_id = ? AND session_date = ?", (partner_id, today)).fetchone()["m"]
            d["partner_today_minutes"] = p_mins
            out.append(d)
    return {"buddies": out}


@app.get("/api/buddies/search")
async def search_buddies(q: str = Query(default=""), user: dict = Depends(require_user)):
    with get_db() as db:
        rows = db.execute(
            """SELECT id, full_name, username, profile_pic, board, stream, daily_target_minutes
               FROM users
               WHERE id != ? AND role IN ('student','moderator') AND onboarding_complete = 1 AND allow_buddy_requests = 1
                 AND (username LIKE ? OR full_name LIKE ?)
               LIMIT 20""",
            (user["id"], f"%{q}%", f"%{q}%"),
        ).fetchall()
    return {"students": [dict(r) for r in rows]}


@app.post("/api/buddies/request")
async def request_buddy(receiver_id: int = Body(..., embed=True), shared_target_minutes: int = Body(default=360, embed=True), user: dict = Depends(require_user)):
    if receiver_id == user["id"]:
        raise HTTPException(400, "Cannot send buddy request to yourself.")
    with get_db() as db:
        rec = db.execute("SELECT id, allow_buddy_requests FROM users WHERE id = ?", (receiver_id,)).fetchone()
        if not rec:
            raise HTTPException(404, "Student not found.")
        if not rec["allow_buddy_requests"]:
            raise HTTPException(403, "This student is not accepting study buddy requests.")
        db.execute(
            """INSERT INTO study_buddies (requester_id, receiver_id, status, shared_target_minutes, created_at)
               VALUES (?, ?, 'pending', ?, ?)
               ON CONFLICT(requester_id, receiver_id) DO UPDATE SET status = 'pending', shared_target_minutes = excluded.shared_target_minutes""",
            (user["id"], receiver_id, shared_target_minutes, ist_now().isoformat()),
        )
    create_notification(receiver_id, "buddy", "New Study Buddy Request", f"{user['full_name']} wants to be your study buddy!", "#community")
    return {"ok": True}


@app.post("/api/buddies/{buddy_id}/respond")
async def respond_buddy(buddy_id: int, action: str = Body(..., embed=True), user: dict = Depends(require_user)):
    if action not in ("accepted", "rejected"):
        raise HTTPException(400, "Action must be 'accepted' or 'rejected'.")
    with get_db() as db:
        row = db.execute("SELECT * FROM study_buddies WHERE id = ? AND receiver_id = ?", (buddy_id, user["id"])).fetchone()
        if not row:
            raise HTTPException(404, "Buddy request not found.")
        db.execute("UPDATE study_buddies SET status = ? WHERE id = ?", (action, buddy_id))
    if action == "accepted":
        evaluate_badges(user["id"])
        evaluate_badges(row["requester_id"])
        create_notification(row["requester_id"], "buddy", "Study Buddy Accepted", f"{user['full_name']} accepted your study buddy request!", "#community")
    return {"ok": True}


# --- Leaderboard & Daily 11 PM Report ---
@app.get("/api/leaderboard")
async def get_leaderboard(user: dict = Depends(require_user)):
    now = ist_now()
    is_sunday = now.weekday() == 6
    w_start, w_end = get_week_bounds(now)
    with get_db() as db:
        setting = db.execute("SELECT value FROM platform_settings WHERE key = 'leaderboard_public_sunday_only'").fetchone()
        sunday_only = (setting["value"] == "1") if setting else True

    entries = compute_leaderboard_entries(w_start, w_end)
    my_entry = next((e for e in entries if e["user_id"] == user["id"]), None)
    public_unlocked = (not sunday_only) or is_sunday or (user["role"] == "admin")

    return {
        "week_start": w_start,
        "week_end": w_end,
        "is_sunday": is_sunday,
        "public_unlocked": public_unlocked,
        "my_entry": my_entry,
        "leaderboard": entries if public_unlocked else [],
        "total_participants": len(entries),
    }


@app.get("/api/leaderboard/history")
async def get_leaderboard_history(user: dict = Depends(require_user)):
    with get_db() as db:
        rows = db.execute("SELECT * FROM weekly_archives ORDER BY week_start DESC LIMIT 12").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["data"] = json.loads(d.pop("data_json"))
        out.append(d)
    return {"archives": out}


@app.get("/api/reports/daily")
async def get_daily_report(date: Optional[str] = None, user: dict = Depends(require_user)):
    r_date = date or ist_today_str()
    now = ist_now()
    is_sunday = now.weekday() == 6
    with get_db() as db:
        row = db.execute("SELECT * FROM daily_reports WHERE report_date = ?", (r_date,)).fetchone()
        setting = db.execute("SELECT value FROM platform_settings WHERE key = 'leaderboard_public_sunday_only'").fetchone()
        sunday_only = (setting["value"] == "1") if setting else True

    if row:
        payload = json.loads(row["data_json"])
    else:
        payload = generate_daily_report(r_date, notify=False)

    can_view_all = (not sunday_only) or is_sunday or (user["role"] == "admin")
    if not can_view_all:
        payload["students"] = [s for s in payload["students"] if s["user_id"] == user["id"]]
        payload["restricted_visibility"] = True
    else:
        payload["restricted_visibility"] = False
    return payload


# --- My Backlogs ---
class BacklogReq(BaseModel):
    subject_name: str
    chapter_title: str = Field(min_length=2, max_length=200)
    priority: str = Field(default="Medium")
    target_date: str


@app.get("/api/backlogs")
async def list_backlogs(user: dict = Depends(require_user)):
    today = ist_today_str()
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM backlogs WHERE user_id = ? ORDER BY is_completed ASC, CASE priority WHEN 'High' THEN 1 WHEN 'Medium' THEN 2 ELSE 3 END, target_date ASC",
            (user["id"],),
        ).fetchall()
    items = [dict(r) for r in rows]
    total = len(items)
    completed = sum(1 for i in items if i["is_completed"])
    high_pri = sum(1 for i in items if not i["is_completed"] and i["priority"] == "High")
    overdue = sum(1 for i in items if not i["is_completed"] and i["target_date"] < today)
    suggestions = [i for i in items if not i["is_completed"]][:3]
    return {
        "backlogs": items,
        "stats": {
            "total": total,
            "completed": completed,
            "pending": total - completed,
            "high_priority": high_pri,
            "overdue": overdue,
        },
        "suggested_for_today": suggestions,
    }


@app.post("/api/backlogs")
async def create_backlog(req: BacklogReq, user: dict = Depends(require_user)):
    if req.priority not in ("High", "Medium", "Low"):
        raise HTTPException(400, "Priority must be High, Medium, or Low.")
    with get_db() as db:
        cur = db.execute(
            """INSERT INTO backlogs (user_id, subject_name, chapter_title, priority, target_date, is_completed, created_at)
               VALUES (?, ?, ?, ?, ?, 0, ?)""",
            (user["id"], req.subject_name.strip(), req.chapter_title.strip(), req.priority, req.target_date, ist_now().isoformat()),
        )
    return {"id": cur.lastrowid, "ok": True}


@app.put("/api/backlogs/{backlog_id}")
async def toggle_backlog(backlog_id: int, is_completed: bool = Body(..., embed=True), user: dict = Depends(require_user)):
    with get_db() as db:
        comp_at = ist_now().isoformat() if is_completed else None
        res = db.execute(
            "UPDATE backlogs SET is_completed = ?, completed_at = ? WHERE id = ? AND user_id = ?",
            (1 if is_completed else 0, comp_at, backlog_id, user["id"]),
        )
        if res.rowcount == 0:
            raise HTTPException(404, "Backlog item not found.")
    if is_completed:
        evaluate_badges(user["id"])
    return {"ok": True}


@app.delete("/api/backlogs/{backlog_id}")
async def delete_backlog(backlog_id: int, user: dict = Depends(require_user)):
    with get_db() as db:
        res = db.execute("DELETE FROM backlogs WHERE id = ? AND user_id = ?", (backlog_id, user["id"]))
        if res.rowcount == 0:
            raise HTTPException(404, "Backlog item not found.")
    return {"ok": True}


# --- Spaced Repetition Revision System ---
@app.get("/api/revisions")
async def list_revisions(user: dict = Depends(require_user)):
    today = ist_today_str()
    with get_db() as db:
        rows = db.execute(
            """SELECT r.*, c.title as chapter_title, c.chapter_number, s.name as subject_name
               FROM revisions r
               JOIN chapters c ON c.id = r.chapter_id
               JOIN subjects s ON s.id = r.subject_id
               WHERE r.user_id = ?
               ORDER BY r.due_date ASC, r.revision_number ASC""",
            (user["id"],),
        ).fetchall()
        rset = db.execute("SELECT intervals_json FROM revision_settings WHERE user_id = ?", (user["id"],)).fetchone()
        intervals = json.loads(rset["intervals_json"]) if rset else [3, 7, 15, 30]

    items = []
    for r in rows:
        d = dict(r)
        d["is_overdue"] = bool(d["status"] == "pending" and d["due_date"] < today)
        d["is_due_today"] = bool(d["status"] == "pending" and d["due_date"] == today)
        items.append(d)

    return {
        "upcoming": [i for i in items if i["status"] == "pending"],
        "overdue": [i for i in items if i["is_overdue"]],
        "history": [i for i in items if i["status"] == "completed"],
        "intervals": intervals,
    }


@app.post("/api/revisions/{rev_id}/complete")
async def complete_revision(rev_id: int, user: dict = Depends(require_user)):
    with get_db() as db:
        res = db.execute(
            "UPDATE revisions SET status = 'completed', completed_at = ? WHERE id = ? AND user_id = ?",
            (ist_now().isoformat(), rev_id, user["id"]),
        )
        if res.rowcount == 0:
            raise HTTPException(404, "Revision not found.")
    return {"ok": True}


@app.put("/api/revisions/settings")
async def update_revision_settings(intervals: list[int] = Body(..., embed=True), user: dict = Depends(require_user)):
    if not intervals or len(intervals) > 6 or any(i < 1 or i > 180 for i in intervals):
        raise HTTPException(400, "Provide 1 to 6 positive day intervals (1-180 days).")
    with get_db() as db:
        db.execute(
            """INSERT INTO revision_settings (user_id, intervals_json) VALUES (?, ?)
               ON CONFLICT(user_id) DO UPDATE SET intervals_json = excluded.intervals_json""",
            (user["id"], json.dumps(intervals)),
        )
    return {"intervals": intervals}


# --- Study Calendar & Daily Planner ---
@app.get("/api/calendar")
async def get_study_calendar(month: Optional[str] = None, user: dict = Depends(require_user)):
    m_prefix = month or ist_now().strftime("%Y-%m")
    board = user.get("board") or "CBSE"
    stream = user.get("stream") or "PCM"
    with get_db() as db:
        study_days = [
            dict(r)
            for r in db.execute(
                "SELECT session_date as date, SUM(duration_minutes) as minutes FROM study_sessions WHERE user_id = ? AND session_date LIKE ? GROUP BY session_date",
                (user["id"], f"{m_prefix}%"),
            ).fetchall()
        ]
        exams = [
            dict(r)
            for r in db.execute(
                "SELECT name, exam_date as date, exam_time FROM subjects WHERE board = ? AND stream = ? AND exam_date LIKE ?",
                (board, stream, f"{m_prefix}%"),
            ).fetchall()
        ]
        backlogs = [
            dict(r)
            for r in db.execute(
                "SELECT subject_name, chapter_title, target_date as date, priority FROM backlogs WHERE user_id = ? AND is_completed = 0 AND target_date LIKE ?",
                (user["id"], f"{m_prefix}%"),
            ).fetchall()
        ]
        revisions = [
            dict(r)
            for r in db.execute(
                """SELECT r.due_date as date, r.revision_number, c.title as chapter_title, s.name as subject_name
                   FROM revisions r JOIN chapters c ON c.id = r.chapter_id JOIN subjects s ON s.id = r.subject_id
                   WHERE r.user_id = ? AND r.status = 'pending' AND r.due_date LIKE ?""",
                (user["id"], f"{m_prefix}%"),
            ).fetchall()
        ]
    return {"month": m_prefix, "study_days": study_days, "exams": exams, "backlogs": backlogs, "revisions": revisions}


class DailyPlanReq(BaseModel):
    plan_date: Optional[str] = None
    subject_name: str
    chapter_title: str
    goal_text: str = ""


@app.get("/api/planner")
async def get_daily_planner(date: Optional[str] = None, user: dict = Depends(require_user)):
    p_date = date or ist_today_str()
    with get_db() as db:
        plans = [dict(r) for r in db.execute("SELECT * FROM daily_plans WHERE user_id = ? AND plan_date = ? ORDER BY id ASC", (user["id"], p_date)).fetchall()]
    return {"date": p_date, "plans": plans}


@app.post("/api/planner")
async def add_daily_plan(req: DailyPlanReq, user: dict = Depends(require_user)):
    p_date = req.plan_date or ist_today_str()
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO daily_plans (user_id, plan_date, subject_name, chapter_title, goal_text, is_completed, created_at) VALUES (?, ?, ?, ?, ?, 0, ?)",
            (user["id"], p_date, req.subject_name.strip(), req.chapter_title.strip(), req.goal_text.strip(), ist_now().isoformat()),
        )
    return {"id": cur.lastrowid, "ok": True}


@app.put("/api/planner/{plan_id}")
async def toggle_daily_plan(plan_id: int, is_completed: bool = Body(..., embed=True), user: dict = Depends(require_user)):
    with get_db() as db:
        res = db.execute("UPDATE daily_plans SET is_completed = ? WHERE id = ? AND user_id = ?", (1 if is_completed else 0, plan_id, user["id"]))
        if res.rowcount == 0:
            raise HTTPException(404, "Plan item not found.")
    return {"ok": True}


@app.delete("/api/planner/{plan_id}")
async def delete_daily_plan(plan_id: int, user: dict = Depends(require_user)):
    with get_db() as db:
        db.execute("DELETE FROM daily_plans WHERE id = ? AND user_id = ?", (plan_id, user["id"]))
    return {"ok": True}


# --- Notifications ---
@app.get("/api/notifications")
async def list_notifications(user: dict = Depends(require_user)):
    with get_db() as db:
        rows = db.execute("SELECT * FROM notifications WHERE user_id = ? ORDER BY id DESC LIMIT 40", (user["id"],)).fetchall()
        unread = db.execute("SELECT COUNT(*) as c FROM notifications WHERE user_id = ? AND is_read = 0", (user["id"],)).fetchone()["c"]
        prefs = db.execute("SELECT prefs_json FROM notification_prefs WHERE user_id = ?", (user["id"],)).fetchone()
    return {
        "notifications": [dict(r) for r in rows],
        "unread_count": unread,
        "prefs": json.loads(prefs["prefs_json"]) if prefs else {},
    }


@app.post("/api/notifications/read-all")
async def read_all_notifications(user: dict = Depends(require_user)):
    with get_db() as db:
        db.execute("UPDATE notifications SET is_read = 1 WHERE user_id = ?", (user["id"],))
    return {"ok": True}


@app.put("/api/notifications/prefs")
async def update_notification_prefs(prefs: dict = Body(...), user: dict = Depends(require_user)):
    with get_db() as db:
        db.execute(
            """INSERT INTO notification_prefs (user_id, prefs_json) VALUES (?, ?)
               ON CONFLICT(user_id) DO UPDATE SET prefs_json = excluded.prefs_json""",
            (user["id"], json.dumps(prefs)),
        )
    return {"prefs": prefs}


# --- Moderator Endpoints (Strict Boundaries: NO admin stats, NO syllabus/timetable edits, NO perm bans) ---
@app.get("/api/mod/reports")
async def mod_list_reports(user: dict = Depends(require_mod_or_admin)):
    with get_db() as db:
        rows = db.execute(
            """SELECT mr.*, m.content as message_content, m.user_id as author_id, m.channel_id,
                      au.username as author_username, ru.username as reporter_username, ch.name as channel_name
               FROM message_reports mr
               JOIN chat_messages m ON m.id = mr.message_id
               JOIN users au ON au.id = m.user_id
               JOIN users ru ON ru.id = mr.reporter_id
               JOIN chat_channels ch ON ch.id = m.channel_id
               ORDER BY mr.id DESC LIMIT 50"""
        ).fetchall()
    return {"reports": [dict(r) for r in rows]}


@app.post("/api/mod/reports/{report_id}/resolve")
async def mod_resolve_report(report_id: int, action: str = Body(..., embed=True), user: dict = Depends(require_mod_or_admin)):
    if action not in ("delete_message", "dismiss", "warn", "mute_24h"):
        raise HTTPException(400, "Invalid moderation action.")
    with get_db() as db:
        rep = db.execute(
            "SELECT mr.*, m.user_id as author_id FROM message_reports mr JOIN chat_messages m ON m.id = mr.message_id WHERE mr.id = ?",
            (report_id,),
        ).fetchone()
        if not rep:
            raise HTTPException(404, "Report not found.")
        if action == "delete_message":
            db.execute("UPDATE chat_messages SET is_deleted = 1 WHERE id = ?", (rep["message_id"],))
            db.execute("UPDATE message_reports SET status = 'reviewed', reviewed_by = ? WHERE id = ?", (user["id"], report_id))
        elif action == "dismiss":
            db.execute("UPDATE message_reports SET status = 'dismissed', reviewed_by = ? WHERE id = ?", (user["id"], report_id))
        elif action == "warn":
            db.execute("UPDATE message_reports SET status = 'reviewed', reviewed_by = ? WHERE id = ?", (user["id"], report_id))
            create_notification(rep["author_id"], "announcement", "Community Warning", "A moderator issued a warning regarding your recent message. Please follow community guidelines.", "#community")
        elif action == "mute_24h":
            until = (ist_now() + timedelta(hours=24)).isoformat()
            db.execute("UPDATE users SET mute_until = ? WHERE id = ?", (until, rep["author_id"]))
            db.execute("UPDATE message_reports SET status = 'reviewed', reviewed_by = ? WHERE id = ?", (user["id"], report_id))
            create_notification(rep["author_id"], "announcement", "Temporary Chat Mute (24h)", "Your chat privileges have been muted for 24 hours by a moderator.", "#community")

    log_audit(user["id"], user["role"], f"mod_{action}", "report", str(report_id), f"Target user {rep['author_id']}")
    return {"ok": True}


@app.post("/api/mod/channels/{channel_id}/lock")
async def mod_toggle_lock_channel(channel_id: int, is_locked: bool = Body(..., embed=True), user: dict = Depends(require_mod_or_admin)):
    with get_db() as db:
        db.execute("UPDATE chat_channels SET is_locked = ? WHERE id = ?", (1 if is_locked else 0, channel_id))
    log_audit(user["id"], user["role"], "toggle_channel_lock", "chat_channel", str(channel_id), f"locked={is_locked}")
    return {"ok": True}


# --- Super Administrator Endpoints (/api/admin/* — Strictly require role == 'admin') ---
@app.get("/api/admin/overview")
async def admin_overview(
    board: str = Query(default=""),
    stream: str = Query(default=""),
    activity: str = Query(default=""),
    date: str = Query(default=""),
    admin: dict = Depends(require_admin),
):
    target_date = date or ist_today_str()
    w_start, w_end = get_week_bounds()
    with get_db() as db:
        sql = """SELECT id, full_name, username, role, board, stream, profile_pic, daily_target_minutes,
                        is_suspended, ban_type, ban_reason, ban_until, mute_until, mod_categories_json, last_active_at, created_at
                 FROM users WHERE role != 'admin'"""
        params: list[Any] = []
        if board:
            sql += " AND board = ?"
            params.append(board)
        if stream:
            sql += " AND stream = ?"
            params.append(stream)
        sql += " ORDER BY created_at DESC"
        students = db.execute(sql, tuple(params)).fetchall()

        student_rows = []
        below_6h = 0
        unreported = 0
        for s in students:
            uid = s["id"]
            d_res = db.execute("SELECT COALESCE(SUM(duration_minutes), 0) as m, COUNT(*) as c FROM study_sessions WHERE user_id = ? AND session_date = ?", (uid, target_date)).fetchone()
            w_mins = db.execute("SELECT COALESCE(SUM(duration_minutes), 0) as m FROM study_sessions WHERE user_id = ? AND session_date BETWEEN ? AND ?", (uid, w_start, w_end)).fetchone()["m"]
            l_mins = db.execute("SELECT COALESCE(SUM(duration_minutes), 0) as m FROM study_sessions WHERE user_id = ?", (uid,)).fetchone()["m"]

            total_ch = db.execute("SELECT COUNT(*) as c FROM chapters ch JOIN subjects sub ON sub.id = ch.subject_id WHERE sub.board = ? AND sub.stream = ?", (s["board"], s["stream"])).fetchone()["c"]
            done_ch = db.execute("SELECT COUNT(*) as c FROM chapter_progress cp JOIN chapters ch ON ch.id = cp.chapter_id JOIN subjects sub ON sub.id = ch.subject_id WHERE cp.user_id = ? AND cp.full_done = 1 AND sub.board = ? AND sub.stream = ?", (uid, s["board"], s["stream"])).fetchone()["c"]
            backlogs_pending = db.execute("SELECT COUNT(*) as c FROM backlogs WHERE user_id = ? AND is_completed = 0", (uid,)).fetchone()["c"]
            revs_done = db.execute("SELECT COUNT(*) as c FROM revisions WHERE user_id = ? AND status = 'completed'", (uid,)).fetchone()["c"]
            msg_cnt = db.execute("SELECT COUNT(*) as c FROM chat_messages WHERE user_id = ? AND is_deleted = 0", (uid,)).fetchone()["c"]

            reported = d_res["c"] > 0
            d_mins = d_res["m"] if reported else 0
            if not reported:
                unreported += 1
            elif d_mins < (s["daily_target_minutes"] or 360):
                below_6h += 1

            if activity == "below_target" and (not reported or d_mins >= (s["daily_target_minutes"] or 360)):
                continue
            if activity == "unreported" and reported:
                continue

            sd = dict(s)
            sd["daily_reported"] = reported
            sd["daily_minutes"] = d_mins if reported else None
            sd["weekly_minutes"] = w_mins
            sd["lifetime_minutes"] = l_mins
            sd["completed_chapters"] = done_ch
            sd["total_chapters"] = total_ch
            sd["syllabus_pct"] = round((done_ch / total_ch) * 100.0, 1) if total_ch > 0 else 0.0
            sd["pending_backlogs"] = backlogs_pending
            sd["revisions_completed"] = revs_done
            sd["community_messages"] = msg_cnt
            student_rows.append(sd)

        audit = [dict(r) for r in db.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT 40").fetchall()]
        timetables = [dict(r) for r in db.execute("SELECT * FROM exam_timetables").fetchall()]
        settings = {r["key"]: r["value"] for r in db.execute("SELECT * FROM platform_settings").fetchall()}
        appeals = [dict(r) for r in db.execute("SELECT ba.*, u.username, u.full_name FROM ban_appeals ba JOIN users u ON u.id = ba.user_id ORDER BY ba.id DESC LIMIT 20").fetchall()]

    return {
        "date": target_date,
        "summary": {
            "total_students": len(students),
            "below_6h": below_6h,
            "unreported": unreported,
        },
        "students": student_rows,
        "timetables": timetables,
        "settings": settings,
        "audit_logs": audit,
        "appeals": appeals,
    }


class AdminModAssignReq(BaseModel):
    role: str
    mod_categories: list[str] = []


@app.post("/api/admin/students/{student_id}/role")
async def admin_set_role(student_id: int, req: AdminModAssignReq, admin: dict = Depends(require_admin)):
    if req.role not in ("student", "moderator"):
        raise HTTPException(400, "Role must be 'student' or 'moderator'. Super admin cannot be assigned via UI.")
    with get_db() as db:
        res = db.execute(
            "UPDATE users SET role = ?, mod_categories_json = ? WHERE id = ? AND role != 'admin'",
            (req.role, json.dumps(req.mod_categories), student_id),
        )
        if res.rowcount == 0:
            raise HTTPException(404, "Student not found.")
    log_audit(admin["id"], "admin", f"set_role_{req.role}", "user", str(student_id), json.dumps(req.mod_categories))
    create_notification(student_id, "announcement", "Role Updated", f"An administrator updated your community role to {req.role}.", "#community")
    return {"ok": True}


class AdminBanReq(BaseModel):
    action: str  # suspend, unsuspend, temp_ban, perm_ban, unban, mute, delete, reset
    reason: str = ""
    duration_days: int = 7
    confirm: bool = False


@app.post("/api/admin/students/{student_id}/moderation")
async def admin_moderate_student(student_id: int, req: AdminBanReq, admin: dict = Depends(require_admin)):
    now = ist_now()
    with get_db() as db:
        target = db.execute("SELECT id, username, role FROM users WHERE id = ?", (student_id,)).fetchone()
        if not target or target["role"] == "admin":
            raise HTTPException(404, "Student not found.")

        if req.action == "suspend":
            db.execute("UPDATE users SET is_suspended = 1 WHERE id = ?", (student_id,))
        elif req.action == "unsuspend":
            db.execute("UPDATE users SET is_suspended = 0 WHERE id = ?", (student_id,))
        elif req.action == "temp_ban":
            if not req.reason.strip():
                raise HTTPException(400, "Ban reason is required.")
            until = (now + timedelta(days=max(1, req.duration_days))).isoformat()
            db.execute("UPDATE users SET ban_type = 'temp', ban_reason = ?, ban_until = ? WHERE id = ?", (req.reason.strip(), until, student_id))
            db.execute("UPDATE sessions SET revoked = 1 WHERE user_id = ?", (student_id,))
        elif req.action == "perm_ban":
            if not req.reason.strip() or not req.confirm:
                raise HTTPException(400, "Permanent ban requires a reason and explicit confirmation.")
            db.execute("UPDATE users SET ban_type = 'perm', ban_reason = ?, ban_until = NULL WHERE id = ?", (req.reason.strip(), student_id))
            db.execute("UPDATE sessions SET revoked = 1 WHERE user_id = ?", (student_id,))
        elif req.action == "unban":
            db.execute("UPDATE users SET ban_type = 'none', ban_reason = '', ban_until = NULL, is_suspended = 0, mute_until = NULL WHERE id = ?", (student_id,))
        elif req.action == "mute":
            until = (now + timedelta(days=max(1, req.duration_days))).isoformat()
            db.execute("UPDATE users SET mute_until = ? WHERE id = ?", (until, student_id))
        elif req.action == "reset":
            if not req.confirm:
                raise HTTPException(400, "Explicit confirmation required to reset student account.")
            db.execute("DELETE FROM chapter_progress WHERE user_id = ?", (student_id,))
            db.execute("DELETE FROM study_sessions WHERE user_id = ?", (student_id,))
            db.execute("DELETE FROM backlogs WHERE user_id = ?", (student_id,))
            db.execute("DELETE FROM revisions WHERE user_id = ?", (student_id,))
        elif req.action == "delete":
            if not req.confirm:
                raise HTTPException(400, "Explicit confirmation required to delete student account.")
            db.execute("DELETE FROM users WHERE id = ?", (student_id,))
        else:
            raise HTTPException(400, "Unknown moderation action.")

    log_audit(admin["id"], "admin", f"admin_{req.action}", "user", str(student_id), req.reason.strip())
    return {"ok": True}


class TimetableUpdateReq(BaseModel):
    board: str
    official_url: str
    status: str
    last_verified: str
    exam_start_date: Optional[str] = None
    exam_end_date: Optional[str] = None
    notes: str = ""


@app.put("/api/admin/timetables")
async def admin_update_timetable(req: TimetableUpdateReq, admin: dict = Depends(require_admin)):
    with get_db() as db:
        db.execute(
            """UPDATE exam_timetables
               SET official_url = ?, status = ?, last_verified = ?, exam_start_date = ?, exam_end_date = ?, notes = ?
               WHERE board = ?""",
            (req.official_url.strip(), req.status, req.last_verified, req.exam_start_date, req.exam_end_date, req.notes.strip(), req.board),
        )
    log_audit(admin["id"], "admin", "update_timetable", "exam_timetable", req.board, f"status={req.status}")
    return {"ok": True}


class SubjectExamUpdateReq(BaseModel):
    exam_date: Optional[str] = None
    exam_time: str = ""


@app.put("/api/admin/subjects/{subject_id}/exam")
async def admin_update_subject_exam(subject_id: int, req: SubjectExamUpdateReq, admin: dict = Depends(require_admin)):
    with get_db() as db:
        db.execute("UPDATE subjects SET exam_date = ?, exam_time = ? WHERE id = ?", (req.exam_date, req.exam_time, subject_id))
    log_audit(admin["id"], "admin", "update_subject_exam", "subject", str(subject_id), f"{req.exam_date} {req.exam_time}")
    return {"ok": True}


@app.get("/api/admin/academics")
async def admin_list_academics(admin: dict = Depends(require_admin)):
    with get_db() as db:
        subjects = [dict(r) for r in db.execute("SELECT * FROM subjects ORDER BY board, stream, display_order").fetchall()]
        chapters = [dict(r) for r in db.execute("SELECT * FROM chapters ORDER BY subject_id, chapter_number").fetchall()]
        quotes = [dict(r) for r in db.execute("SELECT * FROM quotes ORDER BY id DESC").fetchall()]
    return {"subjects": subjects, "chapters": chapters, "quotes": quotes}


@app.post("/api/admin/chapters")
async def admin_add_chapter(subject_id: int = Body(...), chapter_number: int = Body(...), title: str = Body(...), unit_name: str = Body(default=""), admin: dict = Depends(require_admin)):
    with get_db() as db:
        cur = db.execute("INSERT INTO chapters (subject_id, chapter_number, title, unit_name) VALUES (?, ?, ?, ?)", (subject_id, chapter_number, title.strip(), unit_name.strip()))
    log_audit(admin["id"], "admin", "add_chapter", "chapter", str(cur.lastrowid), title.strip())
    return {"id": cur.lastrowid, "ok": True}


@app.delete("/api/admin/chapters/{chapter_id}")
async def admin_delete_chapter(chapter_id: int, admin: dict = Depends(require_admin)):
    with get_db() as db:
        db.execute("DELETE FROM chapters WHERE id = ?", (chapter_id,))
    log_audit(admin["id"], "admin", "delete_chapter", "chapter", str(chapter_id), "")
    return {"ok": True}


@app.post("/api/admin/quotes")
async def admin_add_quote(text: str = Body(...), author: str = Body(...), category: str = Body(default="Discipline"), admin: dict = Depends(require_admin)):
    with get_db() as db:
        cur = db.execute("INSERT INTO quotes (text, author, category) VALUES (?, ?, ?)", (text.strip(), author.strip(), category.strip()))
    log_audit(admin["id"], "admin", "add_quote", "quote", str(cur.lastrowid), author.strip())
    return {"id": cur.lastrowid, "ok": True}


@app.post("/api/admin/announcements")
async def admin_create_announcement(title: str = Body(...), body: str = Body(...), admin: dict = Depends(require_admin)):
    now_iso = ist_now().isoformat()
    with get_db() as db:
        cur = db.execute("INSERT INTO announcements (title, body, created_by, is_active, created_at) VALUES (?, ?, ?, 1, ?)", (title.strip(), body.strip(), admin["id"], now_iso))
        students = db.execute("SELECT id FROM users WHERE role IN ('student','moderator')").fetchall()
        for s in students:
            db.execute("INSERT INTO notifications (user_id, type, title, body, link, created_at) VALUES (?, 'announcement', ?, ?, '#home', ?)", (s["id"], title.strip(), body.strip(), now_iso))
    log_audit(admin["id"], "admin", "create_announcement", "announcement", str(cur.lastrowid), title.strip())
    return {"id": cur.lastrowid, "ok": True}


@app.put("/api/admin/settings")
async def admin_update_settings(settings: dict = Body(...), admin: dict = Depends(require_admin)):
    with get_db() as db:
        for k, v in settings.items():
            db.execute("INSERT INTO platform_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (str(k), str(v)))
    log_audit(admin["id"], "admin", "update_settings", "platform_settings", "global", json.dumps(settings))
    return {"ok": True}


@app.post("/api/admin/trigger-daily-report")
async def admin_trigger_daily_report(admin: dict = Depends(require_admin)):
    payload = generate_daily_report(ist_today_str(), notify=True)
    log_audit(admin["id"], "admin", "trigger_daily_report", "daily_report", ist_today_str(), "")
    return payload


@app.post("/api/admin/trigger-weekly-archive")
async def admin_trigger_weekly_archive(admin: dict = Depends(require_admin)):
    w_start, w_end = get_week_bounds()
    data = publish_weekly_archive(w_start, w_end)
    log_audit(admin["id"], "admin", "trigger_weekly_archive", "weekly_archive", w_start, "")
    return data
