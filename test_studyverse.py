import os
import sys
import tempfile
from pathlib import Path

# Configure isolated test DB and env-based super admin before importing app
TMP_DIR = tempfile.mkdtemp(prefix="sv_test_")
os.environ["STUDYVERSE_DB_PATH"] = str(Path(TMP_DIR) / "test_sv.db")
os.environ["STUDYVERSE_UPLOADS_DIR"] = str(Path(TMP_DIR) / "uploads")
os.environ["ADMIN_1_USERNAME"] = "super_adm_1"
os.environ["ADMIN_1_PASSWORD"] = "StrongTestPass#2027"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from app.main import app


def run_all_tests():
    with TestClient(app) as client:
        # 1. Serve HTML pages
        r = client.get("/")
        assert r.status_code == 200 and "STUDYVERSE" in r.text
        r = client.get("/admin")
        assert r.status_code == 200 and "Super Administrator" in r.text

        # 2. Register Student 1 & Onboarding (CBSE PCM)
        reg1 = client.post("/api/auth/register", json={"full_name": "Aarav Sharma", "username": "aarav12", "password": "Password123!"})
        assert reg1.status_code == 200, reg1.text
        rec_code = reg1.json()["recovery_code"]
        t1 = {"Authorization": f"Bearer {reg1.json()['token']}"}

        ob1 = client.post("/api/auth/onboarding", headers=t1, json={"board": "CBSE", "stream": "PCM"})
        assert ob1.status_code == 200 and ob1.json()["user"]["board"] == "CBSE"

        # 3. Verify Home Dashboard, Quote, & Official CBSE 2027 Countdown
        home = client.get("/api/dashboard/home", headers=t1).json()
        assert home["timetable"]["status"] == "published"
        assert "2027-02-16" == home["timetable"]["exam_start_date"]
        assert len(home["subject_exams"]) == 5

        # 4. Verify 5-checkbox Chapter Progress & Non-Auto Full Completion + Spaced Repetition Trigger
        subjs = client.get("/api/subjects", headers=t1).json()
        assert len(subjs["subjects"]) == 5
        phys_id = subjs["subjects"][0]["id"]
        chs = client.get(f"/api/subjects/{phys_id}/chapters", headers=t1).json()["chapters"]
        ch1 = chs[0]["id"]

        # Tick first 4 boxes -> full_done MUST remain 0
        p1 = client.put(f"/api/chapters/{ch1}/progress", headers=t1, json={
            "notes_done": True, "lectures_done": True, "theory_done": True, "pyqs_done": True
        }).json()
        assert p1["full_done"] == 0 and p1["chapter_progress_pct"] == 80.0

        # Explicitly confirm full_done -> schedules 4 revisions (+3, +7, +15, +30)
        p2 = client.put(f"/api/chapters/{ch1}/progress", headers=t1, json={"full_done": True}).json()
        assert p2["full_done"] == 1 and p2["revisions_scheduled"] == 4
        revs = client.get("/api/revisions", headers=t1).json()
        assert len(revs["upcoming"]) == 4

        # 5. Study Tracker, Live Timer, & Anti-Manipulation
        assert client.post("/api/study/sessions", headers=t1, json={"subject_id": phys_id, "duration_minutes": 180, "note": "Electrostatics"}).status_code == 200
        # Duplicate within 60s rejected
        assert client.post("/api/study/sessions", headers=t1, json={"subject_id": phys_id, "duration_minutes": 180, "note": "Electrostatics"}).status_code == 409
        assert client.post("/api/study/timer/start", headers=t1, json={"subject_id": phys_id}).status_code == 200
        assert client.post("/api/study/timer/pause", headers=t1).status_code == 200
        assert client.post("/api/study/timer/resume", headers=t1).status_code == 200
        assert client.post("/api/study/timer/stop", headers=t1, json={"note": "Timer test"}).status_code == 200

        # 6. 6-Hour Challenge Accountability
        acc_st = client.get("/api/accountability/status", headers=t1).json()
        assert acc_st["below_target"] is True
        assert "6-hour goal" in acc_st["prompt_message"]
        assert client.post("/api/accountability/posts", headers=t1, json={
            "reason_category": "Procrastination", "explanation": "Lost focus in evening", "no_explanation": False, "request_suggestions": True
        }).status_code == 200

        # 7. Real-time WebSocket Chat & REST Chat + Reporting
        chans = client.get("/api/chat/channels", headers=t1).json()["channels"]
        assert len(chans) == 23  # 14 Academic + 9 Non-Academic
        phys_ch = chans[0]["id"]
        with client.websocket_connect(f"/ws/chat/{phys_ch}?token={reg1.json()['token']}") as ws:
            msg_res = client.post(f"/api/chat/channels/{phys_ch}/messages", headers=t1, json={"content": "Hello Class 12 Physics peers!"})
            assert msg_res.status_code == 200
            ws_ev = ws.receive_json()
            assert ws_ev["type"] == "new_message" and ws_ev["message"]["content"] == "Hello Class 12 Physics peers!"

        # 8. Backlogs, Planner, Calendar, & Recovery
        bl = client.post("/api/backlogs", headers=t1, json={"subject_name": "Physics", "chapter_title": "Wave Optics", "priority": "High", "target_date": "2026-12-01"})
        assert bl.status_code == 200
        assert client.post("/api/planner", headers=t1, json={"subject_name": "Physics", "chapter_title": "Atoms", "goal_text": "20 PYQs"}).status_code == 200
        assert client.get("/api/calendar", headers=t1).status_code == 200

        # 9. Admin Login (env var), Moderator Promotion, & Strict Moderator Boundary Check
        adm_login = client.post("/api/auth/login", json={"username": "super_adm_1", "password": "StrongTestPass#2027"})
        assert adm_login.status_code == 200 and adm_login.json()["user"]["role"] == "admin"
        t_adm = {"Authorization": f"Bearer {adm_login.json()['token']}"}

        # Student cannot access /api/admin/overview
        assert client.get("/api/admin/overview", headers=t1).status_code == 403

        # Promote Aarav to Moderator
        uid1 = reg1.json()["user"]["id"]
        assert client.post(f"/api/admin/students/{uid1}/role", headers=t_adm, json={"role": "moderator", "mod_categories": ["ACADEMIC"]}).status_code == 200
        # Moderator STILL cannot access /api/admin/overview!
        assert client.get("/api/admin/overview", headers=t1).status_code == 403

        # Admin triggers Daily 11 PM Report & Weekly Archive
        rep = client.post("/api/admin/trigger-daily-report", headers=t_adm).json()
        assert rep["total_students"] >= 1
        arch = client.post("/api/admin/trigger-weekly-archive", headers=t_adm).json()
        assert len(arch["entries"]) >= 1

        # 10. Account Recovery Flow
        rec_res = client.post("/api/auth/recover", json={"username": "aarav12", "recovery_code": rec_code, "new_password": "NewPassword456!"})
        assert rec_res.status_code == 200
        assert client.post("/api/auth/login", json={"username": "aarav12", "password": "NewPassword456!"}).status_code == 200

    print("ALL STUDYVERSE END-TO-END TESTS PASSED!")


if __name__ == "__main__":
    run_all_tests()
