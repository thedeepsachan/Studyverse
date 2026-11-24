import asyncio
import json
from datetime import datetime, timedelta
from app.config import IST
from app.database import get_db


def ist_now() -> datetime:
    return datetime.now(IST)


def ist_today_str() -> str:
    return ist_now().strftime("%Y-%m-%d")


def get_week_bounds(ref_date: datetime | None = None) -> tuple[str, str]:
    now = ref_date or ist_now()
    monday = now - timedelta(days=now.weekday())
    sunday = monday + timedelta(days=6)
    return monday.strftime("%Y-%m-%d"), sunday.strftime("%Y-%m-%d")


def get_quote_of_the_day() -> dict:
    today = ist_today_str()
    with get_db() as db:
        row = db.execute("SELECT * FROM quotes WHERE last_shown_date = ? LIMIT 1", (today,)).fetchone()
        if row:
            return dict(row)
        nxt = db.execute(
            "SELECT * FROM quotes ORDER BY COALESCE(last_shown_date, '0000-00-00') ASC, id ASC LIMIT 1"
        ).fetchone()
        if not nxt:
            return {"id": 0, "text": "Consistency compounds every single day.", "author": "STUDYVERSE", "category": "Consistency"}
        db.execute("UPDATE quotes SET last_shown_date = ? WHERE id = ?", (today, nxt["id"]))
        res = dict(nxt)
        res["last_shown_date"] = today
        return res


def compute_leaderboard_entries(week_start: str, week_end: str) -> list[dict]:
    prev_start = (datetime.strptime(week_start, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")
    prev_end = (datetime.strptime(week_start, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")

    with get_db() as db:
        students = db.execute(
            """SELECT id, full_name, username, profile_pic, board, stream, daily_target_minutes
               FROM users
               WHERE role IN ('student', 'moderator') AND ban_type = 'none' AND is_suspended = 0 AND onboarding_complete = 1"""
        ).fetchall()

        entries = []
        for s in students:
            uid = s["id"]
            target_min = s["daily_target_minutes"] or 360

            # Current week daily breakdown
            rows = db.execute(
                """SELECT session_date, SUM(duration_minutes) as mins
                   FROM study_sessions
                   WHERE user_id = ? AND session_date BETWEEN ? AND ?
                   GROUP BY session_date""",
                (uid, week_start, week_end),
            ).fetchall()
            day_map = {r["session_date"]: int(r["mins"] or 0) for r in rows}
            weekly_minutes = sum(day_map.values())
            days_met_goal = sum(1 for m in day_map.values() if m >= target_min)
            consistency_pct = round((days_met_goal / 7.0) * 100.0, 1)
            goal_achievement_pct = round(min(100.0, (weekly_minutes / (target_min * 7.0)) * 100.0), 1)

            # Previous week minutes
            prev_row = db.execute(
                """SELECT COALESCE(SUM(duration_minutes), 0) as mins
                   FROM study_sessions WHERE user_id = ? AND session_date BETWEEN ? AND ?""",
                (uid, prev_start, prev_end),
            ).fetchone()
            prev_minutes = int(prev_row["mins"] or 0)

            # Syllabus completion %
            total_ch = db.execute(
                """SELECT COUNT(*) as c FROM chapters c
                   JOIN subjects sub ON sub.id = c.subject_id
                   WHERE sub.board = ? AND sub.stream = ?""",
                (s["board"], s["stream"]),
            ).fetchone()["c"]
            done_ch = db.execute(
                """SELECT COUNT(*) as c FROM chapter_progress cp
                   JOIN chapters c ON c.id = cp.chapter_id
                   JOIN subjects sub ON sub.id = c.subject_id
                   WHERE cp.user_id = ? AND cp.full_done = 1 AND sub.board = ? AND sub.stream = ?""",
                (uid, s["board"], s["stream"]),
            ).fetchone()["c"]
            syllabus_pct = round((done_ch / total_ch) * 100.0, 1) if total_ch > 0 else 0.0

            # Transparent combined score rewarding consistency + syllabus + verified hours
            weekly_hours = round(weekly_minutes / 60.0, 2)
            effective_hours = min(weekly_hours, 84.0)
            combined_score = round(effective_hours * 1.0 + consistency_pct * 0.4 + syllabus_pct * 0.3, 1)

            # 7-day array Mon..Sun
            daily_series = []
            start_dt = datetime.strptime(week_start, "%Y-%m-%d")
            for i in range(7):
                d_str = (start_dt + timedelta(days=i)).strftime("%Y-%m-%d")
                daily_series.append({"date": d_str, "minutes": day_map.get(d_str, 0)})

            entries.append({
                "user_id": uid,
                "full_name": s["full_name"],
                "username": s["username"],
                "profile_pic": s["profile_pic"] or "",
                "board": s["board"],
                "stream": s["stream"],
                "weekly_minutes": weekly_minutes,
                "weekly_hours": weekly_hours,
                "prev_weekly_minutes": prev_minutes,
                "consistency_pct": consistency_pct,
                "days_met_goal": days_met_goal,
                "syllabus_pct": syllabus_pct,
                "goal_achievement_pct": goal_achievement_pct,
                "combined_score": combined_score,
                "daily_series": daily_series,
            })

        entries.sort(key=lambda x: (x["combined_score"], x["consistency_pct"], x["weekly_minutes"]), reverse=True)
        for idx, item in enumerate(entries, start=1):
            item["rank"] = idx
        return entries


def generate_daily_report(report_date_str: str | None = None, notify: bool = True) -> dict:
    r_date = report_date_str or ist_today_str()
    now_iso = ist_now().isoformat()

    with get_db() as db:
        students = db.execute(
            """SELECT id, full_name, username, profile_pic, board, stream, daily_target_minutes
               FROM users
               WHERE role IN ('student', 'moderator') AND ban_type = 'none' AND is_suspended = 0 AND onboarding_complete = 1
               ORDER BY full_name ASC"""
        ).fetchall()

        rows = []
        reported_mins = []
        for s in students:
            uid = s["id"]
            res = db.execute(
                "SELECT COALESCE(SUM(duration_minutes), 0) as mins, COUNT(*) as cnt FROM study_sessions WHERE user_id = ? AND session_date = ?",
                (uid, r_date),
            ).fetchone()
            cnt = int(res["cnt"])
            mins = int(res["mins"] or 0)
            target = s["daily_target_minutes"] or 360
            reported = cnt > 0
            if reported:
                reported_mins.append(mins)
            rows.append({
                "user_id": uid,
                "full_name": s["full_name"],
                "username": s["username"],
                "profile_pic": s["profile_pic"] or "",
                "board": s["board"],
                "stream": s["stream"],
                "reported": reported,
                "study_minutes": mins if reported else None,
                "status_label": f"{mins // 60}h {mins % 60}m" if reported else "Not reported",
                "target_minutes": target,
                "target_achieved": bool(reported and mins >= target),
            })

        total_minutes = sum(reported_mins)
        avg_minutes = int(round(total_minutes / len(reported_mins))) if reported_mins else 0
        highest_minutes = max(reported_mins) if reported_mins else 0

        payload = {
            "report_date": r_date,
            "total_minutes": total_minutes,
            "avg_minutes": avg_minutes,
            "highest_minutes": highest_minutes,
            "reported_count": len(reported_mins),
            "total_students": len(rows),
            "students": rows,
        }

        db.execute(
            """INSERT INTO daily_reports (report_date, total_minutes, avg_minutes, highest_minutes, data_json, generated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(report_date) DO UPDATE SET
                 total_minutes = excluded.total_minutes,
                 avg_minutes = excluded.avg_minutes,
                 highest_minutes = excluded.highest_minutes,
                 data_json = excluded.data_json,
                 generated_at = excluded.generated_at""",
            (r_date, total_minutes, avg_minutes, highest_minutes, json.dumps(payload), now_iso),
        )

        if notify:
            for s in students:
                db.execute(
                    """INSERT INTO notifications (user_id, type, title, body, link, created_at)
                       VALUES (?, 'daily_report', ?, ?, '#leaderboard', ?)""",
                    (
                        s["id"],
                        f"11:00 PM Daily Progress Report ({r_date})",
                        f"Community total: {total_minutes // 60}h {total_minutes % 60}m across {len(reported_mins)} reported students.",
                        now_iso,
                    ),
                )

    return payload


def publish_weekly_archive(week_start: str | None = None, week_end: str | None = None) -> dict:
    if not week_start or not week_end:
        week_start, week_end = get_week_bounds()
    entries = compute_leaderboard_entries(week_start, week_end)
    now_iso = ist_now().isoformat()
    archive_data = {
        "week_start": week_start,
        "week_end": week_end,
        "entries": entries,
    }
    with get_db() as db:
        db.execute(
            """INSERT INTO weekly_archives (week_start, week_end, data_json, published_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(week_start) DO UPDATE SET
                 week_end = excluded.week_end,
                 data_json = excluded.data_json,
                 published_at = excluded.published_at""",
            (week_start, week_end, json.dumps(archive_data), now_iso),
        )
        if entries:
            topper_id = entries[0]["user_id"]
            db.execute(
                "INSERT OR IGNORE INTO user_badges (user_id, badge_key, earned_at) VALUES (?, 'weekly_topper', ?)",
                (topper_id, now_iso),
            )
        students = db.execute("SELECT id FROM users WHERE role IN ('student','moderator')").fetchall()
        for s in students:
            db.execute(
                """INSERT INTO notifications (user_id, type, title, body, link, created_at)
                   VALUES (?, 'leaderboard', 'Weekly Leaderboard Published', ?, '#leaderboard', ?)""",
                (s["id"], f"Weekly results for {week_start} to {week_end} are now archived.", now_iso),
            )
    return archive_data


def check_overdue_revisions():
    today = ist_today_str()
    now_iso = ist_now().isoformat()
    with get_db() as db:
        due = db.execute(
            """SELECT r.user_id, COUNT(*) as cnt
               FROM revisions r
               WHERE r.status = 'pending' AND r.due_date <= ?
               GROUP BY r.user_id""",
            (today,),
        ).fetchall()
        for row in due:
            already = db.execute(
                """SELECT 1 FROM notifications
                   WHERE user_id = ? AND type = 'revision' AND created_at LIKE ?""",
                (row["user_id"], f"{today}%"),
            ).fetchone()
            if not already:
                db.execute(
                    """INSERT INTO notifications (user_id, type, title, body, link, created_at)
                       VALUES (?, 'revision', 'Revision Reminder', ?, '#revision', ?)""",
                    (row["user_id"], f"You have {row['cnt']} chapter revision(s) due or overdue today.", now_iso),
                )


async def background_scheduler_loop():
    last_daily_report_date = None
    last_weekly_archive_start = None
    last_revision_check_date = None

    while True:
        try:
            now = ist_now()
            today_str = now.strftime("%Y-%m-%d")

            # 11:00 PM IST Daily Community Progress Report
            if now.hour == 23 and now.minute == 0 and last_daily_report_date != today_str:
                generate_daily_report(today_str, notify=True)
                last_daily_report_date = today_str

            # Sunday 23:55 IST Weekly Leaderboard Archive & Reset
            if now.weekday() == 6 and now.hour == 23 and now.minute >= 55:
                w_start, w_end = get_week_bounds(now)
                if last_weekly_archive_start != w_start:
                    publish_weekly_archive(w_start, w_end)
                    last_weekly_archive_start = w_start

            # Morning 08:00 IST revision reminder check
            if now.hour >= 8 and last_revision_check_date != today_str:
                check_overdue_revisions()
                last_revision_check_date = today_str

        except Exception:
            pass
        await asyncio.sleep(30)
