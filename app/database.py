import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from app.config import DATABASE_URL, DB_PATH, IST, UPLOADS_DIR, get_configured_admins

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'student' CHECK(role IN ('student','moderator','admin')),
    board TEXT CHECK(board IN ('MP Board','CBSE')),
    class_level TEXT NOT NULL DEFAULT 'Class 12',
    stream TEXT CHECK(stream IN ('PCM','PCB','Commerce','Arts / Humanities')),
    profile_pic TEXT DEFAULT '',
    bio TEXT DEFAULT '',
    daily_target_minutes INTEGER NOT NULL DEFAULT 360,
    allow_buddy_requests INTEGER NOT NULL DEFAULT 1,
    mod_categories_json TEXT DEFAULT '[]',
    is_suspended INTEGER NOT NULL DEFAULT 0,
    ban_type TEXT NOT NULL DEFAULT 'none' CHECK(ban_type IN ('none','temp','perm')),
    ban_reason TEXT DEFAULT '',
    ban_until TEXT,
    mute_until TEXT,
    recovery_hash TEXT DEFAULT '',
    onboarding_complete INTEGER NOT NULL DEFAULT 0,
    last_active_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    device_info TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS exam_timetables (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    board TEXT NOT NULL UNIQUE CHECK(board IN ('MP Board','CBSE')),
    official_url TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'published' CHECK(status IN ('published','not_published')),
    last_verified TEXT NOT NULL,
    exam_start_date TEXT,
    exam_end_date TEXT,
    notes TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS subjects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    board TEXT NOT NULL CHECK(board IN ('MP Board','CBSE')),
    stream TEXT NOT NULL CHECK(stream IN ('PCM','PCB','Commerce','Arts / Humanities')),
    name TEXT NOT NULL,
    code TEXT DEFAULT '',
    exam_date TEXT,
    exam_time TEXT DEFAULT '',
    display_order INTEGER NOT NULL DEFAULT 0,
    UNIQUE(board, stream, name)
);

CREATE TABLE IF NOT EXISTS chapters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    chapter_number INTEGER NOT NULL,
    title TEXT NOT NULL,
    unit_name TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS chapter_progress (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    chapter_id INTEGER NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
    notes_done INTEGER NOT NULL DEFAULT 0,
    lectures_done INTEGER NOT NULL DEFAULT 0,
    theory_done INTEGER NOT NULL DEFAULT 0,
    pyqs_done INTEGER NOT NULL DEFAULT 0,
    full_done INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, chapter_id)
);

CREATE TABLE IF NOT EXISTS study_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    subject_id INTEGER REFERENCES subjects(id) ON DELETE SET NULL,
    duration_minutes INTEGER NOT NULL CHECK(duration_minutes > 0 AND duration_minutes <= 960),
    session_date TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual' CHECK(source IN ('manual','timer','pomodoro')),
    note TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_study_sessions_user_date ON study_sessions(user_id, session_date);

CREATE TABLE IF NOT EXISTS active_timers (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    subject_id INTEGER REFERENCES subjects(id) ON DELETE SET NULL,
    started_at TEXT,
    accumulated_seconds INTEGER NOT NULL DEFAULT 0,
    is_paused INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS accountability_posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    post_date TEXT NOT NULL,
    studied_minutes INTEGER NOT NULL,
    target_minutes INTEGER NOT NULL,
    reason_category TEXT NOT NULL,
    explanation TEXT DEFAULT '',
    no_explanation INTEGER NOT NULL DEFAULT 0,
    request_suggestions INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, post_date)
);

CREATE TABLE IF NOT EXISTS accountability_replies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL REFERENCES accountability_posts(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    message TEXT NOT NULL,
    reply_type TEXT NOT NULL DEFAULT 'suggestion' CHECK(reply_type IN ('reply','suggestion','encourage','invite')),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL CHECK(category IN ('ACADEMIC','NON-ACADEMIC')),
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    is_locked INTEGER NOT NULL DEFAULT 0,
    display_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id INTEGER NOT NULL REFERENCES chat_channels(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    reply_to_id INTEGER REFERENCES chat_messages(id) ON DELETE SET NULL,
    is_pinned INTEGER NOT NULL DEFAULT 0,
    is_deleted INTEGER NOT NULL DEFAULT 0,
    is_edited INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_channel ON chat_messages(channel_id, id DESC);

CREATE TABLE IF NOT EXISTS chat_reactions (
    message_id INTEGER NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    emoji TEXT NOT NULL,
    PRIMARY KEY (message_id, user_id, emoji)
);

CREATE TABLE IF NOT EXISTS chat_reads (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    channel_id INTEGER NOT NULL REFERENCES chat_channels(id) ON DELETE CASCADE,
    last_read_id INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, channel_id)
);

CREATE TABLE IF NOT EXISTS message_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
    reporter_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','reviewed','dismissed')),
    reviewed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_blocks (
    blocker_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    blocked_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (blocker_id, blocked_id)
);

CREATE TABLE IF NOT EXISTS support_posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    topic TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS support_comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL REFERENCES support_posts(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS study_buddies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    requester_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    receiver_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','accepted','rejected')),
    shared_target_minutes INTEGER NOT NULL DEFAULT 360,
    created_at TEXT NOT NULL,
    UNIQUE(requester_id, receiver_id)
);

CREATE TABLE IF NOT EXISTS backlogs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    subject_id INTEGER REFERENCES subjects(id) ON DELETE SET NULL,
    subject_name TEXT NOT NULL,
    chapter_title TEXT NOT NULL,
    priority TEXT NOT NULL DEFAULT 'Medium' CHECK(priority IN ('High','Medium','Low')),
    target_date TEXT NOT NULL,
    is_completed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    chapter_id INTEGER NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    revision_number INTEGER NOT NULL,
    due_date TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','completed'))
);

CREATE TABLE IF NOT EXISTS revision_settings (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    intervals_json TEXT NOT NULL DEFAULT '[3, 7, 15, 30]'
);

CREATE TABLE IF NOT EXISTS daily_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    plan_date TEXT NOT NULL,
    subject_name TEXT NOT NULL,
    chapter_title TEXT NOT NULL,
    goal_text TEXT DEFAULT '',
    is_completed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_badges (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    badge_key TEXT NOT NULL,
    earned_at TEXT NOT NULL,
    PRIMARY KEY (user_id, badge_key)
);

CREATE TABLE IF NOT EXISTS daily_reports (
    report_date TEXT PRIMARY KEY,
    total_minutes INTEGER NOT NULL,
    avg_minutes INTEGER NOT NULL,
    highest_minutes INTEGER NOT NULL,
    data_json TEXT NOT NULL,
    generated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS weekly_archives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    week_start TEXT NOT NULL UNIQUE,
    week_end TEXT NOT NULL,
    data_json TEXT NOT NULL,
    published_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    author TEXT NOT NULL,
    category TEXT NOT NULL,
    last_shown_date TEXT
);

CREATE TABLE IF NOT EXISTS announcements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    link TEXT DEFAULT '',
    is_read INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, is_read, id DESC);

CREATE TABLE IF NOT EXISTS notification_prefs (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    prefs_json TEXT NOT NULL DEFAULT '{"revision":true,"exam":true,"daily_goal":true,"backlog":true,"community":true,"buddy":true,"announcement":true,"leaderboard":true,"daily_report":true,"browser_push":false}'
);

CREATE TABLE IF NOT EXISTS ban_appeals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected')),
    admin_note TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    actor_role TEXT NOT NULL,
    action TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT DEFAULT '',
    details TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS platform_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _qmarks_to_pct_s(sql: str) -> str:
    out = []
    in_quote = False
    i = 0
    while i < len(sql):
        ch = sql[i]
        if ch == "'":
            if in_quote and i + 1 < len(sql) and sql[i + 1] == "'":
                out.append("''")
                i += 2
                continue
            in_quote = not in_quote
            out.append(ch)
        elif ch == "?" and not in_quote:
            out.append("%s")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _translate_sql_to_pg(sql: str) -> tuple[str, bool]:
    s = sql.strip()
    if s.upper().startswith("PRAGMA"):
        return "", False
    s = s.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
    s = s.replace("COLLATE NOCASE", "")
    s = s.replace("MAX(last_read_id, excluded.last_read_id)", "GREATEST(last_read_id, excluded.last_read_id)")
    is_insert_or_ignore = bool(re.match(r"^INSERT\s+OR\s+IGNORE\s+INTO\s+", s, re.IGNORECASE))
    if is_insert_or_ignore:
        s = re.sub(r"^INSERT\s+OR\s+IGNORE\s+INTO\s+", "INSERT INTO ", s, count=1, flags=re.IGNORECASE)
        if "ON CONFLICT" not in s.upper():
            s = s.rstrip(";") + " ON CONFLICT DO NOTHING"
    needs_returning = (
        re.match(r"^INSERT\s+INTO\s+(users|exam_timetables|subjects|chapters|study_sessions|accountability_posts|accountability_replies|chat_channels|chat_messages|message_reports|support_posts|support_comments|study_buddies|backlogs|revisions|daily_plans|weekly_archives|quotes|announcements|notifications|ban_appeals|audit_logs)\b", s, re.IGNORECASE)
        and "RETURNING" not in s.upper()
        and "ON CONFLICT" not in s.upper()
    )
    if needs_returning:
        s = s.rstrip(";") + " RETURNING id"
    return _qmarks_to_pct_s(s), bool(needs_returning)


class _HybridRow(dict):
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class _PGCursor:
    def __init__(self, cur, lastrowid=None):
        self._cur = cur
        self.lastrowid = lastrowid
        self.rowcount = cur.rowcount

    def fetchone(self):
        r = self._cur.fetchone()
        return _HybridRow(r) if isinstance(r, dict) else r

    def fetchall(self):
        rows = self._cur.fetchall()
        return [_HybridRow(r) if isinstance(r, dict) else r for r in rows]


class _PGConnection:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql: str, params=()):
        pg_sql, ret_id = _translate_sql_to_pg(sql)
        if not pg_sql:
            return _PGCursor(self._conn.cursor())
        cur = self._conn.cursor()
        cur.execute(pg_sql, params if params else None)
        last_id = None
        if ret_id:
            row = cur.fetchone()
            if row and "id" in row:
                last_id = row["id"]
        return _PGCursor(cur, lastrowid=last_id)

    def executescript(self, script: str):
        for stmt in script.split(";"):
            stmt = stmt.strip()
            if not stmt:
                continue
            pg_sql, _ = _translate_sql_to_pg(stmt)
            if pg_sql:
                self._conn.cursor().execute(pg_sql)


@contextmanager
def get_db():
    if DATABASE_URL:
        import psycopg
        from psycopg.rows import dict_row
        conn = psycopg.connect(DATABASE_URL, row_factory=dict_row, prepare_threshold=None)
        try:
            yield _PGConnection(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    else:
        conn = sqlite3.connect(str(DB_PATH), timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


# Official 2027 Examination Dates (Verified November 2026)
# CBSE: https://www.cbse.gov.in/cbsenew/documents/Datesheet_ClassX%20_XII_2027_Main_06112026.pdf
# MP Board: https://mpbse.nic.in/
STREAM_SUBJECTS = {
    "PCM": ["Physics", "Chemistry", "Mathematics", "English", "Hindi"],
    "PCB": ["Physics", "Chemistry", "Biology", "English", "Hindi"],
    "Commerce": ["Accountancy", "Business Studies", "Economics", "English", "Hindi"],
    "Arts / Humanities": ["History", "Political Science", "Geography", "Economics", "English", "Hindi"],
}

CBSE_2027_DATES = {
    "Physics": ("042", "2027-02-16", "10:30 AM - 01:30 PM IST"),
    "Accountancy": ("055", "2027-02-23", "10:30 AM - 01:30 PM IST"),
    "Geography": ("029", "2027-02-25", "10:30 AM - 01:30 PM IST"),
    "Chemistry": ("043", "2027-02-27", "10:30 AM - 01:30 PM IST"),
    "English": ("301", "2027-03-05", "10:30 AM - 01:30 PM IST"),
    "Mathematics": ("041", "2027-03-09", "10:30 AM - 01:30 PM IST"),
    "Hindi": ("302", "2027-03-13", "10:30 AM - 01:30 PM IST"),
    "Economics": ("030", "2027-03-16", "10:30 AM - 01:30 PM IST"),
    "Political Science": ("028", "2027-03-19", "10:30 AM - 01:30 PM IST"),
    "Biology": ("044", "2027-03-25", "10:30 AM - 01:30 PM IST"),
    "History": ("027", "2027-03-27", "10:30 AM - 01:30 PM IST"),
    "Business Studies": ("054", "2027-04-01", "10:30 AM - 01:30 PM IST"),
}

MP_BOARD_2027_DATES = {
    "Hindi": ("MP-HIN", "2027-02-23", "09:00 AM - 12:00 PM IST"),
    "English": ("MP-ENG", "2027-02-26", "09:00 AM - 12:00 PM IST"),
    "Physics": ("MP-PHY", "2027-03-04", "09:00 AM - 12:00 PM IST"),
    "Economics": ("MP-ECO", "2027-03-04", "09:00 AM - 12:00 PM IST"),
    "Biology": ("MP-BIO", "2027-03-08", "09:00 AM - 12:00 PM IST"),
    "Political Science": ("MP-POL", "2027-03-11", "09:00 AM - 12:00 PM IST"),
    "Chemistry": ("MP-CHE", "2027-03-13", "09:00 AM - 12:00 PM IST"),
    "History": ("MP-HIS", "2027-03-13", "09:00 AM - 12:00 PM IST"),
    "Business Studies": ("MP-BST", "2027-03-13", "09:00 AM - 12:00 PM IST"),
    "Mathematics": ("MP-MAT", "2027-03-16", "09:00 AM - 12:00 PM IST"),
    "Accountancy": ("MP-ACC", "2027-03-17", "09:00 AM - 12:00 PM IST"),
    "Geography": ("MP-GEO", "2027-03-19", "09:00 AM - 12:00 PM IST"),
}

SYLLABUS_CHAPTERS = {
    "Physics": [
        ("Electrostatics", "Electric Charges and Fields"),
        ("Electrostatics", "Electrostatic Potential and Capacitance"),
        ("Current Electricity", "Current Electricity"),
        ("Magnetic Effects of Current", "Moving Charges and Magnetism"),
        ("Magnetic Effects of Current", "Magnetism and Matter"),
        ("EMI & AC", "Electromagnetic Induction"),
        ("EMI & AC", "Alternating Current"),
        ("EM Waves", "Electromagnetic Waves"),
        ("Optics", "Ray Optics and Optical Instruments"),
        ("Optics", "Wave Optics"),
        ("Modern Physics", "Dual Nature of Radiation and Matter"),
        ("Modern Physics", "Atoms"),
        ("Modern Physics", "Nuclei"),
        ("Electronic Devices", "Semiconductor Electronics"),
    ],
    "Chemistry": [
        ("Physical Chemistry", "Solutions"),
        ("Physical Chemistry", "Electrochemistry"),
        ("Physical Chemistry", "Chemical Kinetics"),
        ("Inorganic Chemistry", "The d- and f-Block Elements"),
        ("Inorganic Chemistry", "Coordination Compounds"),
        ("Organic Chemistry", "Haloalkanes and Haloarenes"),
        ("Organic Chemistry", "Alcohols, Phenols and Ethers"),
        ("Organic Chemistry", "Aldehydes, Ketones and Carboxylic Acids"),
        ("Organic Chemistry", "Amines"),
        ("Biomolecules", "Biomolecules"),
    ],
    "Mathematics": [
        ("Relations & Functions", "Relations and Functions"),
        ("Relations & Functions", "Inverse Trigonometric Functions"),
        ("Algebra", "Matrices"),
        ("Algebra", "Determinants"),
        ("Calculus", "Continuity and Differentiability"),
        ("Calculus", "Application of Derivatives"),
        ("Calculus", "Integrals"),
        ("Calculus", "Application of Integrals"),
        ("Calculus", "Differential Equations"),
        ("Vectors & 3D", "Vector Algebra"),
        ("Vectors & 3D", "Three Dimensional Geometry"),
        ("Linear Programming", "Linear Programming"),
        ("Probability", "Probability"),
    ],
    "Biology": [
        ("Reproduction", "Sexual Reproduction in Flowering Plants"),
        ("Reproduction", "Human Reproduction"),
        ("Reproduction", "Reproductive Health"),
        ("Genetics & Evolution", "Principles of Inheritance and Variation"),
        ("Genetics & Evolution", "Molecular Basis of Inheritance"),
        ("Genetics & Evolution", "Evolution"),
        ("Human Welfare", "Human Health and Disease"),
        ("Human Welfare", "Microbes in Human Welfare"),
        ("Biotechnology", "Biotechnology: Principles and Processes"),
        ("Biotechnology", "Biotechnology and its Applications"),
        ("Ecology", "Organisms and Populations"),
        ("Ecology", "Ecosystem"),
        ("Ecology", "Biodiversity and Conservation"),
    ],
    "Accountancy": [
        ("Partnership", "Accounting for Partnership Firms: Fundamentals"),
        ("Partnership", "Goodwill: Nature and Valuation"),
        ("Partnership", "Change in Profit-Sharing Ratio"),
        ("Partnership", "Admission of a Partner"),
        ("Partnership", "Retirement and Death of a Partner"),
        ("Partnership", "Dissolution of a Partnership Firm"),
        ("Company Accounts", "Accounting for Share Capital"),
        ("Company Accounts", "Issue and Redemption of Debentures"),
        ("Financial Analysis", "Financial Statements of a Company"),
        ("Financial Analysis", "Analysis of Financial Statements & Ratios"),
        ("Cash Flow", "Cash Flow Statement"),
    ],
    "Business Studies": [
        ("Management", "Nature and Significance of Management"),
        ("Management", "Principles of Management"),
        ("Management", "Business Environment"),
        ("Functions", "Planning"),
        ("Functions", "Organising"),
        ("Functions", "Staffing"),
        ("Functions", "Directing"),
        ("Functions", "Controlling"),
        ("Finance & Marketing", "Financial Management"),
        ("Finance & Marketing", "Financial Markets"),
        ("Finance & Marketing", "Marketing Management"),
        ("Consumer Protection", "Consumer Protection"),
    ],
    "Economics": [
        ("Macroeconomics", "National Income and Related Aggregates"),
        ("Macroeconomics", "Money and Banking"),
        ("Macroeconomics", "Determination of Income and Employment"),
        ("Macroeconomics", "Government Budget and the Economy"),
        ("Macroeconomics", "Balance of Payments"),
        ("Indian Economy", "Indian Economy on the Eve of Independence"),
        ("Indian Economy", "Indian Economy 1950-1990 & Economic Reforms"),
        ("Indian Economy", "Human Capital Formation & Rural Development"),
        ("Indian Economy", "Employment & Sustainable Economic Development"),
        ("Indian Economy", "Development Experiences: India & Neighbours"),
    ],
    "History": [
        ("Ancient India", "Bricks, Beads and Bones (Harappan Civilisation)"),
        ("Ancient India", "Kings, Farmers and Towns"),
        ("Ancient India", "Kinship, Caste and Class"),
        ("Ancient India", "Thinkers, Beliefs and Buildings"),
        ("Medieval India", "Through the Eyes of Travellers"),
        ("Medieval India", "Bhakti-Sufi Traditions"),
        ("Medieval India", "An Imperial Capital: Vijayanagara"),
        ("Medieval India", "Peasants, Zamindars and the State"),
        ("Modern India", "Colonialism and the Countryside"),
        ("Modern India", "Rebels and the Raj (1857)"),
        ("Modern India", "Mahatma Gandhi and the Nationalist Movement"),
        ("Modern India", "Framing the Constitution"),
    ],
    "Political Science": [
        ("World Politics", "The End of Bipolarity"),
        ("World Politics", "Contemporary Centres of Power"),
        ("World Politics", "Contemporary South Asia"),
        ("World Politics", "International Organisations"),
        ("World Politics", "Security in the Contemporary World"),
        ("World Politics", "Environment and Natural Resources"),
        ("World Politics", "Globalisation"),
        ("Indian Politics", "Challenges of Nation-Building"),
        ("Indian Politics", "Era of One-Party Dominance & Planned Development"),
        ("Indian Politics", "India's External Relations"),
        ("Indian Politics", "Challenges to and Restoration of Congress System"),
        ("Indian Politics", "The Crisis of Democratic Order & Regional Aspirations"),
    ],
    "Geography": [
        ("Human Geography", "Human Geography: Nature and Scope"),
        ("Human Geography", "The World Population: Distribution & Growth"),
        ("Human Geography", "Human Development"),
        ("Human Geography", "Primary, Secondary & Tertiary Activities"),
        ("Human Geography", "Transport, Communication & International Trade"),
        ("India People & Economy", "Population: Distribution, Density & Composition"),
        ("India People & Economy", "Human Settlements"),
        ("India People & Economy", "Land Resources, Agriculture & Water Resources"),
        ("India People & Economy", "Mineral and Energy Resources"),
        ("India People & Economy", "Planning, Transport & Geographical Perspective"),
    ],
    "English": [
        ("Flamingo Prose", "The Last Lesson & Lost Spring"),
        ("Flamingo Prose", "Deep Water & The Rattrap"),
        ("Flamingo Prose", "Indigo & Poets and Pancakes"),
        ("Flamingo Prose", "The Interview & Going Places"),
        ("Flamingo Poetry", "My Mother at Sixty-Six & Keeping Quiet"),
        ("Flamingo Poetry", "A Thing of Beauty, Roadside Stand & Aunt Jennifer"),
        ("Vistas", "The Third Level, The Tiger King & Journey to End of Earth"),
        ("Vistas", "The Enemy, On the Face of It & Memories of Childhood"),
        ("Writing & Reading", "Unseen Comprehension, Notices, Invitations & Letters"),
        ("Writing & Reading", "Article & Report Writing"),
    ],
    "Hindi": [
        ("Aroh Kavya", "Harivansh Rai Bachchan & Alok Dhanwa"),
        ("Aroh Kavya", "Kunwar Narayan & Raghuvir Sahay"),
        ("Aroh Kavya", "Shamsher Bahadur Singh & Suryakant Tripathi Nirala"),
        ("Aroh Kavya", "Tulsidas, Firaq Gorakhpuri & Umashankar Joshi"),
        ("Aroh Gadya", "Mahadevi Verma & Jainendra Kumar"),
        ("Aroh Gadya", "Dharamvir Bharati & Phanishwar Nath Renu"),
        ("Aroh Gadya", "Razia Sajjad Zaheer & Babasaheb Ambedkar"),
        ("Vitan", "Silver Wedding, Joojh & Ateet Mein Dabe Paon"),
        ("Abhivyakti", "Patrakarita, Feature, Alekh & Rachnatmak Lekhan"),
    ],
}

ACADEMIC_CHANNELS = [
    ("Physics", "physics"),
    ("Chemistry", "chemistry"),
    ("Mathematics", "mathematics"),
    ("Biology", "biology"),
    ("Accountancy", "accountancy"),
    ("Business Studies", "business-studies"),
    ("Economics", "economics"),
    ("English", "english"),
    ("Hindi", "hindi"),
    ("Doubt Solving", "doubt-solving"),
    ("Exam Preparation", "exam-preparation"),
    ("Backlog Help", "backlog-help"),
    ("Study Techniques", "study-techniques"),
    ("Motivation", "academic-motivation"),
]

NON_ACADEMIC_CHANNELS = [
    ("Mental Refreshment", "mental-refreshment"),
    ("Daily Life", "daily-life"),
    ("General Discussions", "general-discussions"),
    ("Music and Movies", "music-and-movies"),
    ("Hobbies", "hobbies"),
    ("Technology", "technology"),
    ("Personal Experiences", "personal-experiences"),
    ("Motivation and Support", "motivation-and-support"),
    ("Casual Conversations", "casual-conversations"),
]

SEED_QUOTES = [
    ("Discipline is choosing between what you want now and what you want most.", "Abraham Lincoln", "Discipline"),
    ("We are what we repeatedly do. Excellence, then, is not an act, but a habit.", "Will Durant", "Consistency"),
    ("Education is the most powerful weapon which you can use to change the world.", "Nelson Mandela", "Education"),
    ("Success is the sum of small efforts, repeated day in and day out.", "Robert Collier", "Consistency"),
    ("It does not matter how slowly you go as long as you do not stop.", "Confucius", "Resilience"),
    ("The secret of getting ahead is getting started.", "Mark Twain", "Discipline"),
    ("Hard work beats talent when talent doesn't work hard.", "Tim Notke", "Hard work"),
    ("Believe you can and you're halfway there.", "Theodore Roosevelt", "Resilience"),
    ("An investment in knowledge pays the best interest.", "Benjamin Franklin", "Education"),
    ("Small daily improvements over time lead to stunning results.", "Robin Sharma", "Personal growth"),
    ("Don't watch the clock; do what it does. Keep going.", "Sam Levenson", "Consistency"),
    ("The expert in anything was once a beginner.", "Helen Hayes", "Personal growth"),
    ("Self-belief and hard work will always earn you success.", "Virat Kohli", "Hard work"),
    ("Arise, awake, and stop not until the goal is reached.", "Swami Vivekananda", "Resilience"),
    ("You have to dream before your dreams can come true.", "A. P. J. Abdul Kalam", "Education"),
    ("Consistency compounds. One focused hour today builds tomorrow's confidence.", "STUDYVERSE Editorial", "Consistency"),
    ("Fall seven times, stand up eight.", "Japanese Proverb", "Resilience"),
    ("Focus on being productive instead of busy.", "Tim Ferriss", "Discipline"),
    ("Your future is created by what you do today, not tomorrow.", "Robert Kiyosaki", "Discipline"),
    ("Strive for progress, not perfection.", "David Perlmutter", "Personal growth"),
]


def init_db():
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    with get_db() as db:
        db.executescript(SCHEMA_SQL)

        # Seed exam timetables if empty
        if db.execute("SELECT COUNT(*) FROM exam_timetables").fetchone()[0] == 0:
            db.execute(
                """INSERT INTO exam_timetables (board, official_url, status, last_verified, exam_start_date, exam_end_date, notes)
                   VALUES (?, ?, 'published', '2026-11-24', '2027-02-16', '2027-04-09', 'Official CBSE Class 12 Date Sheet 2027 published 06 Nov 2026 (10:30 AM IST shift).')""",
                ("CBSE", "https://www.cbse.gov.in/cbsenew/documents/Datesheet_ClassX%20_XII_2027_Main_06112026.pdf"),
            )
            db.execute(
                """INSERT INTO exam_timetables (board, official_url, status, last_verified, exam_start_date, exam_end_date, notes)
                   VALUES (?, ?, 'published', '2026-11-24', '2027-02-17', '2027-03-19', 'Official MPBSE Class 12 Time Table 2027 published 13 Aug 2026 (09:00 AM - 12:00 PM IST).')""",
                ("MP Board", "https://mpbse.nic.in/"),
            )

        # Seed subjects & chapters if empty
        if db.execute("SELECT COUNT(*) FROM subjects").fetchone()[0] == 0:
            for board, date_map in [("CBSE", CBSE_2027_DATES), ("MP Board", MP_BOARD_2027_DATES)]:
                for stream, subj_list in STREAM_SUBJECTS.items():
                    for idx, sname in enumerate(subj_list, start=1):
                        code, ex_date, ex_time = date_map.get(sname, ("", None, ""))
                        cur = db.execute(
                            """INSERT INTO subjects (board, stream, name, code, exam_date, exam_time, display_order)
                               VALUES (?, ?, ?, ?, ?, ?, ?)""",
                            (board, stream, sname, code, ex_date, ex_time, idx),
                        )
                        sid = cur.lastrowid
                        for c_idx, (unit, ch_title) in enumerate(SYLLABUS_CHAPTERS.get(sname, []), start=1):
                            db.execute(
                                "INSERT INTO chapters (subject_id, chapter_number, title, unit_name) VALUES (?, ?, ?, ?)",
                                (sid, c_idx, ch_title, unit),
                            )

        # Seed chat channels if empty
        if db.execute("SELECT COUNT(*) FROM chat_channels").fetchone()[0] == 0:
            for idx, (name, slug) in enumerate(ACADEMIC_CHANNELS, start=1):
                db.execute(
                    "INSERT INTO chat_channels (category, name, slug, display_order) VALUES ('ACADEMIC', ?, ?, ?)",
                    (name, slug, idx),
                )
            for idx, (name, slug) in enumerate(NON_ACADEMIC_CHANNELS, start=1):
                db.execute(
                    "INSERT INTO chat_channels (category, name, slug, display_order) VALUES ('NON-ACADEMIC', ?, ?, ?)",
                    (name, slug, idx),
                )

        # Seed quotes if empty
        if db.execute("SELECT COUNT(*) FROM quotes").fetchone()[0] == 0:
            for text, author, cat in SEED_QUOTES:
                db.execute(
                    "INSERT INTO quotes (text, author, category) VALUES (?, ?, ?)",
                    (text, author, cat),
                )

        # Seed platform settings
        defaults = {
            "daily_target_minutes": "360",
            "report_time_ist": "23:00",
            "leaderboard_public_sunday_only": "1",
            "default_revision_intervals": "[3, 7, 15, 30]",
        }
        for k, v in defaults.items():
            db.execute("INSERT OR IGNORE INTO platform_settings (key, value) VALUES (?, ?)", (k, v))

        # Bootstrap Super Admins from environment variables (never hardcoded!)
        from app.auth import hash_password
        now_iso = datetime.now(IST).isoformat()
        for admin_username, admin_password in get_configured_admins():
            existing = db.execute("SELECT id, role FROM users WHERE username = ?", (admin_username,)).fetchone()
            pw_hash = hash_password(admin_password)
            if not existing:
                db.execute(
                    """INSERT INTO users (full_name, username, password_hash, role, onboarding_complete, created_at)
                       VALUES (?, ?, ?, 'admin', 1, ?)""",
                    (f"Super Admin ({admin_username})", admin_username, pw_hash, now_iso),
                )
            else:
                # Sync password & ensure role is admin from secure env config
                db.execute(
                    "UPDATE users SET password_hash = ?, role = 'admin', onboarding_complete = 1 WHERE id = ?",
                    (pw_hash, existing["id"]),
                )
